"""
Frontend Vitest testing integration.

Runs tests only for the specific changed frontend file, when a matching test
file exists. Non-blocking by default; sets severity/category for policy-based
blocking (test failures → severity="error", category="test"). Skips silently
when no matching test file is found.
"""

from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action


FRONTEND_ROOT_CANDIDATES = ["frontend"]  # can be expanded if needed
SRC_PREFIX = "frontend/src/"
SUPPORTED_SOURCE_EXTS = {".js", ".mjs", ".ts", ".tsx", ".res"}
TEST_EXTS = [".js", ".ts", ".mjs", ".res"]


def _frontend_cwd(payload: dict[str, Any]) -> str | None:
    """Resolve the working directory where vitest should run (frontend app dir)."""
    base = Path(str(payload.get("cwd") or "."))
    for cand in FRONTEND_ROOT_CANDIDATES:
        p = (base / cand)
        if p.exists() and p.is_dir():
            return str(p)
    # fallback to base if no explicit frontend dir exists
    return str(base)


def _find_test_file_for_source(source_file_path: str) -> str | None:
    """Find the corresponding test file for a source file using common patterns."""
    source_path = Path(source_file_path)

    test_patterns: list[Path] = []
    for test_ext in TEST_EXTS:
        test_patterns.extend(
            [
                # Same directory with .test/.spec
                source_path.with_suffix(f".test{test_ext}"),
                source_path.with_suffix(f".spec{test_ext}"),
                # __tests__ subdirectory
                source_path.parent / "__tests__" / f"{source_path.stem}.test{test_ext}",
                source_path.parent / "__tests__" / f"{source_path.stem}.spec{test_ext}",
                # tests directory parallel to src
                Path(str(source_path).replace("/src/", "/tests/")).with_suffix(f".test{test_ext}"),
                Path(str(source_path).replace("/src/", "/tests/")).with_suffix(f".spec{test_ext}"),
            ]
        )

    for test_path in test_patterns:
        try:
            if test_path.exists():
                return str(test_path)
        except Exception:
            continue
    return None


def _vitest_base_cmd() -> list[str] | None:
    """Return preferred command list for running vitest in run mode.

    Preference order:
    - npx vitest --run
    - pnpm vitest --run
    - npm run test -- --run
    - yarn test (best-effort)
    """
    if find_exe("npx"):
        return ["npx", "vitest", "--run"]
    if find_exe("pnpm"):
        return ["pnpm", "vitest", "--run"]
    if find_exe("npm"):
        return ["npm", "run", "test", "--", "--run"]
    if find_exe("yarn"):
        return ["yarn", "test"]
    return None


def _run_vitest_for_file(file_path: str, cwd: str | None) -> tuple[str | None, str]:
    """Run vitest for a specific file by matching its test name pattern."""
    test_file = _find_test_file_for_source(file_path)
    if not test_file:
        return None, "success"  # No test file → not an error

    base = _vitest_base_cmd()
    if base is None:
        return "Vitest not available - install with 'npm install vitest'", "warning"

    test_stem = Path(test_file).stem  # e.g. Utils.test
    pattern = test_stem.replace(".test", "").replace(".spec", "")

    cmd = [*base, "-t", pattern]
    r = run_command(cmd, cwd=cwd, timeout_seconds=45)

    if r.timed_out:
        return f"Vitest tests timed out for {Path(file_path).name}", "warning"

    out = (r.out or "")
    err = (r.err or "")

    if r.code == 0:
        # Passed or no tests matched: treat as success
        return None, "success"

    # Non-zero → likely failures; collect relevant lines
    error_output = out + err
    lines = error_output.splitlines()
    relevant: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("npm") or s.startswith("yarn"):
            continue
        low = s.lower()
        if any(k in low for k in ["fail", "error", "✗", "expected", "received", "test", "assert"]):
            relevant.append(s)
    if relevant:
        return "\n".join(relevant[:10]), "error"
    return f"Tests failed for {Path(test_file).name}", "error"


def run(payload: dict[str, Any]) -> Action:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    if not files:
        return Action()

    # Only run on frontend source files
    frontend_files = [
        f
        for f in files
        if f.startswith(SRC_PREFIX)
        and Path(f).suffix.lower() in SUPPORTED_SOURCE_EXTS
        and Path(f).exists()
    ]

    if not frontend_files:
        return Action()

    cwd = _frontend_cwd(payload)
    issues: list[str] = []
    warnings: list[str] = []
    tests_run = 0

    for src in frontend_files:
        msg, status = _run_vitest_for_file(src, cwd=cwd)
        if status == "error" and msg:
            issues.append(f"🚨 {Path(src).name}:\n  {msg}")
        elif status == "warning" and msg:
            warnings.append(f"⚠️  {Path(src).name}: {msg}")
        elif status == "success":
            if _find_test_file_for_source(src):
                tests_run += 1

    if issues:
        ctx = (
            "Test failures detected:\n\n" +
            "\n\n".join(issues) +
            "\n\n💡 Run 'cd frontend && npm run test' to debug"
        )
        return Action(add_context=ctx, severity="error", category="test")

    if warnings:
        return Action(add_context="\n".join(warnings), severity="warn", category="test")

    return Action()
