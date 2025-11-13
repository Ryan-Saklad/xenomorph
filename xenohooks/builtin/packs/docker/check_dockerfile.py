"""
Dockerfile validation and linting.
Runs Hadolint on Dockerfiles for best practices.
"""

from pathlib import Path
from typing import Any

from xenohooks.common.exec import run_command
from xenohooks.common.types import Action


def _is_dockerfile(file_path: str) -> bool:
    """Heuristic check if a path is a Dockerfile."""
    ALLOWED_NAMES = {"dockerfile", "dockerfile.prod", "dockerfile.dev", "dockerfile.staging", "dockerfile.test"}

    path = Path(file_path)
    name = path.name.lower()
    if name.lower() in ALLOWED_NAMES:
        return True
    if name.startswith("dockerfile."):
        return True
    # Content heuristic: first line begins with FROM
    try:
        with path.open("r", encoding="utf-8") as f:
            first = f.readline().strip().upper()
            return first.startswith("FROM ")
    except Exception:
        return False


def _run_basic_check(file_path: str) -> tuple[str | None, str]:
    """Basic Dockerfile validation when hadolint isn't available."""
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return None, "success"

    issues: list[str] = []
    lines = content.splitlines()
    has_from = False

    for i, line in enumerate(lines, 1):
        s = line.strip().upper()
        if s.startswith("FROM"):
            has_from = True
        if s.startswith("RUN") and "APT-GET UPDATE" in s and "APT-GET INSTALL" not in s:
            issues.append(f"  Line {i}: apt-get update should be chained with install")
        if s.startswith("RUN") and "SUDO" in s:
            issues.append(f"  Line {i}: Avoid using sudo in containers")
        if s.startswith("ADD") and not (s.endswith(".TAR") or s.endswith(".TAR.GZ")):
            issues.append(f"  Line {i}: Use COPY instead of ADD for files")
        if "EXPOSE 22" in s:
            issues.append(f"  Line {i}: Avoid exposing SSH port in containers")

    if not has_from:
        issues.append("  Missing FROM instruction")

    if issues:
        return "\n".join(issues), "error"
    return None, "success"


def _format_hadolint(out: str) -> str:
    lines = out.strip().splitlines()
    if not lines:
        return out.strip()
    formatted: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        parts = s.split(":", 3)
        if len(parts) >= 3:
            line_no = parts[1]
            rest = ":".join(parts[2:]).strip()
            formatted.append(f"  Line {line_no}: {rest}")
        else:
            formatted.append(f"  {s}")
    return "\n".join(formatted)


def _run_hadolint(file_path: str) -> tuple[str | None, str]:
    r = run_command(["hadolint", file_path], timeout_seconds=30)
    if r.timed_out:
        return "Hadolint check timed out", "warning"
    if r.code == 127:
        return _run_basic_check(file_path)
    if r.code != 0 and r.out.strip():
        return _format_hadolint(r.out.strip()), "error"
    if r.err.strip():
        return f"Hadolint stderr: {r.err.strip()}", "warning"
    return None, "success"


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    dockerfiles = [f for f in files if _is_dockerfile(f) and Path(f).exists()]
    if not dockerfiles:
        return Action()

    errors: list[str] = []
    warns: list[str] = []
    for fp in dockerfiles:
        msg, status = _run_hadolint(fp)
        if status == "error" and msg:
            errors.append(f"🚨 {Path(fp).name}:\n{msg}")
        elif status == "warning" and msg:
            warns.append(f"⚠️  {Path(fp).name}: {msg}")

    if errors:
        ctx = (
            "Dockerfile issues detected:\n\n" + "\n\n".join(errors) +
            "\n\n💡 Fix Dockerfile issues to improve security and efficiency"
        )
        return Action(add_context=ctx, severity="error", category="docker")

    if warns:
        ctx = "\n".join(warns)
        return Action(add_context=ctx, severity="warn", category="docker")

    return Action()
