"""
Background task runner for spawning and monitoring tasks.

Spawns tasks as background processes and updates queue with results.
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from xenohooks.common.task_queue import (
    BackgroundTask,
    get_pending_tasks,
    get_running_tasks,
    update_task_status,
    mark_task_consumed,
    _session_dir,
)


def spawn_tasks(session_id: str = "", max_concurrent: int = 2, cwd: str | None = None) -> list[str]:
    """
    Spawn pending tasks up to max_concurrent limit.

    Returns list of task IDs that were spawned.
    """
    # Check how many slots are available
    running = get_running_tasks(session_id)
    available_slots = max(0, max_concurrent - len(running))

    if available_slots == 0:
        return []

    # Get pending tasks
    pending = get_pending_tasks(session_id, limit=available_slots)

    if not pending:
        return []

    spawned_ids = []

    for task in pending:
        if not task.command:
            # Mark as failed - no command to run
            update_task_status(
                task.task_id,
                session_id=session_id,
                status="failed",
                completed_at=time.time(),
                error="No command specified"
            )
            continue

        try:
            # Create output directory for this task
            output_dir = _session_dir(session_id) / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            stdout_file = output_dir / f"{task.task_id}.stdout"
            stderr_file = output_dir / f"{task.task_id}.stderr"

            # Spawn as background process (detached)
            with open(stdout_file, "w") as out_f, open(stderr_file, "w") as err_f:
                proc = subprocess.Popen(
                    task.command,
                    stdout=out_f,
                    stderr=err_f,
                    stdin=subprocess.DEVNULL,
                    cwd=cwd,
                    start_new_session=True,  # Detach from parent
                )

            # Mark as running and store PID in metadata
            metadata = task.metadata or {}
            metadata["pid"] = proc.pid
            metadata["stdout_file"] = str(stdout_file)
            metadata["stderr_file"] = str(stderr_file)

            update_task_status(
                task.task_id,
                session_id=session_id,
                status="running",
                started_at=time.time(),
                metadata=metadata
            )

            spawned_ids.append(task.task_id)

        except Exception as e:
            # Failed to spawn
            update_task_status(
                task.task_id,
                session_id=session_id,
                status="failed",
                completed_at=time.time(),
                error=str(e)
            )

    return spawned_ids


def check_running_tasks(session_id: str = "") -> list[str]:
    """
    Check status of running tasks and update queue.

    Returns list of task IDs that completed.
    """
    running = get_running_tasks(session_id)
    completed_ids = []

    current_time = time.time()

    for task in running:
        # Check if task timed out
        if task.started_at and (current_time - task.started_at) > task.timeout:
            # Read partial output if available
            stdout_content = ""
            stderr_content = ""
            if task.metadata:
                stdout_file = task.metadata.get("stdout_file")
                stderr_file = task.metadata.get("stderr_file")
                if stdout_file:
                    try:
                        stdout_content = Path(stdout_file).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                if stderr_file:
                    try:
                        stderr_content = Path(stderr_file).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

            update_task_status(
                task.task_id,
                session_id=session_id,
                status="failed",
                completed_at=current_time,
                error=f"Task timed out after {task.timeout}s",
                stdout=stdout_content,
                stderr=stderr_content
            )
            completed_ids.append(task.task_id)
            continue

        # Check if process is still running
        if task.metadata and "pid" in task.metadata:
            pid = task.metadata.get("pid")
            try:
                # Check if process exists (doesn't kill it)
                os.kill(pid, 0)
                # Process still running, continue
            except (OSError, TypeError):
                # Process completed or doesn't exist
                # Read output files
                stdout_content = ""
                stderr_content = ""
                exit_code = None

                stdout_file = task.metadata.get("stdout_file")
                stderr_file = task.metadata.get("stderr_file")

                if stdout_file:
                    try:
                        stdout_content = Path(stdout_file).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

                if stderr_file:
                    try:
                        stderr_content = Path(stderr_file).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

                # Try to get exit code (best effort)
                # For detached processes we can't reliably get this, so assume 0 if no stderr
                exit_code = 0 if not stderr_content else 1

                update_task_status(
                    task.task_id,
                    session_id=session_id,
                    status="completed" if exit_code == 0 else "failed",
                    completed_at=current_time,
                    exit_code=exit_code,
                    stdout=stdout_content,
                    stderr=stderr_content
                )
                completed_ids.append(task.task_id)

    return completed_ids


def process_completed_tasks(session_id: str = "") -> list[dict[str, Any]]:
    """
    Process completed tasks and convert to feedback format.

    Returns list of feedback items ready to be recorded.
    """
    from xenohooks.common.task_queue import get_completed_tasks

    completed = get_completed_tasks(session_id, limit=10)
    feedback_items = []

    for task in completed:
        # Parse task output into feedback
        feedback = _parse_task_output(task)

        if feedback:
            feedback_items.append(feedback)

        # Mark task as consumed
        mark_task_consumed(task.task_id, session_id)

    return feedback_items


def _parse_task_output(task: BackgroundTask) -> dict[str, Any] | None:
    """
    Parse task output into feedback format.

    Expects JSON output in stdout with format:
    {
      "feedback": [
        {
          "content": "message",
          "severity": "warn",
          "category": "security"
        }
      ]
    }

    Falls back to showing stdout/stderr as-is if not JSON.
    """
    import json

    # Try to parse as JSON
    if task.stdout:
        try:
            data = json.loads(task.stdout)
            if isinstance(data, dict) and "feedback" in data:
                # Structured feedback
                feedback_list = data["feedback"]
                if isinstance(feedback_list, list) and feedback_list:
                    # Use first feedback item (or combine multiple)
                    first = feedback_list[0]
                    return {
                        "content": first.get("content", ""),
                        "severity": first.get("severity", "info"),
                        "category": first.get("category", "background-task"),
                        "task_id": task.source,
                    }
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    # Fallback: Show raw output
    if task.exit_code == 0 and task.stdout:
        # Success with output
        return {
            "content": f"Background task completed:\n{task.stdout[:500]}",
            "severity": "info",
            "category": "background-task",
            "task_id": task.source,
        }
    elif task.exit_code != 0:
        # Failed
        error_msg = task.error or task.stderr or "Unknown error"
        return {
            "content": f"Background task failed: {error_msg[:500]}",
            "severity": "warn",
            "category": "background-task",
            "task_id": task.source,
        }

    return None