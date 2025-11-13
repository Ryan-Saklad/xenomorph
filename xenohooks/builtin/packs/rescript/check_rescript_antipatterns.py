"""
ReScript antipattern detection (ported to dict payload API).

Detects forbidden or discouraged patterns such as Obj.magic and %raw in .res/.resi
files. Blocks on findings with a concise reason.
"""

import re
from pathlib import Path
from typing import Any

from xenohooks.common.types import Action


def _detect_rescript_antipatterns(file_path: str) -> list[str]:
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, FileNotFoundError):
        return []

    issues: list[str] = []
    for line_num, line in enumerate(content.splitlines(), 1):
        s = line.strip()

        # Skip comments quickly
        if s.startswith("//") or s.startswith("/*"):
            continue

        # Obj.magic usage
        if re.search(r"\bObj\.magic\s*\(", line):
            issues.append(f"  Line {line_num}: Avoid Obj.magic - use proper type conversion instead\n    {s}")

        # %raw usage
        if re.search(r"%raw\s*\(", line):
            issues.append(f"  Line {line_num}: Avoid %raw - use ReScript bindings instead\n    {s}")

        # Unsafe type assertions
        if re.search(r":>\s*[A-Z][a-zA-Z0-9_]*", line):
            issues.append(f"  Line {line_num}: Unsafe type assertion - consider safer alternatives\n    {s}")

        # Direct JavaScript access patterns
        if re.search(r"##\s*[a-zA-Z_][a-zA-Z0-9_]*\s*=", line):
            issues.append(f"  Line {line_num}: Direct JS object mutation - prefer immutable updates\n    {s}")

        # console.log (should use Js.log)
        if re.search(r"\bconsole\.log\s*\(", line):
            issues.append(f"  Line {line_num}: Use Js.log instead of console.log in ReScript\n    {s}")

        # JS-style block comments (non-doc) are invalid in ReScript
        if re.search(r"/\*(?!\*)", line):  # /* but not /**
            issues.append(f"  Line {line_num}: JS-style comments not allowed - use // instead\n    {s}")

        # Multiline JS comments that aren't doc comments
        if re.search(r"\*/(?!\s*$)", line) and not re.search(r"\*\*/", line):
            issues.append(f"  Line {line_num}: JS-style comment end - use // instead\n    {s}")

    return issues


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    res_files = [f for f in files if Path(f).suffix.lower() in {".res", ".resi"} and Path(f).exists()]
    if not res_files:
        return Action()

    problems: list[str] = []
    for fp in res_files:
        issues = _detect_rescript_antipatterns(fp)
        if issues:
            problems.append(f"🚨 {Path(fp).name}:\n" + "\n".join(issues))

    if not problems:
        return Action()

    ctx = (
        "ReScript anti-patterns detected:\n\n" +
        "\n\n".join(problems) +
        "\n\n💡 Follow ReScript best practices (see project guidelines)"
    )
    return Action(add_context=ctx, severity="error", category="rescript")
