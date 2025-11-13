"""
Configuration loader for Claude Hooks.

Search order (first found wins):
  1. Path in env var CLAUDE_HOOKS_CONFIG
  2. ./hooks.yml or ./hooks.yaml
  3. ./config/hooks.yml or ./config/hooks.yaml
  4. ./.claude/hooks/config/hooks.yml
  5. ./hooks.json
If nothing is found or parsing fails, returns a safe default config.
"""

import json
import os
import yaml
import importlib.resources as resources
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable



_DEFAULTS: dict[str, Any] = {
    "concurrency": 6,
    "default_timeout": 12,
    "timeouts": {},  # e.g., {"python.mypy": 10}
    "policy": {
        "missing_tool_is_warning": True,
        "treat_stderr_only_as_warning": True,
        # Block-on rules are opt-in; tokens can be:
        #   - "error" (by severity)
        #   - "security" (by category)
        #   - "security:error" (by category and severity)
        "block_on": [],
    },
    # Event sections use canonical event names (empty by default)
    "PreToolUse": {"tasks": []},
    "PostToolUse": {"tasks": []},
    "UserPromptSubmit": {"tasks": []},
    "Notification": {"tasks": []},
    "Stop": {"tasks": []},
    "SubagentStop": {"tasks": []},
    "PreCompact": {"tasks": []},
    "SessionStart": {"tasks": []},
    "SessionEnd": {"tasks": []},
}

# Known event section keys that may contain task lists
_EVENT_KEYS: set[str] = {
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
}


def _try_load_yaml_text(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _try_load_json_text(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _candidate_paths(cwd: Path) -> list[Path]:
    env_path = os.environ.get("CLAUDE_HOOKS_CONFIG")
    if env_path:
        return [Path(env_path)]

    return [
        cwd / "hooks.yml",
        cwd / "hooks.yaml",
        cwd / "config" / "hooks.yml",
        cwd / "config" / "hooks.yaml",
        cwd / ".claude" / "hooks" / "config" / "hooks.yml",
        cwd / "hooks.json",
    ]


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """
    Shallow-merge two dicts with simple deep merge for nested dicts.
    Values in b override a.
    """
    out: dict[str, Any] = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_event_section(section: Any) -> dict[str, Any]:
    if isinstance(section, list):
        return {"tasks": section}
    if isinstance(section, dict):
        tasks = section.get("tasks")
        if isinstance(tasks, list):
            return {**{k: v for k, v in section.items() if k != "tasks"}, "tasks": tasks}
    return {"tasks": []}


def _task_key(entry: Any) -> str:
    if isinstance(entry, dict):
        tid = entry.get("id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
        ref = entry.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
    elif isinstance(entry, str) and entry.strip():
        return entry.strip()
    return ""


def _merge_event_sections(a: Any, b: Any) -> dict[str, Any]:
    """Merge two event sections. Later entries override same-id tasks.

    Supports:
      - list or {tasks: [...]} shapes
      - task-level disabled: true
      - section-level remove: [id|ref, ...]
    """
    sa = _normalize_event_section(a)
    sb = _normalize_event_section(b)

    out: dict[str, Any] = {k: v for k, v in sa.items() if k != "tasks"}

    # Seed with A's tasks
    ordered: dict[str, Any] = {}
    for ent in sa.get("tasks", []) or []:
        k = _task_key(ent)
        if not k:
            continue
        ordered[k] = ent

    # Apply removes declared in B
    removes = sb.get("remove")
    if isinstance(removes, list):
        for rid in removes:
            if isinstance(rid, str) and rid in ordered:
                ordered.pop(rid, None)

    # Apply B's tasks
    for ent in sb.get("tasks", []) or []:
        k = _task_key(ent)
        if not k:
            continue
        if isinstance(ent, dict) and ent.get("disabled") is True:
            ordered.pop(k, None)
        else:
            ordered[k] = ent

    out["tasks"] = list(ordered.values())
    return out


def _merge_configs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in _EVENT_KEYS:
            out[k] = _merge_event_sections(out.get(k, {"tasks": []}), v)
        elif isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_example(name: str) -> dict[str, Any] | None:
    """Load a packaged example config by name (without extension)."""
    base = resources.files("xenohooks.examples")
    for ext in (".yml", ".yaml"):
        try:
            cfg_path = base / f"{name}{ext}"
            with cfg_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None

def _load_one(source: str, base_dir: Path | None) -> tuple[dict[str, Any] | None, Path | None]:
    """Load a single config source (example:name or file path). Returns (cfg, origin_path)."""
    source = source.strip()
    if source.startswith("example:"):
        name = source.split(":", 1)[1].strip()
        return _load_example(name), None
    p = Path(source)
    if not p.is_absolute() and base_dir is not None:
        p = (base_dir / p).resolve()
    try:
        if p.exists():
            if p.suffix.lower() in {".yaml", ".yml"}:
                return _try_load_yaml_text(p), p
            if p.suffix.lower() == ".json":
                return _try_load_json_text(p), p
            data = _try_load_yaml_text(p)
            if data is None:
                data = _try_load_json_text(p)
            return data, p
    except Exception:
        return None, p
    return None, p


def _resolve_extends(cfg: dict[str, Any], origin: Path | None, seen: set[str]) -> dict[str, Any]:
    items = cfg.get("extends")
    if not isinstance(items, list) or not items:
        return {k: v for k, v in cfg.items() if k != "extends"}

    base_dir = origin.parent if isinstance(origin, Path) else None
    merged: dict[str, Any] = {}
    for raw in items:
        if not isinstance(raw, str) or not raw.strip():
            continue
        token = raw.strip()
        # Cycle detection key
        key = token
        if not token.startswith("example:") and base_dir is not None:
            # Normalize file path key
            try:
                key = str(((base_dir / token).resolve()))
            except Exception:
                key = token
        if key in seen:
            continue
        seen.add(key)
        data, child_origin = _load_one(token, base_dir)
        if isinstance(data, dict):
            # Recurse for child's extends
            child_resolved = _resolve_extends(data, child_origin, seen)
            merged = _merge_configs(merged, child_resolved)
    # Finally, merge current cfg (sans extends) on top
    cur = {k: v for k, v in cfg.items() if k != "extends"}
    return _merge_configs(merged, cur)


@lru_cache(maxsize=8)
def _load_config_cached(key: tuple[str, ...] | None) -> dict[str, Any]:
    result = dict(_DEFAULTS)
    sources: list[str] = []
    if key:
        sources.extend(list(key))
    else:
        # Discovery
        cwd = Path(os.getcwd())
        for p in _candidate_paths(cwd):
            if p.exists():
                sources = [str(p)]
                break
    if not sources:
        # No discovered sources and no explicit path(s) provided
        raise RuntimeError(
            "No hooks config found. Provide --config or add hooks.yml in your project."
        )

    if not sources:
        return result

    seen: set[str] = set()
    for src in sources:
        data, origin = _load_one(src, None)
        if not isinstance(data, dict):
            continue
        resolved = _resolve_extends(data, origin, seen)
        result = _merge_configs(result, resolved)

    return result


def load_config(path: str | Iterable[str] | None = None) -> dict[str, Any]:
    if path is None:
        return _load_config_cached(None)
    if isinstance(path, str):
        # Support comma-separated list for CLI convenience
        parts = [p.strip() for p in path.split(",") if p.strip()]
        return _load_config_cached(tuple(parts))
    try:
        parts = tuple(str(p).strip() for p in path if str(p).strip())  # type: ignore[arg-type]
    except Exception:
        parts = tuple()
    return _load_config_cached(parts or None)
