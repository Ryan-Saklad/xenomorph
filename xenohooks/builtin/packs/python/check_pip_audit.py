"""
Python dependency vulnerability scanning using pip-audit.

Scans project dependencies for known security vulnerabilities.
NEVER blocks commits - dependency issues should be tracked separately.
Provides concise summaries instead of verbose JSON output.
"""

import json
from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action

# Output limits for concise feedback
MAX_VULNS_TO_SHOW = 3


def _pip_audit_cmd() -> list[str] | None:
    """Return command vector for pip-audit, or None if not available."""
    if find_exe("pip-audit"):
        return ["pip-audit"]
    # Try via uv
    if find_exe("uv"):
        return ["uv", "run", "pip-audit"]
    # Try via python
    for py in ["python3", "python", "py"]:
        if find_exe(py):
            return [py, "-m", "pip_audit"]
    return None


def _parse_pip_audit_json(output: str) -> list[dict[str, Any]]:
    """Parse pip-audit JSON output and extract key vulnerability info."""
    try:
        data = json.loads(output)
        vulns: list[dict[str, Any]] = []

        # pip-audit JSON format: {"dependencies": [...]}
        dependencies = data.get("dependencies", [])
        for dep in dependencies:
            name = dep.get("name", "unknown")
            version = dep.get("version", "unknown")
            for vuln in dep.get("vulns", []):
                vulns.append({
                    "package": name,
                    "version": version,
                    "id": vuln.get("id", ""),
                    "severity": vuln.get("fix_versions", []),
                    "description": vuln.get("description", "")[:100],  # Truncate
                })
        return vulns
    except Exception:
        return []


def _check_if_package_imported(package: str, files: list[str]) -> bool:
    """Check if a package is actually imported in any of the changed files."""
    # Normalize package name (e.g., "some-package" -> "some_package")
    import_name = package.replace("-", "_").lower()

    for file_path in files:
        try:
            if not Path(file_path).suffix.lower() == ".py":
                continue
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            # Simple heuristic: check for import statements
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    if import_name in stripped.lower():
                        return True
        except Exception:
            continue
    return False


def run(payload: dict[str, Any]) -> Action:
    """Run pip-audit on project dependencies (non-blocking)."""
    # Only run on Python file changes or requirements file changes
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    # Check if any Python files or requirements files changed
    has_python_files = any(Path(f).suffix.lower() == ".py" for f in files)
    has_requirements = any("requirements" in Path(f).name.lower() for f in files)

    if not (has_python_files or has_requirements):
        return Action()

    cmd = _pip_audit_cmd()
    if cmd is None:
        # Silently skip if pip-audit not available
        return Action()

    # Run pip-audit with JSON output
    result = run_command([*cmd, "--format", "json"], timeout_seconds=60)

    if result.code == 0:
        # No vulnerabilities found
        return Action()

    # Parse vulnerabilities
    vulns = _parse_pip_audit_json(result.out or "")

    if not vulns:
        # Failed to parse or no vulnerabilities
        if result.err and "error" in result.err.lower():
            return Action(
                add_context=f"⚠️ **pip-audit:** Tool error\n   • {result.err[:200]}",
                severity="info",
                category="security"
            )
        return Action()

    # Filter to only vulnerabilities in packages used by changed files
    relevant_vulns: list[dict[str, Any]] = []
    other_vulns: list[dict[str, Any]] = []

    for vuln in vulns:
        pkg = vuln["package"]
        if _check_if_package_imported(pkg, files):
            relevant_vulns.append(vuln)
        else:
            other_vulns.append(vuln)

    # Build concise output
    lines: list[str] = []

    if relevant_vulns:
        # These are in code being modified - higher priority
        lines.append(f"🔒 **pip-audit:** {len(relevant_vulns)} vulnerability(ies) in modified code")
        for v in relevant_vulns[:MAX_VULNS_TO_SHOW]:
            lines.append(f"   • {v['package']} {v['version']}: {v['id']}")
        if len(relevant_vulns) > MAX_VULNS_TO_SHOW:
            lines.append(f"   … and {len(relevant_vulns) - MAX_VULNS_TO_SHOW} more")
        lines.append("   💡 Run `pip-audit` for full details")
        return Action(
            add_context="\n".join(lines),
            severity="warn",
            category="security"
        )

    if other_vulns:
        # These are unrelated to changed code - informational only
        lines.append(f"ℹ️ **pip-audit:** {len(other_vulns)} dependency vulnerability(ies) (unrelated to changes)")
        for v in other_vulns[:MAX_VULNS_TO_SHOW]:
            lines.append(f"   • {v['package']} {v['version']}: {v['id']}")
        if len(other_vulns) > MAX_VULNS_TO_SHOW:
            lines.append(f"   … and {len(other_vulns) - MAX_VULNS_TO_SHOW} more")
        lines.append("   💡 Consider running `pip-audit` separately to address")
        return Action(
            add_context="\n".join(lines),
            severity="info",
            category="security"
        )

    return Action()