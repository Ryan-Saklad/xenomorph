"""
Python security analysis (Bandit).

Runs Bandit on changed Python files. Non-blocking by default; sets
severity/category for policy-based blocking:
- Security findings → severity="error", category="security"
- Tool/timeouts and other issues → severity="warn"
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3
MAX_WARNINGS_TO_SHOW = 5
MAX_ISSUES_PER_FILE = 3  # Limit security issues shown per file


def _bandit_base_cmd() -> list[str] | None:
    if find_exe("bandit"):
        return ["bandit"]
    # Fallbacks via Python launcher
    if find_exe("python"):
        return ["python", "-m", "bandit"]
    if find_exe("python3"):
        return ["python3", "-m", "bandit"]
    if find_exe("py"):
        return ["py", "-m", "bandit"]
    return None


def _run_bandit_check(file_path: str) -> tuple[str | None, str]:
    base = _bandit_base_cmd()
    if base is None:
        return "Bandit not installed - install with 'pip install bandit'", "warning"

    result = run_command([*base, "-f", "txt", file_path], timeout_seconds=30)

    if result.timed_out:
        return "Bandit check timed out", "warning"

    if result.code == 127:
        return "Bandit not installed - install with 'pip install bandit'", "warning"

    out = (result.out or "").strip()
    err = (result.err or "").strip()

    # Bandit returns non-zero on issues; prints to stdout
    if result.code != 0 and out:
        return out, "error"
    if err and "INFO" not in err:
        return f"Bandit stderr: {err}", "warning"
    return None, "success"


def _format_bandit_output(text: str, max_issues: int = MAX_ISSUES_PER_FILE) -> tuple[str, int]:
    """Format bandit output with issue limit.

    Returns: (formatted_output, total_issue_count)
    """
    lines = text.strip().splitlines()
    formatted: list[str] = []
    in_issue = False
    issue_count = 0

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if ">> Issue:" in s:
            issue_count += 1
            if issue_count <= max_issues:
                in_issue = True
                formatted.append(f"  {s}")
            continue
        if in_issue and issue_count <= max_issues:
            if s.startswith("Code scanned:") or s.startswith("Total lines"):
                in_issue = False
                continue
            if not s.startswith("Test results:"):
                formatted.append(f"  {s}")

    result = "\n".join(formatted) if formatted else text.strip()
    if issue_count > max_issues:
        result += f"\n  … and {issue_count - max_issues} more issue(s)"

    return result, issue_count


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    # Filter to Python files that exist and skip common junk dirs
    skip_substrings = [
        "node_modules/",
        ".git/",
        "__pycache__/",
        ".mypy_cache/",
        ".pytest_cache/",
        ".venv/",
        "venv/",
    ]
    def _skip(p: str) -> bool:
        ps = p.replace("\\", "/")
        return any(s in ps for s in skip_substrings)

    py_files = [f for f in files if Path(f).suffix.lower() == ".py" and Path(f).exists() and not _skip(f)]
    if not py_files:
        return Action()

    issues: list[tuple[str, str, int]] = []  # (file_name, formatted_output, issue_count)
    warnings: list[str] = []

    for fp in py_files:
        msg, status = _run_bandit_check(fp)
        name = Path(fp).name
        if status == "error" and msg:
            formatted, count = _format_bandit_output(msg)
            issues.append((name, formatted, count))
        elif status == "warning" and msg:
            warnings.append(f"{name}: {msg}")

    if issues:
        total_issues = sum(count for _, _, count in issues)
        lines: list[str] = [f"🔒 **Bandit:** {total_issues} security issue(s) in {len(issues)} file(s)"]
        lines.append("")
        for name, formatted, _ in issues[:MAX_FILES_TO_SHOW]:
            lines.append(f"{name}:\n{formatted}")
        if len(issues) > MAX_FILES_TO_SHOW:
            lines.append(f"\n… and {len(issues) - MAX_FILES_TO_SHOW} more file(s)")
        return Action(add_context="\n".join(lines), severity="error", category="security")

    if warnings:
        ctx = f"⚠️ **Bandit warnings:** {len(warnings)} issue(s)\n" + "\n".join(f"   • {w}" for w in warnings[:MAX_WARNINGS_TO_SHOW])
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            ctx += f"\n   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more"
        return Action(add_context=ctx, severity="warn", category="security")

    return Action()
