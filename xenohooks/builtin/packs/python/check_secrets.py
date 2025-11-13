"""
Security secrets scanning (ported to dict payload API).

Runs detect-secrets on changed files to prevent credential leaks.
Falls back to lightweight regex-based detection when the tool is unavailable.
Adds additional context on findings; never blocks.
"""

import json
import re
from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_SECRETS_TO_SHOW = 3


def _detect_secrets_cmd() -> list[str] | None:
    """Return command vector for detect-secrets, or None if not available."""
    if find_exe("detect-secrets"):
        return ["detect-secrets", "scan"]
    # Python module fallback
    if find_exe("python"):
        return ["python", "-m", "detect_secrets", "scan"]
    if find_exe("python3"):
        return ["python3", "-m", "detect_secrets", "scan"]
    if find_exe("py"):
        return ["py", "-m", "detect_secrets", "scan"]
    return None


def _format_secrets_output(secrets_data: dict) -> str:
    lines: list[str] = []
    results = secrets_data.get("results", {})
    if not isinstance(results, dict):
        return "Potential secrets detected (details unavailable)"
    for file_path, arr in results.items():
        if not isinstance(arr, list):
            continue
        for secret in arr:
            if not isinstance(secret, dict):
                continue
            line_num = secret.get("line_number", "?")
            secret_type = secret.get("type", "Unknown")
            hashed_secret = str(secret.get("hashed_secret", ""))
            if hashed_secret:
                hashed_secret = hashed_secret[:12] + "..."
            lines.append(f"Line {line_num}: {secret_type}" + (f" (hash: {hashed_secret})" if hashed_secret else ""))
    return "\n".join(lines) if lines else "Potential secrets detected (details unavailable)"


def _is_pattern_false_positive(line: str, matched_text: str) -> bool:
    s = line.strip()
    if s.startswith("#") or s.startswith("//"):
        return True
    matched_lower = matched_text.lower()
    for fp in [
        "example",
        "test",
        "dummy",
        "placeholder",
        "sample",
        "your_key_here",
        "replace_with",
        "changeme",
        "todo",
    ]:
        if fp in matched_lower:
            return True
    return False


_FALLBACK_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(api[_-]?key|apikey)\s*[=:]\s*['\"]([a-zA-Z0-9_-]{16,})['\"]", "API Key"),
    (r"(?i)(secret[_-]?key|secretkey)\s*[=:]\s*['\"]([a-zA-Z0-9_-]{16,})['\"]", "Secret Key"),
    (r"(?i)(password|pwd|pass)\s*[=:]\s*['\"]([a-zA-Z0-9_@#$%^&*!-]{8,})['\"]", "Password"),
    (r"(?i)(token|access[_-]?token)\s*[=:]\s*['\"]([a-zA-Z0-9_-]{16,})['\"]", "Token"),
    (r"sk-[a-zA-Z0-9-]{20,}", "OpenAI-style API Key"),
    (r"(?i)github[_-]?token['\"]?\s*[=:]\s*['\"]?([a-zA-Z0-9_]{40})['\"]?", "GitHub Token"),
    (r"(?i)aws[_-]?access[_-]?key[_-]?id\s*[=:]\s*['\"]([A-Z0-9]{20})['\"]", "AWS Access Key"),
]


def _run_pattern_based_detection(file_path: str) -> tuple[str | None, str]:
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, FileNotFoundError):
        return None, "success"

    errors: list[str] = []
    warns: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, stype in _FALLBACK_PATTERNS:
            for m in re.finditer(pat, line):
                if _is_pattern_false_positive(line, m.group(0)):
                    continue
                issues.append(f"Line {i}: {stype} pattern detected")
    if issues:
        return "\n".join(issues), "error"
    return None, "success"


def _is_likely_false_positive_file(file_path: str) -> bool:
    p = Path(file_path)
    s = str(p).lower()
    for tok in [
        "test",
        "spec",
        "example",
        "demo",
        "mock",
        "fixture",
        ".md",
        ".txt",
        ".rst",
        "readme",
        "changelog",
    ]:
        if tok in s:
            return True
    return False


def _run_detect_secrets(file_path: str) -> tuple[str | None, str]:
    cmd = _detect_secrets_cmd()
    if cmd is None:
        return _run_pattern_based_detection(file_path)

    r = run_command([*cmd, file_path], timeout_seconds=30)

    if r.code != 0:
        try:
            data = json.loads(r.out) if (r.out or "").strip() else {}
        except Exception:
            out = (r.out or "").strip()
            if out:
                return out, "error"
            err = (r.err or "").strip()
            if err:
                return f"detect-secrets error: {err}", "warning"
            return None, "success"

        results = data.get("results")
        if isinstance(results, dict) and results:
            return _format_secrets_output(data), "error"

        err = (r.err or "").strip()
        if err:
            return f"detect-secrets error: {err}", "warning"
        return None, "success"

    return None, "success"


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    issues: list[str] = []

    for fp in files:
        p = Path(fp)
        if not p.exists():
            continue
        try:
            # Skip very large files (>1MB)
            if p.stat().st_size > 1024 * 1024:
                continue
        except OSError:
            continue

        msg, status = _run_detect_secrets(str(p))
        if not msg:
            continue
        if status == "error":
            prefix = "(possible false positive) " if _is_likely_false_positive_file(str(p)) else ""
            errors.append(f"{p.name}: {prefix}{msg}")
        elif status == "warning":
            warns.append(f"{p.name}: {msg}")

    if not errors and not warns:
        return Action()

    # Summarize to keep it concise
    lines: list[str] = []
    if errors:
        lines.append(f"🔐 **Secrets detected:** {len(errors)} file(s) with potential secrets")
        for s in errors[:MAX_SECRETS_TO_SHOW]:
            lines.append(f"   • {s}")
        if len(errors) > MAX_SECRETS_TO_SHOW:
            lines.append(f"   … and {len(errors) - MAX_SECRETS_TO_SHOW} more file(s)")
        return Action(add_context="\n".join(lines), severity="error", category="security")
    # Only warnings present
    lines.append(f"⚠️ **Secrets warnings:** {len(warns)} potential issue(s)")
    for s in warns[:MAX_SECRETS_TO_SHOW]:
        lines.append(f"   • {s}")
    if len(warns) > MAX_SECRETS_TO_SHOW:
        lines.append(f"   … and {len(warns) - MAX_SECRETS_TO_SHOW} more warning(s)")
    return Action(add_context="\n".join(lines), severity="warn", category="security")
