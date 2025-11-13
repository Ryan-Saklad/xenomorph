"""
Frontend Biome linting integration (ported to dict payload API).

Runs Biome linting on JS/TS/JSON files that changed.
Produces additional context (never blocks).
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3
MAX_WARNINGS_TO_SHOW = 3

SUPPORTED_EXTS = {".js", ".mjs", ".json", ".ts", ".tsx"}


def _biome_base_cmd() -> list[str] | None:
    """Return the preferred command prefix for Biome, or None if unavailable.

    Prefer a direct `biome` binary if present, else fall back to `npx biome`.
    """
    if find_exe("biome"):
        return ["biome"]
    if find_exe("npx"):
        return ["npx", "biome"]
    return None


def _run_biome_check(file_path: str) -> tuple[str | None, str]:
    """Run `biome check` on a single file and classify the result.

    Returns (message, status) where status is one of: "success", "warning", "error".
    """
    base = _biome_base_cmd()
    if base is None:
        return "Biome not available (missing 'biome' and 'npx').", "warning"

    r = run_command([*base, "check", file_path], timeout_seconds=60)
    if r.timed_out:
        return "Biome check timed out", "warning"

    # Biome typically uses non-zero exit on issues and prints to stdout
    out = (r.out or "").strip()
    err = (r.err or "").strip()

    if r.code != 0 and out:
        return out, "error"
    if err:
        return f"Biome stderr: {err}", "warning"
    return None, "success"


def _format_biome_output(biome_output: str) -> str:
    """Format Biome output for readability by trimming separators/empties."""
    lines = biome_output.strip().splitlines()
    formatted: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("--") or s.startswith("=="):
            continue
        formatted.append(s)
    return "\n".join(formatted) if formatted else biome_output.strip()


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    biome_files = [f for f in files if Path(f).suffix.lower() in SUPPORTED_EXTS and Path(f).exists()]
    if not biome_files:
        return Action()

    issues_by_file: list[tuple[str, str]] = []  # (file, formatted_output)
    warnings: list[str] = []

    for fp in biome_files:
        msg, status = _run_biome_check(fp)
        if status == "error" and msg:
            issues_by_file.append((fp, _format_biome_output(msg)))
        elif status == "warning" and msg:
            warnings.append(f"{Path(fp).name}: {msg}")

    if not issues_by_file and not warnings:
        return Action()

    lines: list[str] = []

    if issues_by_file:
        lines.append(f"❌ **Biome:** {len(issues_by_file)} file(s) with issues")
        # Summarize up to a few files to keep concise
        for fp, formatted in issues_by_file[:MAX_FILES_TO_SHOW]:
            count = len([ln for ln in formatted.splitlines() if ln.strip()])
            lines.append(f"   • {Path(fp).name}: ~{count} issue(s)")
        if len(issues_by_file) > MAX_FILES_TO_SHOW:
            lines.append(f"   … and {len(issues_by_file) - MAX_FILES_TO_SHOW} more file(s)")
        lines.append("   💡 Run `npx biome check <file>` for full details")

    if warnings:
        if lines:
            lines.append("")
        lines.append(f"⚠️ **Biome warnings:** {len(warnings)} issue(s)")
        for w in warnings[:MAX_WARNINGS_TO_SHOW]:
            lines.append(f"   • {w}")
        if len(warnings) > MAX_WARNINGS_TO_SHOW:
            lines.append(f"   … and {len(warnings) - MAX_WARNINGS_TO_SHOW} more warning(s)")

    return Action(add_context="\n".join(lines), severity="error" if issues_by_file else "warn", category="lint")
