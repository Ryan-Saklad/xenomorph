"""
Design token checker.
Identifies hardcoded values that should use design tokens instead.
Returns Action(add_context=...) with a concise summary (never blocks).
"""

import re
from pathlib import Path
from typing import Any

from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_FILES_TO_SHOW = 3
MAX_CATEGORIES_PER_FILE = 3


# Patterns for CSS-like files
DESIGN_TOKEN_PATTERNS: dict[str, dict[str, Any]] = {
    "colors": {
        "patterns": [
            r"#[0-9a-fA-F]{3,8}",
            r"rgb\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)",
            r"rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*[\d.]+\s*\)",
            r"hsl\s*\(\s*\d+\s*,\s*\d+%\s*,\s*\d+%\s*\)",
        ],
        "message": "Use design token for color",
    },
    "spacing": {
        "patterns": [
            r"\b(?:[2-9](?:\.\d+)?(?:px|rem|em))\b",
            r"\b(?:\d{2,}(?:\.\d+)?(?:px|rem|em))\b",
            r"\b(?:1\.[1-9](?:\d+)?(?:px|rem|em))\b",
        ],
        "message": "Use design token for spacing",
    },
    "typography": {
        "patterns": [
            r"font-size\s*:\s*\d+(?:\.\d+)?(?:px|rem|em)",
            r"line-height\s*:\s*\d+(?:\.\d+)?",
        ],
        "message": "Use design token for typography",
    },
    "border_radius": {
        "patterns": [
            r"border-radius\s*:\s*(?!0(?:px|rem|em)?|50%)\d+(?:\.\d+)?(?:px|rem|em)",
            r"border-.*-radius\s*:\s*(?!0(?:px|rem|em)?|50%)\d+(?:\.\d+)?(?:px|rem|em)",
        ],
        "message": "Use design token for border radius",
    },
    "shadows": {
        "patterns": [
            r"box-shadow\s*:\s*(?!none)[^;]+(?:px|rem|em)[^;]*",
            r"text-shadow\s*:\s*(?!none)[^;]+(?:px|rem|em)[^;]*",
        ],
        "message": "Use design token for shadows",
    },
    "z_index": {
        "patterns": [r"z-index\s*:\s*(?!-1|0|1|auto)\d+"],
        "message": "Use design token for z-index",
    },
}


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _check_css_like(file_path: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    lines = _read_lines(file_path)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("/*") or s.startswith("//"):
            continue
        for category, cfg in DESIGN_TOKEN_PATTERNS.items():
            for pat in cfg["patterns"]:
                for m in re.finditer(pat, s, re.IGNORECASE):
                    issues.append({
                        "line": i,
                        "value": m.group(0),
                        "category": category,
                        "message": cfg["message"],
                        "line_content": s,
                    })
    return issues


def _check_rescript(file_path: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    lines = _read_lines(file_path)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("/*"):
            continue
        style_pats = [
            r'(?:color|backgroundColor|borderColor)\s*:\s*"(#[0-9a-fA-F]{3,8})"',
            r'(?:padding|margin|width|height|fontSize)\s*:\s*"(\d+(?:\.\d+)?(?:px|rem|em))"',
            r'borderRadius\s*:\s*"(\d+(?:\.\d+)?(?:px|rem|em))"',
        ]
        for pat in style_pats:
            for m in re.finditer(pat, s, re.IGNORECASE):
                issues.append({
                    "line": i,
                    "value": m.group(1),
                    "category": "rescript_styles",
                    "message": "Use design token for inline styles",
                    "line_content": s,
                })
    return issues


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    relevant: list[tuple[str, Path]] = []
    for p in files:
        try:
            path = Path(p)
        except Exception:
            continue
        suf = path.suffix.lower()
        if suf in {".css", ".scss", ".sass"} and path.exists():
            relevant.append(("css", path))
        elif suf in {".res", ".resi"} and path.exists():
            relevant.append(("res", path))

    if not relevant:
        return Action()

    all_issues: list[dict[str, Any]] = []
    for kind, path in relevant:
        issues = _check_css_like(path) if kind == "css" else _check_rescript(path)
        if issues:
            all_issues.append({"file": str(path), "issues": issues})

    if not all_issues:
        return Action()

    # Build concise additional context (limited lines)
    lines: list[str] = ["💡 **Design token opportunities:**"]
    for file_data in all_issues[:MAX_FILES_TO_SHOW]:
        lines.append(f"   • {Path(file_data['file']).name}")
        cats: dict[str, list[dict[str, Any]]] = {}
        for iss in file_data["issues"]:
            cats.setdefault(iss["category"], []).append(iss)
        for category, cat_issues in list(cats.items())[:MAX_CATEGORIES_PER_FILE]:
            lines.append(f"     - {category.replace('_',' ').title()}: {len(cat_issues)} occurrence(s)")
    if len(all_issues) > MAX_FILES_TO_SHOW:
        lines.append(f"   … and {len(all_issues) - MAX_FILES_TO_SHOW} more file(s)")

    return Action(add_context="\n".join(lines), severity="info", category="quality")
