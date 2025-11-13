"""
Task selection and dynamic import.

Conventions:
- Config refers to tasks by string references like "package.module:func".
- For modules with a top-level callable named `run`, "package.module" is allowed
  and will resolve to "package.module:run".
- Task references can also be dictionaries, e.g.:
    {ref: "package.module:func", timeout: 20, id: "friendly-name"}
- Selectors return TaskDescriptor objects with resolved callables.
"""

import importlib
import importlib.metadata as ilmd
from dataclasses import dataclass
from typing import Any

from .config import load_config
from .log import warn
from .types import Action, TaskDescriptor, TaskFn


@dataclass(slots=True, frozen=True)
class _Resolved:
    id: str
    fn: TaskFn
    timeout: int | None
    params: dict[str, Any] | None = None


def _resolve_callable_from_ref(ref: str) -> tuple[str, TaskFn]:
    """
    Resolve a string like "package.module:func" to a callable.

    If ":func" is omitted, tries "run".
    """
    module_name, sep, attr = ref.partition(":")
    # First try module import resolution
    try:
        mod = importlib.import_module(module_name)
        if sep:
            target = getattr(mod, attr)
            if not callable(target):
                raise TypeError(f"Task target is not callable: {ref}")
            return ref, target
        if hasattr(mod, "run") and callable(getattr(mod, "run")):
            return f"{module_name}:run", getattr(mod, "run")
        if callable(mod):
            return module_name, mod  # type: ignore[return-value]
        raise AttributeError(f"No callable 'run' found in module and module is not callable: {module_name}")
    except Exception:
        # Fallback: entry point resolution by name (xenohooks.tasks)
        try:
            eps = ilmd.entry_points()
            # Python 3.10+: .select is available
            try:
                matches = [ep for ep in eps.select(group="xenohooks.tasks") if ep.name == ref]
            except Exception:
                matches = [ep for ep in eps.get("xenohooks.tasks", []) if ep.name == ref]  # type: ignore[attr-defined]
            if not matches:
                raise
            ep = matches[0]
            fn = ep.load()
            if not callable(fn):
                raise TypeError(f"Entry point target is not callable: {ref}")
            return f"ep:{ref}", fn  # id prefix indicates entry point
        except Exception:
            # Re-raise original import error
            raise


def _wrap_missing_task(ref: str, error_text: str) -> TaskFn:
    """Return a placeholder task that reports a non-blocking warning Action.

    This avoids hard failures on import/resolve errors while surfacing context
    to the router for inclusion in logs or user-visible messages if desired.
    """

    def _runner(ctx) -> Action:
        return Action(block=False, reason=f"Task not available: {ref} ({error_text})", task_id=ref)

    return _runner


def _parse_task_entry(entry: Any) -> _Resolved | None:
    """
    Accepts string or dict entries and resolves them to callables.
    On import/resolve failure, returns a warning-producing placeholder task.
    """
    ref: str | None = None
    timeout: int | None = None
    friendly_id: str | None = None
    params: dict[str, Any] | None = None

    if isinstance(entry, str):
        ref = entry
    elif isinstance(entry, dict):
        # Keys: "ref", "timeout", "id"
        vref = entry.get("ref")
        if isinstance(vref, str):
            ref = vref
        vto = entry.get("timeout")
        if isinstance(vto, int) and vto > 0:
            timeout = vto
        vid = entry.get("id")
        if isinstance(vid, str) and vid.strip():
            friendly_id = vid
        vparams = entry.get("params") or entry.get("args") or entry.get("config")
        if isinstance(vparams, dict):
            params = vparams
    else:
        return None

    if not ref:
        return None

    try:
        resolved_id, fn = _resolve_callable_from_ref(ref)
        return _Resolved(id=friendly_id or resolved_id, fn=fn, timeout=timeout, params=params)
    except Exception as e:
        warn("task_import_failed", ref=ref, err=str(e))
        return _Resolved(id=friendly_id or ref, fn=_wrap_missing_task(ref, str(e)), timeout=timeout)


def _dedupe_descriptors(items: list[_Resolved]) -> list[TaskDescriptor]:
    seen: set[str] = set()
    out: list[TaskDescriptor] = []
    for r in items:
        if r.id in seen:
            continue
        seen.add(r.id)
        out.append(TaskDescriptor(id=r.id, fn=r.fn, timeout_seconds=r.timeout, params=r.params))
    return out


def _read(cfg: dict[str, Any], *keys: str) -> Any:
    cur: Any = cfg
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur

def _entries_for_section(section: Any) -> list[Any]:
    if isinstance(section, dict):
        tasks = section.get("tasks")
        return tasks if isinstance(tasks, list) else []
    if isinstance(section, list):
        return section
    return []


def select_tasks(
    event: str,
    cfg: dict[str, Any] | None = None,
    tool_name: str = "",
    files: list[str] | None = None,
) -> list[TaskDescriptor]:
    cfg = cfg or load_config()
    section = _read(cfg, event)
    entries = _entries_for_section(section)
    if not entries:
        return []

    items: list[_Resolved] = []
    files = files or []

    for task_entry in entries:
        if not isinstance(task_entry, dict):
            # allow string shorthand
            r = _parse_task_entry(task_entry)
            if r:
                items.append(r)
            continue

        # tools filter (applies only if both config provides tools list and payload has tool_name)
        tools = task_entry.get("tools", [])
        if isinstance(tools, list) and tools:
            if not tool_name:
                # No tool_name in payload → condition cannot match
                continue
            tool_names_lower = [str(t).lower() for t in tools]
            if tool_name.lower() not in tool_names_lower:
                continue

        # file_types filter (applies only if config provides file_types and payload supplies files)
        file_types = task_entry.get("file_types", [])
        if isinstance(file_types, list) and file_types:
            if not files:
                continue
            # Pure file extension filtering (case-insensitive)
            exts = [str(ft).lower().lstrip('.') for ft in file_types]
            def _ext(p: str) -> str:
                p = p.lower()
                return p.rsplit('.', 1)[-1] if '.' in p else ''
            if not any(_ext(fp) in exts for fp in files):
                continue

        r = _parse_task_entry(task_entry)
        if r:
            items.append(r)

    return _dedupe_descriptors(items)
