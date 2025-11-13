"""
Feedback management system using SQLite for reliable storage and querying.

Provides deduplication, progressive disclosure, and foundation for future
features like background tasks and deferred feedback.
"""

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# Default cache duration in seconds (5 minutes)
DEFAULT_DEDUP_WINDOW = 300


Strategy = Literal["show_once", "always", "summary_after_first", "defer"]


@dataclass
class FeedbackItem:
    """Represents a piece of feedback from a hook."""
    # Core identification
    instance_id: str  # Content hash for exact deduplication
    issue_id: str  # Stable ID: {task_id}:{file}:{issue_type} for tracking

    # Content
    content: str

    # Metadata
    task_id: str
    severity: str | None = None
    category: str | None = None
    file_path: str | None = None

    # Presentation
    strategy: Strategy = "show_once"

    # Tracking
    first_seen: float = 0.0
    last_seen: float = 0.0
    occurrence_count: int = 1
    times_shown: int = 0


def _cache_dir() -> Path:
    """Get or create the feedback cache directory."""
    cache = Path.home() / ".cache" / "xenohooks" / "feedback"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _db_path(session_id: str) -> Path:
    """Get the database path for a session."""
    if not session_id:
        session_id = "default"
    return _cache_dir() / f"{session_id[:16]}.db"


