"""
JSON and YAML/TOML syntax validation (ported to dict payload API).

Validates syntax for JSON, YAML, and TOML configuration files that changed.
Adds concise additional context on errors; never blocks.
"""

import json
from pathlib import Path
from typing import Any, Tuple

from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3


def _check_json_syntax(file_path: str) -> str | None:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            json.load(f)
        return None
    except json.JSONDecodeError as e:
        return f"JSON syntax error: {e.msg} at line {e.lineno}, column {e.colno}"
    except Exception as e:
        return f"Error reading JSON file: {str(e)}"


def _check_yaml_syntax(file_path: str) -> str | None:
    try:
        import yaml  # type: ignore
    except Exception:
        return "PyYAML not installed - skipping YAML validation"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
        return None
    except Exception as e:  # yaml.YAMLError subtype acceptable here
        # Attempt to extract line/column if available
        problem = getattr(e, "problem", None)
        mark = getattr(e, "problem_mark", None)
        if problem and mark is not None and hasattr(mark, "line") and hasattr(mark, "column"):
            return f"YAML syntax error: {problem} at line {mark.line + 1}, column {mark.column + 1}"
        return f"YAML syntax error: {str(e)}"


def _check_toml_syntax(file_path: str) -> str | None:
    try:
        import tomllib  # type: ignore[attr-defined]
    except Exception:
        try:
            import tomli as tomllib  # type: ignore
        except Exception:
            return "TOML parser not available - skipping TOML validation"
    try:
        with open(file_path, "rb") as f:
            tomllib.load(f)
        return None
    except Exception as e:
        return f"TOML syntax error: {str(e)}"


def _check_file_syntax(file_path: str) -> Tuple[str | None, str]:
    path = Path(file_path)
    extension = path.suffix.lower()
    name = path.name.lower()

    if extension == ".json" or name in {"package.json", "tsconfig.json"}:
        return _check_json_syntax(file_path), "JSON"
    if extension in {".yaml", ".yml"}:
        return _check_yaml_syntax(file_path), "YAML"
    if extension == ".toml":
        return _check_toml_syntax(file_path), "TOML"

    # Fallback content sniffing
    try:
        text = Path(file_path).read_text(encoding="utf-8").strip()
    except Exception:
        return None, "Unknown"
    if text.startswith("{") or text.startswith("["):
        return _check_json_syntax(file_path), "JSON"
    if "=" in text and "[" in text:
        return _check_toml_syntax(file_path), "TOML"
    return _check_yaml_syntax(file_path), "YAML"


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    # Only consider likely config files
    config_names = {"package.json", "tsconfig.json"}
    config_exts = {".json", ".yaml", ".yml", ".toml"}
    targets = [
        f for f in files
        if Path(f).exists() and (Path(f).suffix.lower() in config_exts or Path(f).name.lower() in config_names)
    ]
    if not targets:
        return Action()

    errors: list[str] = []
    for fp in targets:
        err, kind = _check_file_syntax(fp)
        if err:
            errors.append(f"{fp} ({kind}): {err}")

    if not errors:
        return Action()

    lines: list[str] = [f"❌ **Config syntax errors:** {len(errors)} file(s)"]
    for s in errors[:MAX_FILES_TO_SHOW]:
        lines.append(f"   • {s}")
    if len(errors) > MAX_FILES_TO_SHOW:
        lines.append(f"   … and {len(errors) - MAX_FILES_TO_SHOW} more file(s)")

    return Action(add_context="\n".join(lines), severity="error", category="syntax")

