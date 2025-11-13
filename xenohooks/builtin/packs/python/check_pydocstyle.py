"""
Python docstring validation (ported to dict payload API).

Runs pydocstyle on changed Python files and summarizes findings.
Adds additional context when issues or warnings are present; never blocks.
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


def _pydocstyle_base_cmd() -> list[str] | None:
    """Return a command vector to run pydocstyle, or None if unavailable."""
    if find_exe("pydocstyle"):
        return ["pydocstyle"]
    # Fallbacks via Python launcher
    if find_exe("python"):
        return ["python", "-m", "pydocstyle"]
    if find_exe("python3"):
        return ["python3", "-m", "pydocstyle"]
    if find_exe("py"):
        return ["py", "-m", "pydocstyle"]
    return None


def _run_pydocstyle_check(file_path: str) -> tuple[str | None, str]:
    base = _pydocstyle_base_cmd()
    if base is None:
        return "pydocstyle not available - install with 'pip install pydocstyle'", "warning"

    try:
        result = run_command([*base, file_path], timeout_seconds=30)
        out = (result.out or "").strip()
        err = (result.err or "").strip()

        if result.timed_out:
            return "pydocstyle check timed out", "warning"

        # pydocstyle returns non-zero exit code when issues are found
        if result.code != 0 and out:
            return out, "error"
        if err:
            return f"pydocstyle stderr: {err}", "warning"
        return None, "success"
    except Exception as e:
        return f"Error running pydocstyle: {str(e)}", "warning"


def _format_pydocstyle_output(text: str, max_lines: int = MAX_ERRORS_PER_FILE) -> tuple[str, int]:
    """Format pydocstyle output with line limit.

    Returns: (formatted_output, total_count)
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    total_count = len(lines)

    result = "\n".join(lines[:max_lines])
    if total_count > max_lines:
        result += f"\n  … and {total_count - max_lines} more issue(s)"

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
        msg, status = _run_pydocstyle_check(fp)
        if status == "error" and msg:
            formatted, count = _format_pydocstyle_output(msg)
            issues.append((fp, formatted, count))
        elif status == "warning" and msg:
            warnings.append(f"{fp}: {msg}")

    if not issues and not warnings:
        return Action()

    lines: list[str] = []
    if issues:
        total_errors = sum(count for _, _, count in issues)
        lines.append(f"❌ **Pydocstyle:** {total_errors} issue(s) in {len(issues)} file(s)")
        for fp, formatted, _ in issues[:MAX_FILES_TO_SHOW]:
            lines.append(f"   {fp}:\n{formatted}")
        if len(issues) > MAX_FILES_TO_SHOW:
            lines.append(f"   … and {len(issues) - MAX_FILES_TO_SHOW} more file(s)")
    if warnings:
        lines.append(f"⚠️ **Pydocstyle warnings:** {len(warnings)} issue(s)")
        for w in warnings[:MAX_WARNINGS_TO_SHOW]:
            lines.append(f"   • {w}")
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            lines.append(f"   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more warning(s)")

    if issues:
        return Action(add_context="\n".join(lines), severity="error", category="lint")
    if warnings:
        return Action(add_context="\n".join(lines), severity="warn", category="lint")
    return Action()
