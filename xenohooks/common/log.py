"""
Simple JSON logging utilities for hooks and tasks.

- Writes to file only (not stderr to avoid interfering with Claude Code).
- File location defaults to $CLAUDE_HOOKS_LOG_DIR or ./.claude/hooks/logs/.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# A very lightweight thread-safe JSON logger.
_lock = threading.Lock()
_log_dir_env = "CLAUDE_HOOKS_LOG_DIR"


def _log_dir() -> Path:
    root = os.environ.get(_log_dir_env)
    if root:
        p = Path(root)
    else:
        # Default to project-local .claude/hooks/logs directory
        p = Path.cwd() / ".claude" / "hooks" / "logs"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to a temp directory if we cannot create the directory
        return Path(tempfile.gettempdir()) / "hooks" / "logs"
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_json(level: str, message: str, **fields: Any) -> None:
    """
    Emit a single-line JSON log to file only.
    Never raises; failures are swallowed.
    """
    record = {
        "ts": _now_iso(),
        "level": level.lower(),
        "msg": message,
        **fields,
    }
    text = json.dumps(record, ensure_ascii=False)

    with _lock:
        # file only - don't write to stderr as Claude Code captures it
        try:
            logfile = _log_dir() / "hooks.log"
            with logfile.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass


def info(message: str, **fields: Any) -> None:
    log_json("info", message, **fields)


def warn(message: str, **fields: Any) -> None:
    log_json("warn", message, **fields)


def error(message: str, **fields: Any) -> None:
    log_json("error", message, **fields)
