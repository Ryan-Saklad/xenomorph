"""
Python Ruff linter integration (ported to dict payload API).

Runs Ruff on changed Python files to check code quality and style.
Adds additional context with concise summaries; never blocks.
Also attempts a safe auto-fix for missing newline at EOF (W292).
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3
MAX_WARNINGS_TO_SHOW = 3
MAX_ERRORS_PER_FILE = 5  # Limit errors shown per file


def _ruff_base_cmd() -> list[str] | None:
    """Return a command vector to run ruff, or None if unavailable."""
    if find_exe("ruff"):
        return ["ruff"]
    # Fallback via Python launcher
    if find_exe("python"):
        return ["python", "-m", "ruff"]
    if find_exe("python3"):
        return ["python3", "-m", "ruff"]
    if find_exe("py"):
        return ["py", "-m", "ruff"]
    return None


def _run_ruff_autofix(file_path: str) -> tuple[str, int]:
    base = _ruff_base_cmd()
    if base is None:
        return "ruff not available", 127
    result = run_command([*base, "check", file_path, "--fix", "--select", "W292"], timeout_seconds=30)
    return result.err or "", int(result.code)


def _run_ruff_full_check(file_path: str) -> tuple[str, str, int]:
    base = _ruff_base_cmd()
    if base is None:
        return "", "ruff not available", 127
    result = run_command([*base, "check", file_path, "--output-format", "concise"], timeout_seconds=30)
    return result.out or "", result.err or "", int(result.code)


def _run_ruff_check(file_path: str) -> tuple[str | None, str]:
    """Run ruff check on a Python file with parallel autofix and check."""
    try:
        with ThreadPoolExecutor(max_workers=2) as tp:
            af = tp.submit(_run_ruff_autofix, file_path)
            cf = tp.submit(_run_ruff_full_check, file_path)
            autofix_stderr, autofix_code = af.result()
            check_stdout, check_stderr, check_code = cf.result()

        if check_code == 127:
            return "Ruff not available - install with 'pip install ruff'", "warning"

        # Ruff returns non-zero exit code when issues are found
        if check_code != 0 and check_stdout.strip():
            return check_stdout.strip(), "error"
        if check_stderr.strip():
            return f"Ruff stderr: {check_stderr.strip()}", "warning"
        return None, "success"
    except Exception as e:
        return f"Error running ruff: {str(e)}", "warning"


def _format_ruff_output(text: str, max_lines: int = MAX_ERRORS_PER_FILE) -> tuple[str, int]:
    """Format ruff output for better readability with line limit.

    Ruff concise format lines look like: file:line:column: code message
    We drop the file and keep just line:col and the message.

    Returns: (formatted_output, total_count)
    """
    lines = text.strip().splitlines()
    formatted: list[str] = []
    total_count = len(lines)

    for raw in lines[:max_lines]:
        s = raw.strip()
        if not s:
            continue
        parts = s.split(":", 3)
        if len(parts) >= 4:
            _, line_num, col_num, message = parts
            formatted.append(f"  Line {line_num}:{col_num}: {message.strip()}")
        else:
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
        msg, status = _run_ruff_check(fp)
        if status == "error" and msg:
            formatted, count = _format_ruff_output(msg)
            issues.append((fp, formatted, count))
        elif status == "warning" and msg:
            warnings.append(f"{fp}: {msg}")

    if not issues and not warnings:
        return Action()

    lines: list[str] = []
    if issues:
        total_errors = sum(count for _, _, count in issues)
        lines.append(f"❌ **Ruff:** {total_errors} error(s) in {len(issues)} file(s)")
        for fp, formatted, _ in issues[:MAX_FILES_TO_SHOW]:
            lines.append(f"   {fp}:\n{formatted}")
        if len(issues) > MAX_FILES_TO_SHOW:
            lines.append(f"   … and {len(issues) - MAX_FILES_TO_SHOW} more file(s)")
    if warnings:
        lines.append(f"⚠️ **Ruff warnings:** {len(warnings)} issue(s)")
        for w in warnings[:MAX_WARNINGS_TO_SHOW]:
            lines.append(f"   • {w}")
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            lines.append(f"   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more warning(s)")

    # Add policy metadata so block_on rules can apply
    if issues:
        return Action(add_context="\n".join(lines), severity="error", category="lint")
    if warnings:
        return Action(add_context="\n".join(lines), severity="warn", category="lint")
    return Action()
