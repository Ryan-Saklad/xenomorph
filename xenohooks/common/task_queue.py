"""
Background task queue using SQLite.

Supports various task types and external registration via JSON drop-in.
"""

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "completed", "failed"]
TaskType = Literal["command", "hook", "external"]  # Extensible for future types


@dataclass
class BackgroundTask:
    """Represents a background task."""
    task_id: str
    task_type: TaskType
    status: TaskStatus

    # Task definition (JSON-serialized)
    command: list[str] | None = None
    metadata: dict[str, Any] | None = None

    # Source tracking
    source: str = "unknown"
    session_id: str = ""

    # Timing
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    timeout: int = 120

    # Results
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None


def _cache_dir() -> Path:
    """Get or create the tasks cache directory."""
    cache = Path.home() / ".cache" / "xenohooks" / "tasks"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _session_dir(session_id: str) -> Path:
    """Get or create session-specific directory."""
    if not session_id:
        session_id = "default"
    session_dir = _cache_dir() / session_id[:16]
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _db_path(session_id: str) -> Path:
    """Get the database path for a session."""
    return _session_dir(session_id) / "queue.db"


def _incoming_dir(session_id: str) -> Path:
    """Get the incoming directory for external task drop-ins."""
    incoming = _session_dir(session_id) / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    return incoming


def _get_connection(session_id: str) -> sqlite3.Connection:
    """Get or create database connection with schema initialization."""
    db_path = _db_path(session_id)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            command TEXT,
            metadata TEXT,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            timeout INTEGER NOT NULL DEFAULT 120,
            exit_code INTEGER,
            stdout TEXT,
            stderr TEXT,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at);
        CREATE INDEX IF NOT EXISTS idx_source ON tasks(source);
    """)

    conn.commit()
    return conn


def queue_task(
    command: list[str],
    session_id: str = "",
    source: str = "unknown",
    task_type: TaskType = "command",
    timeout: int = 120,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Queue a background task.

    Returns the task_id.
    """
    task_id = str(uuid.uuid4())
    current_time = time.time()

    conn = _get_connection(session_id)

    conn.execute("""
        INSERT INTO tasks (
            task_id, task_type, status, command, metadata,
            source, session_id, created_at, timeout
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id,
        task_type,
        "pending",
        json.dumps(command),
        json.dumps(metadata or {}),
        source,
        session_id,
        current_time,
        timeout
    ))

    conn.commit()
    conn.close()

    return task_id


def get_pending_tasks(session_id: str = "", limit: int = 10) -> list[BackgroundTask]:
    """Get pending tasks (oldest first)."""
    conn = _get_connection(session_id)

    rows = conn.execute("""
        SELECT * FROM tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()

    return [_row_to_task(row) for row in rows]


def get_running_tasks(session_id: str = "") -> list[BackgroundTask]:
    """Get currently running tasks."""
    conn = _get_connection(session_id)

    rows = conn.execute("""
        SELECT * FROM tasks
        WHERE status = 'running'
        ORDER BY started_at ASC
    """).fetchall()

    conn.close()

    return [_row_to_task(row) for row in rows]


def get_completed_tasks(session_id: str = "", limit: int = 50) -> list[BackgroundTask]:
    """Get recently completed tasks."""
    conn = _get_connection(session_id)

    rows = conn.execute("""
        SELECT * FROM tasks
        WHERE status IN ('completed', 'failed')
        ORDER BY completed_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()

    return [_row_to_task(row) for row in rows]


def update_task_status(
    task_id: str,
    session_id: str = "",
    status: TaskStatus | None = None,
    started_at: float | None = None,
    completed_at: float | None = None,
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Update task status and results."""
    conn = _get_connection(session_id)

    updates = []
    values = []

    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if started_at is not None:
        updates.append("started_at = ?")
        values.append(started_at)
    if completed_at is not None:
        updates.append("completed_at = ?")
        values.append(completed_at)
    if exit_code is not None:
        updates.append("exit_code = ?")
        values.append(exit_code)
    if stdout is not None:
        updates.append("stdout = ?")
        values.append(stdout)
    if stderr is not None:
        updates.append("stderr = ?")
        values.append(stderr)
    if error is not None:
        updates.append("error = ?")
        values.append(error)
    if metadata is not None:
        updates.append("metadata = ?")
        values.append(json.dumps(metadata))

    if updates:
        query = f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?"
        values.append(task_id)
        conn.execute(query, values)
        conn.commit()

    conn.close()


def mark_task_consumed(task_id: str, session_id: str = "") -> None:
    """Remove a completed task after its results have been consumed."""
    conn = _get_connection(session_id)
    conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def import_from_incoming(session_id: str = "") -> list[str]:
    """
    Import task definitions from incoming/ directory.

    Returns list of task IDs that were imported.
    """
    incoming = _incoming_dir(session_id)
    imported_ids = []

    for json_file in incoming.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())

            # Validate required fields
            if not isinstance(data.get("command"), list):
                json_file.unlink()  # Remove invalid file
                continue

            # Queue the task
            task_id = queue_task(
                command=data["command"],
                session_id=session_id,
                source=data.get("source", "external-json"),
                task_type=data.get("task_type", "external"),
                timeout=data.get("timeout", 120),
                metadata=data.get("metadata"),
            )

            imported_ids.append(task_id)

            # Remove the file after successful import
            json_file.unlink()

        except Exception:
            # Leave invalid files for manual inspection
            pass

    return imported_ids


def cleanup_session(session_id: str = "") -> None:
    """Clean up all task data for a session."""
    session_dir = _session_dir(session_id)
    try:
        # Remove database
        db_path = _db_path(session_id)
        if db_path.exists():
            db_path.unlink()

        # Remove incoming directory
        incoming = _incoming_dir(session_id)
        if incoming.exists():
            for f in incoming.iterdir():
                f.unlink()
            incoming.rmdir()

        # Remove session directory if empty
        if session_dir.exists() and not any(session_dir.iterdir()):
            session_dir.rmdir()

    except Exception:
        pass


def _row_to_task(row: sqlite3.Row) -> BackgroundTask:
    """Convert database row to BackgroundTask."""
    return BackgroundTask(
        task_id=row["task_id"],
        task_type=row["task_type"],
        status=row["status"],
        command=json.loads(row["command"]) if row["command"] else None,
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        source=row["source"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        timeout=row["timeout"],
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        error=row["error"]
    )