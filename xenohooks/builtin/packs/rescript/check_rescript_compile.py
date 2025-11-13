"""
ReScript compilation checking (ported to dict payload API).

Verifies ReScript files compile without errors using project scripts.
Adds concise additional context on failures; otherwise silent.
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action


def _frontend_cwd(payload: dict[str, Any]) -> str:
    base = Path(str(payload.get("cwd") or "."))
    p = base / "frontend"
    return str(p) if p.exists() and p.is_dir() else str(base)


def _runner_cmd() -> list[str] | None:
    if find_exe("npm"):
        return ["npm", "run", "lint:res"]
    if find_exe("pnpm"):
        return ["pnpm", "run", "lint:res"]
    if find_exe("yarn"):
        return ["yarn", "lint:res"]
    return None


def _rescript_format_cmd() -> list[str] | None:
    """Resolve a rescript formatter command (non-building)."""
    if find_exe("rescript"):
        return ["rescript", "format", "-check"]
    if find_exe("npx"):
        return ["npx", "rescript", "format", "-check"]
    return None


def _summarize(text: str, max_lines: int = 400, head: int = 200, tail: int = 200) -> str:
    """Summarize long outputs by keeping head and tail lines.

    This preserves the most useful parts (intro + error tail) while avoiding
    overly long context. If the text is short, returns it as-is.
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - (head + tail)
    middle = ["...", f"... truncated ({omitted} lines omitted) ...", "..."]
    return "\n".join(lines[:head] + middle + lines[-tail:])


def _watcher_active(frontend_cwd: str) -> bool:
    """Best-effort detection of an active ReScript watcher to avoid lock churn."""
    # If ps is not available, return False and let the build attempt proceed
    ps = run_command("ps -A -o command", timeout_seconds=2)
    if ps.code != 0:
        return False
    body = (ps.out or "") + (ps.err or "")
    low = body.lower()
    # Look for rescript/bsb watcher processes
    return ("rescript" in low and " -w" in low) or ("bsb" in low and " -w" in low)


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    res_files = [f for f in files if Path(f).suffix.lower() in {".res", ".resi"}]
    if not res_files:
        return Action()

    cmd = _runner_cmd()
    if cmd is None:
        return Action(add_context="ReScript compile check skipped: npm/pnpm/yarn not available", severity="warn", category="rescript")

    cwd = _frontend_cwd(payload)
    lock = Path(cwd) / ".bsb.lock"
    # If a watcher is active or lock exists, avoid compiles; auto-format instead
    if lock.exists() or _watcher_active(cwd):
        # Find rescript formatter (without -check flag for actual formatting)
        fmt_base: list[str] | None = None
        if find_exe("rescript"):
            fmt_base = ["rescript", "format"]
        elif find_exe("npx"):
            fmt_base = ["npx", "rescript", "format"]

        if fmt_base is None:
            # Silently skip if formatter not available
            return Action()

        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        res_files = [f for f in files if Path(f).suffix.lower() in {".res", ".resi"} and Path(f).exists()]

        formatted: list[str] = []
        for fp in res_files:
            # Read original content
            try:
                original = Path(fp).read_text(encoding="utf-8")
            except Exception:
                continue

            # Run formatter (writes to file)
            r = run_command([*fmt_base, fp], cwd=cwd, timeout_seconds=10)

            if r.code == 0:
                # Check if content actually changed
                try:
                    new_content = Path(fp).read_text(encoding="utf-8")
                    if original != new_content:
                        formatted.append(Path(fp).name)
                except Exception:
                    pass

        # Only report if files were actually formatted
        if formatted:
            return Action(
                add_context=(
                    f"✨ **ReScript:** Auto-formatted {len(formatted)} file(s)\n" +
                    "\n".join(f"   • {name}" for name in formatted[:5])
                ),
                severity="info",
                category="rescript"
            )
        return Action()
    r = run_command(cmd, cwd=cwd, timeout_seconds=30)

    if r.code != 0:
        err = (r.err or "").strip()
        out = (r.out or "").strip()
        body = err if err else out
        if not body:
            body = out or err
        summary = _summarize(body, max_lines=160, head=80, tail=80)
        return Action(add_context=f"ReScript compilation failed:\n{summary}", severity="error", category="rescript")

    return Action()
