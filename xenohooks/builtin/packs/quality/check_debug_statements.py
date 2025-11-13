"""
Debug statement detection (ported to dict payload API).

Detects common debug statements that shouldn't be committed.
Adds additional context with a concise summary (never blocks).
"""

import re
from pathlib import Path
from typing import Any

from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_ISSUES_TO_SHOW = 5


def _is_in_string_literal(line: str, pattern: str) -> bool:
    """Heuristic: if the match is inside quotes on the same line, skip it."""
    m = re.search(pattern, line, re.IGNORECASE)
    if not m:
        return False
    pos = m.start()
    before = line[:pos]
    single_quotes = before.count("'") - before.count("\\'")
    double_quotes = before.count('"') - before.count('\\"')
    return (single_quotes % 2 == 1) or (double_quotes % 2 == 1)


def _looks_like_legitimate_logging(line: str) -> bool:
    s = line.strip()
    if len(s) < 20:
        return False
    indicators = [
        "error",
        "warning",
        "info",
        "success",
        "failed",
        "starting",
        "finished",
        "processing",
        "loading",
        "saving",
        "connecting",
        "response",
        "request",
        "status",
        "result",
        "exception",
        "traceback",
    ]
    low = s.lower()
    return any(tok in low for tok in indicators)


def _patterns_for_ext(ext: str) -> list[tuple[str, str]]:
    if ext == ".py":
        return [
            (r"\bprint\s*\(", "print() statement"),
            (r"\bpdb\.set_trace\(\)", "pdb.set_trace() debugger"),
            (r"\bbreakpoint\(\)", "breakpoint() debugger"),
            (r"\bimport\s+pdb", "pdb import for debugging"),
            (r"\bfrom\s+pdb\s+import", "pdb import for debugging"),
            (r"#\s*TODO:\s*remove", "TODO: remove comment"),
            (r"#\s*FIXME:", "FIXME comment"),
            (r"#\s*DEBUG:", "DEBUG comment"),
        ]
    if ext in {".js", ".mjs", ".ts", ".tsx"}:
        return [
            (r"\bconsole\.log\s*\(", "console.log() statement"),
            (r"\bconsole\.debug\s*\(", "console.debug() statement"),
            (r"\bconsole\.warn\s*\(", "console.warn() statement"),
            (r"\bconsole\.error\s*\(", "console.error() statement"),
            (r"\bdebugger\s*;", "debugger statement"),
            (r"//\s*TODO:\s*remove", "TODO: remove comment"),
            (r"//\s*FIXME:", "FIXME comment"),
            (r"//\s*DEBUG:", "DEBUG comment"),
        ]
    if ext in {".res", ".resi"}:
        return [
            (r"\bJs\.log\s*\(", "Js.log() statement"),
            (r"\bConsole\.log\s*\(", "Console.log() statement"),
            (r"//\s*TODO:\s*remove", "TODO: remove comment"),
            (r"//\s*FIXME:", "FIXME comment"),
            (r"//\s*DEBUG:", "DEBUG comment"),
        ]
    if ext in {".css", ".scss", ".sass"}:
        return [
            (r"/\*\s*DEBUG:", "DEBUG comment"),
            (r"/\*\s*FIXME:", "FIXME comment"),
        ]
    return [
        (r"(//|#|\*)\s*TODO:\s*remove", "TODO: remove comment"),
        (r"(//|#|\*)\s*FIXME:", "FIXME comment"),
        (r"(//|#|\*)\s*DEBUG:", "DEBUG comment"),
    ]


def _detect_debug_statements(file_path: str) -> list[str]:
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, FileNotFoundError):
        return []

    issues: list[str] = []
    ext = Path(file_path).suffix.lower()
    pats = _patterns_for_ext(ext)
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        for pat, desc in pats:
            if re.search(pat, line, re.IGNORECASE):
                # Skip if inside string literal
                if _is_in_string_literal(line, pat):
                    continue
                # Skip legitimate logging heuristics for print/console
                pat_low = pat.lower()
                if ("print(" in pat_low or "console.log" in pat_low) and _looks_like_legitimate_logging(line):
                    continue
                issues.append(f"Line {i}: {desc} - {s}")
    return issues


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    all_issues: list[str] = []
    for fp in files:
        p = Path(fp)
        if not p.exists():
            continue
        issues = _detect_debug_statements(fp)
        if issues:
            name = p.name
            for it in issues:
                all_issues.append(f"{name}: {it}")

    if not all_issues:
        return Action()

    lines: list[str] = ["⚠️ **Debug statements found:**"]
    for s in all_issues[:MAX_ISSUES_TO_SHOW]:
        lines.append(f"   • {s}")
    if len(all_issues) > MAX_ISSUES_TO_SHOW:
        lines.append(f"   … and {len(all_issues) - MAX_ISSUES_TO_SHOW} more")

    return Action(add_context="\n".join(lines), severity="error", category="quality")
