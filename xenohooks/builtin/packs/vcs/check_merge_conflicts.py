"""
Merge conflict detection (ported to dict payload API).

Detects Git merge conflict markers in changed files.
Adds concise additional context; never blocks.
"""

from pathlib import Path
from typing import Any

from xenohooks.common.types import Action


_MARKERS: list[tuple[str, str]] = [
    ("<<<<<<<", "conflict start marker"),
    ("=======", "conflict separator marker"),
    (">>>>>>>", "conflict end marker"),
    ("|||||||", "conflict base marker (diff3 style)"),
]


def _is_likely_conflict_marker(line: str, marker: str) -> bool:
    s = line.strip()
    if marker in ("<<<<<<<", ">>>>>>>"):
        after = s[len(marker):].strip()
        return len(after) > 0
    if marker == "=======":
        after = s[len(marker):].strip()
        return len(after) == 0
    if marker == "|||||||":
        after = s[len(marker):].strip()
        return len(after) > 0
    return True


def _detect_merge_conflicts(file_path: str) -> list[str]:
    try:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, PermissionError, FileNotFoundError):
        return []

    issues: list[str] = []
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        for marker, desc in _MARKERS:
            if s.startswith(marker) and _is_likely_conflict_marker(s, marker):
                issues.append(f"Line {i}: {desc} - {s}")
                break
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
        issues = _detect_merge_conflicts(fp)
        if issues:
            name = p.name
            for it in issues:
                all_issues.append(f"{name}: {it}")

    if not all_issues:
        return Action()

    lines: list[str] = ["Merge conflict markers detected:"]
    for s in all_issues[:5]:
        lines.append(f"  - {s}")
    if len(all_issues) > 5:
        lines.append(f"  … and {len(all_issues) - 5} more")

    return Action(add_context="\n".join(lines))