def _get_connection(session_id: str) -> sqlite3.Connection:
    """Get or create database connection with schema initialization."""
    db_path = _db_path(session_id)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row  # Access columns by name

    # Create schema if not exists
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feedback_items (
            issue_id TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL,
            content TEXT NOT NULL,
            task_id TEXT NOT NULL,
            severity TEXT,
            category TEXT,
            file_path TEXT,
            strategy TEXT NOT NULL DEFAULT 'show_once',
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            times_shown INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_last_seen ON feedback_items(last_seen);
        CREATE INDEX IF NOT EXISTS idx_task_category ON feedback_items(task_id, category);
        CREATE INDEX IF NOT EXISTS idx_strategy ON feedback_items(strategy);
    """)

    conn.commit()
    return conn


def _content_hash(content: str) -> str:
    """Generate stable hash from content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _issue_id(task_id: str, file_path: str | None, content: str) -> str:
    """
    Generate stable issue ID for tracking the same issue over time.

    Format: {task_id}:{file}:{content_prefix_hash}
    This allows tracking the same issue (e.g., "unused variable in app.py") across edits.
    """
    file_part = Path(file_path).name if file_path else "global"
    content_prefix = content[:100]  # First 100 chars for issue type
    issue_hash = hashlib.sha256(content_prefix.encode("utf-8")).hexdigest()[:8]
    return f"{task_id}:{file_part}:{issue_hash}"


def _cleanup_expired(conn: sqlite3.Connection, current_time: float, window: int = DEFAULT_DEDUP_WINDOW) -> None:
    """Remove expired feedback items."""
    cutoff = current_time - window
    conn.execute("DELETE FROM feedback_items WHERE last_seen < ?", (cutoff,))
    conn.commit()


def record_feedback(
    content: str,
    task_id: str,
    session_id: str = "",
    file_path: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    strategy: Strategy = "show_once",
) -> FeedbackItem:
    """
    Record a feedback item and return it for decision making.

    This is the main entry point for hooks to report feedback.
    """
    if not content or not content.strip():
        # Empty content - always show
        return FeedbackItem(
            instance_id="empty",
            issue_id="empty",
            content="",
            task_id=task_id,
            strategy="always"
        )

    current_time = time.time()

    # Generate IDs
    instance_id = _content_hash(content.strip())
    issue_id_str = _issue_id(task_id, file_path, content.strip())

    # Get database connection
    conn = _get_connection(session_id)

    # Clean up expired items
    _cleanup_expired(conn, current_time)

    # Check if this issue exists
    row = conn.execute("SELECT * FROM feedback_items WHERE issue_id = ?", (issue_id_str,)).fetchone()

    if row:
        # Update existing item
        conn.execute("""
            UPDATE feedback_items
            SET instance_id = ?,
                content = ?,
                last_seen = ?,
                occurrence_count = occurrence_count + 1
            WHERE issue_id = ?
        """, (instance_id, content.strip(), current_time, issue_id_str))
        conn.commit()

        # Fetch updated row
        row = conn.execute("SELECT * FROM feedback_items WHERE issue_id = ?", (issue_id_str,)).fetchone()
    else:
        # Insert new item
        conn.execute("""
            INSERT INTO feedback_items (
                issue_id, instance_id, content, task_id,
                severity, category, file_path, strategy,
                first_seen, last_seen, occurrence_count, times_shown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            issue_id_str, instance_id, content.strip(), task_id,
            severity, category, file_path, strategy,
            current_time, current_time, 1, 0
        ))
        conn.commit()

        row = conn.execute("SELECT * FROM feedback_items WHERE issue_id = ?", (issue_id_str,)).fetchone()

    conn.close()

    # Convert row to FeedbackItem
    return FeedbackItem(
        instance_id=row["instance_id"],
        issue_id=row["issue_id"],
        content=row["content"],
        task_id=row["task_id"],
        severity=row["severity"],
        category=row["category"],
        file_path=row["file_path"],
        strategy=row["strategy"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        occurrence_count=row["occurrence_count"],
        times_shown=row["times_shown"]
    )


def should_show_feedback(item: FeedbackItem) -> bool:
    """
    Determine if feedback should be shown based on strategy and history.
    """
    match item.strategy:
        case "always":
            return True
        case "show_once":
            return item.times_shown == 0
        case "summary_after_first":
            # Show first time, then only summary
            return item.times_shown == 0
        case "defer":
            # Never show automatically
            return False
        case _:
            return True


def mark_shown(issue_id: str, session_id: str = "") -> None:
    """Mark a feedback item as having been shown."""
    conn = _get_connection(session_id)
    conn.execute("""
        UPDATE feedback_items
        SET times_shown = times_shown + 1
        WHERE issue_id = ?
    """, (issue_id,))
    conn.commit()
    conn.close()


def get_feedback_summary(session_id: str = "", min_occurrences: int = 2) -> list[FeedbackItem]:
    """
    Get feedback items that have occurred multiple times.

    Useful for progressive disclosure: "3 previous warnings still apply"
    """
    conn = _get_connection(session_id)
    current_time = time.time()
    _cleanup_expired(conn, current_time)

    rows = conn.execute("""
        SELECT * FROM feedback_items
        WHERE occurrence_count >= ?
        ORDER BY last_seen DESC
    """, (min_occurrences,)).fetchall()

    conn.close()

    return [
        FeedbackItem(
            instance_id=row["instance_id"],
            issue_id=row["issue_id"],
            content=row["content"],
            task_id=row["task_id"],
            severity=row["severity"],
            category=row["category"],
            file_path=row["file_path"],
            strategy=row["strategy"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            occurrence_count=row["occurrence_count"],
            times_shown=row["times_shown"]
        )
        for row in rows
    ]


def get_deferred_feedback(session_id: str = "") -> list[FeedbackItem]:
    """Get all deferred feedback items (for showing at appropriate times)."""
    conn = _get_connection(session_id)
    current_time = time.time()
    _cleanup_expired(conn, current_time)

    rows = conn.execute("""
        SELECT * FROM feedback_items
        WHERE strategy = 'defer'
        ORDER BY first_seen ASC
    """).fetchall()

    conn.close()

    return [
        FeedbackItem(
            instance_id=row["instance_id"],
            issue_id=row["issue_id"],
            content=row["content"],
            task_id=row["task_id"],
            severity=row["severity"],
            category=row["category"],
            file_path=row["file_path"],
            strategy=row["strategy"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            occurrence_count=row["occurrence_count"],
            times_shown=row["times_shown"]
        )
        for row in rows
    ]


def cleanup_session(session_id: str = "") -> None:
    """Clean up feedback for a session (call on SessionEnd)."""
    db_path = _db_path(session_id)
    try:
        if db_path.exists():
            db_path.unlink()
    except Exception:
        pass