"""
Python MyPy type checking integration (ported to dict payload API).

Runs MyPy type checking on changed Python files and summarizes findings.
Adds additional context (never blocks).
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3
MAX_WARNINGS_TO_SHOW = 3
MAX_ERRORS_PER_FILE = 5  # Limit errors shown per file


def _mypy_base_cmd() -> list[str] | None:
    """Return a command vector to run mypy, or None if unavailable."""
    if find_exe("mypy"):
        return ["mypy"]
    # Fallbacks via Python launcher
    if find_exe("python"):
        return ["python", "-m", "mypy"]
    if find_exe("python3"):
        return ["python3", "-m", "mypy"]
    if find_exe("py"):
        return ["py", "-m", "mypy"]
    return None


def _run_mypy_check(file_path: str) -> tuple[str | None, str]:
    base = _mypy_base_cmd()
    if base is None:
        return "MyPy not available - install with 'pip install mypy'", "warning"

    try:
        result = run_command(
            [*base, "--no-error-summary", "--show-column-numbers", file_path],
            timeout_seconds=60,
        )

        out = (result.out or "").strip()
        err = (result.err or "").strip()

        if result.timed_out:
            return "MyPy check timed out", "warning"

        # MyPy returns non-zero exit code when type errors are found
        if result.code != 0 and out:
            return out, "error"
        if err:
            return f"MyPy stderr: {err}", "warning"
        return None, "success"
    except Exception as e:
        return f"Error running mypy: {str(e)}", "warning"


def _format_mypy_output(text: str, max_lines: int = MAX_ERRORS_PER_FILE) -> tuple[str, int]:
    """Format MyPy output into concise, line-referenced messages with line limit.

    Returns: (formatted_output, total_count)
    """
    lines = text.strip().splitlines()
    formatted: list[str] = []
    total_count = len(lines)

    for raw in lines[:max_lines]:
        s = raw.strip()
        if not s:
            continue
        # Expected format: file:line:column: level: message
        if ":" in s and any(tok in s for tok in ["error:", "warning:", "note:"]):
            parts = s.split(":", 3)
            if len(parts) >= 4:
                _, line_num, col_num, message = parts
                formatted.append(f"  Line {line_num}:{col_num}: {message.strip()}")
                continue
        formatted.append(f"  {s}")

    result = "\n".join(formatted) if formatted else text.strip()
    if total_count > max_lines:
        result += f"\n  … and {total_count - max_lines} more error(s)"

    return result, total_count


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    py_files = [f for f in files if Path(f).suffix.lower() == ".py" and Path(f).exists()]
    if not py_files:
        return Action()

    issues: list[tuple[str, str, int]] = []  # (file_path, formatted_output, error_count)
    warnings: list[str] = []

    for fp in py_files:
        msg, status = _run_mypy_check(fp)
        if status == "error" and msg:
            formatted, count = _format_mypy_output(msg)
            issues.append((fp, formatted, count))
        elif status == "warning" and msg:
            warnings.append(f"{fp}: {msg}")

    if not issues and not warnings:
        return Action()

    lines: list[str] = []
    if issues:
        total_errors = sum(count for _, _, count in issues)
        lines.append(f"❌ **MyPy:** {total_errors} error(s) in {len(issues)} file(s)")
        for fp, formatted, _ in issues[:MAX_FILES_TO_SHOW]:
            lines.append(f"   {fp}:\n{formatted}")
        if len(issues) > MAX_FILES_TO_SHOW:
            lines.append(f"   … and {len(issues) - MAX_FILES_TO_SHOW} more file(s)")
    if warnings:
        lines.append(f"⚠️ **MyPy warnings:** {len(warnings)} issue(s)")
        for w in warnings[:MAX_WARNINGS_TO_SHOW]:
            lines.append(f"   • {w}")
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            lines.append(f"   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more warning(s)")

    if issues:
        return Action(add_context="\n".join(lines), severity="error", category="lint")
    if warnings:
        return Action(add_context="\n".join(lines), severity="warn", category="lint")
    return Action()
