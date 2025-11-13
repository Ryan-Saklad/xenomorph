"""
Parallel task runner with per-task timeouts and robust error handling.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from time import perf_counter
from typing import Iterable

from .log import warn, error
from .types import Action, TaskDescriptor, flatten_actions


def _safe_task_call(desc: TaskDescriptor, payload: dict) -> list[Action]:
    """
    Execute a single task, capturing exceptions into WARN results.
    """
    start = perf_counter()
    try:
        payload2 = dict(payload)
        payload2["task"] = {"id": desc.id, "params": desc.params}
        out = desc.fn(payload2)
        results = flatten_actions(out)
        elapsed = perf_counter() - start
        for a in results:
            if a.task_id is None:
                a.task_id = desc.id
            if a.elapsed_seconds is None:
                a.elapsed_seconds = elapsed
        return results
    except Exception as e:
        elapsed = perf_counter() - start
        warn("task_error", task_id=desc.id, err=str(e))
        return [Action(block=False, reason=str(e), task_id=desc.id, elapsed_seconds=elapsed)]


def run_parallel(
    tasks: Iterable[TaskDescriptor],
    payload: dict,
    max_workers: int | None = None,
) -> list[Action]:
    """
    Run tasks in parallel with per-task timeouts.

    - max_workers defaults to config["concurrency"] or len(tasks) if smaller.
    - Each task can define its own timeout; otherwise ctx.timeout_seconds is used.
    - Timeouts produce WARN results (never raise).
    """
    task_list = list(tasks)
    if not task_list:
        return []

    cfg_conc = int(payload.get("config", {}).get("concurrency", 6))
    pool_size = max_workers if isinstance(max_workers, int) and max_workers > 0 else max(1, min(cfg_conc, len(task_list)))

    results: list[Action] = []
    futures: list[tuple[TaskDescriptor, Future[list[Action]]]] = []

    with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="claude-hooks") as tp:
        for desc in task_list:
            futures.append((desc, tp.submit(_safe_task_call, desc, payload)))

        for desc, fut in futures:
            default_timeout = float(payload.get("timeout_seconds", 12))
            timeout = float(desc.timeout_seconds) if isinstance(desc.timeout_seconds, (int, float)) and float(desc.timeout_seconds) > 0 else default_timeout
            try:
                results.extend(fut.result(timeout=timeout))
            except Exception as e:
                # TimeoutError or other executor-level issue
                error("task_timeout_or_executor_error", task_id=desc.id, err=str(e))
                results.append(Action(block=False, reason=str(e), task_id=desc.id, elapsed_seconds=None))

    return results