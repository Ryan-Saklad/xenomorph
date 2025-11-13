"""
Frontend StyleLint integration.

Runs stylelint on CSS/SCSS/SASS files that changed.
- Non-blocking by default; sets severity/category for policy-based blocking
- Severity mapping: issues → error (category="lint"), tool/timeouts → warn
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_WARNINGS_TO_SHOW = 5

SUPPORTED_EXTS = {".css", ".scss", ".sass"}


def _stylelint_base_cmd() -> list[str] | None:
    """Return preferred command for stylelint, or None if unavailable.

    Prefer a direct `stylelint` binary; otherwise fall back to `npx stylelint`.
    """
    if find_exe("stylelint"):
        return ["stylelint"]
    if find_exe("npx"):
        return ["npx", "stylelint"]
    return None


def _run_stylelint_check(file_path: str) -> tuple[str | None, str]:
    """Run stylelint check on a file.

    Returns (message, status) where status is one of: success | warning | error
    """
    base = _stylelint_base_cmd()
    if base is None:
        return "Stylelint not available (missing 'stylelint' and 'npx').", "warning"

    result = run_command([*base, file_path], timeout_seconds=30)

    if result.timed_out:
        return "Stylelint check timed out", "warning"

    if result.code == 127:
        return "Stylelint not available - install with 'npm install stylelint'", "warning"

    out = (result.out or "").strip()
    err = (result.err or "").strip()

    # Stylelint typically returns non-zero on issues (printed to stdout)
    if result.code != 0 and out:
        return out, "error"
    if err:
        return f"Stylelint stderr: {err}", "warning"
    return None, "success"


def _format_stylelint_output(text: str) -> str:
    """Make stylelint output more scannable by trimming noise."""
    lines = text.strip().splitlines()
    formatted: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        # Skip absolute path headers or separators
        if s.startswith("/") or s.startswith("—") or s.startswith("--") or s.startswith("=="):
            continue
        if "✖" in s or "error" in s.lower() or "warning" in s.lower() or s.endswith("problems"):
            formatted.append(f"  {s}")
        else:
            formatted.append(f"  {s}")
    return "\n".join(formatted) if formatted else text.strip()


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    css_files = [f for f in files if Path(f).suffix.lower() in SUPPORTED_EXTS and Path(f).exists()]
    if not css_files:
        return Action()

    issues: list[str] = []
    warnings: list[str] = []

    for fp in css_files:
        msg, status = _run_stylelint_check(fp)
        name = Path(fp).name
        if status == "error" and msg:
            formatted = _format_stylelint_output(msg)
            issues.append(f"{name}:\n{formatted}")
        elif status == "warning" and msg:
            warnings.append(f"{name}: {msg}")

    if issues:
        ctx = (
            f"❌ **Stylelint:** {len(issues)} file(s) with CSS issues\n\n" +
            "\n\n".join(issues) +
            "\n\n💡 Run `npx stylelint --fix <file>` to auto-fix"
        )
        return Action(add_context=ctx, severity="error", category="lint")

    if warnings:
        ctx = f"⚠️ **Stylelint warnings:** {len(warnings)} issue(s)\n" + "\n".join(f"   • {w}" for w in warnings[:MAX_WARNINGS_TO_SHOW])
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            ctx += f"\n   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more"
        return Action(add_context=ctx, severity="warn", category="lint")

    return Action()
