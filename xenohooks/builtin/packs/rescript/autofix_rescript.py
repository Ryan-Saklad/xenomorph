"""
ReScript autofix functionality (ported to dict payload API).

Automatically fixes safe ReScript warning types and JS-style comments.
Adds a concise additional context summary; never blocks.
"""

import re
from pathlib import Path
from typing import Any

from shutil import which as find_exe
from xenohooks.common.exec import run_command
from xenohooks.common.types import Action


def _frontend_cwd(payload: dict[str, Any]) -> str | None:
    base = Path(str(payload.get("cwd") or "."))
    # Prefer a dedicated frontend dir if present
    for cand in (base / "frontend", base):
        if cand.exists() and cand.is_dir():
            return str(cand)
    return str(base)


def _is_variable_actually_used(var_name: str, lines: list[str], target_line_idx: int) -> bool:
    """Heuristic to reduce false positives by checking other lines for usage."""
    for i, line in enumerate(lines):
        if i == target_line_idx:
            continue
        if f'"{var_name}"' in line or f"'{var_name}'" in line or f"`{var_name}`" in line:
            return True
        if f"{{{var_name}}}" in line:
            return True
        if re.search(rf"\b{re.escape(var_name)}\b(?![\"'])", line):
            s = line.strip()
            if not (s.startswith("//") or s.startswith("/*")):
                return True
    return False


def _fix_unused_variable(file_path: Path, line_num: str, message: str) -> dict[str, str] | None:
    """Fix Warning 26/27 - unused variables by prefixing with an underscore."""
    m = re.search(r"unused variable (\w+)", message)
    if not m:
        return None
    var_name = m.group(1)

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(True)
        idx = int(line_num) - 1
        if idx < 0 or idx >= len(lines):
            return None
        if _is_variable_actually_used(var_name, lines, idx):
            return None
        original_line = lines[idx]

        fixes: list[tuple[str, str]] = [
            (rf"\b{re.escape(var_name)}\s*=>", f"_{var_name} =>"),
            (rf"(\s*)let\s+{re.escape(var_name)}\s*=", rf"\1let _{var_name} ="),
            (rf"~{re.escape(var_name)}\b", f"~_{var_name}"),
        ]

        new_line = original_line
        applied = None
        for pat, rep in fixes:
            if re.search(pat, new_line):
                new_line = re.sub(pat, rep, new_line)
                applied = f"{var_name} → _{var_name}"
                break

        if applied and new_line != original_line:
            lines[idx] = new_line
            file_path.write_text("".join(lines), encoding="utf-8")
            return {"old": original_line.strip(), "new": new_line.strip(), "description": applied}
    except Exception:
        pass
    return None


def _fix_js_comments(file_path: Path) -> list[dict[str, str]]:
    """Convert JS-style comments (/* ... */) to ReScript-style // comments where safe."""
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(True)
    except Exception:
        return []

    fixes: list[dict[str, str]] = []
    modified = False

    for i, line in enumerate(lines):
        original_line = line
        new_line = line

        if re.search(r"/\*(?!\*)", line) and "*/" in line:
            m = re.search(r"/\*\s*(.*?)\s*\*/", line)
            if m:
                comment_text = m.group(1).strip()
                new_line = re.sub(r"/\*\s*.*?\s*\*/", f"// {comment_text}", line)
        elif re.search(r"/\*(?!\*)", line) and "*/" not in line:
            m = re.search(r"/\*\s*(.*)", line)
            if m:
                comment_text = m.group(1).strip()
                indent = re.match(r"(\s*)", line).group(1) if re.match(r"(\s*)", line) else ""
                new_line = f"{indent}// {comment_text}\n"
        elif "*/" in line and not re.search(r"/\*", line):
            m = re.search(r"(.*?)\*/(.*)", line)
            if m:
                before_end = m.group(1).strip()
                after_end = m.group(2).strip()
                indent = re.match(r"(\s*)", line).group(1) if re.match(r"(\s*)", line) else ""
                if before_end and after_end:
                    new_line = f"{indent}// {before_end} {after_end}\n"
                elif before_end:
                    new_line = f"{indent}// {before_end}\n"
                elif after_end:
                    new_line = f"{indent}{after_end}\n"
                else:
                    new_line = ""

        if new_line != original_line:
            lines[i] = new_line
            fixes.append({
                "line": i + 1,
                "old": original_line.strip(),
                "new": new_line.strip() if new_line.strip() else "(removed empty line)",
                "description": "JS-style comment → ReScript comment",
            })
            modified = True

    if modified:
        try:
            file_path.write_text("".join(lines), encoding="utf-8")
        except Exception:
            return []

    return fixes


def _get_rescript_warnings(frontend_cwd: str) -> list[dict[str, Any]]:
    """Get ReScript compilation warnings by running the build script."""
    runner = None
    if find_exe("npm"):
        runner = ["npm", "run", "res:build"]
    elif find_exe("pnpm"):
        runner = ["pnpm", "run", "res:build"]
    elif find_exe("yarn"):
        runner = ["yarn", "res:build"]
    else:
        return []

    r = run_command(runner, cwd=frontend_cwd, timeout_seconds=60)
    warnings: list[dict[str, Any]] = []
    if r.out:
        for line in r.out.splitlines():
            if "Warning number" in line:
                parts = line.split(":")
                if len(parts) >= 3:
                    warning_num_part = parts[0].strip()
                    file_part = parts[1].strip() if len(parts) > 2 else ""
                    message = ":".join(parts[2:]).strip()
                    m = re.search(r"Warning number (\d+)", warning_num_part)
                    if m:
                        warnings.append({
                            "number": int(m.group(1)),
                            "file": file_part,
                            "message": message,
                        })
    return warnings


def run(payload: dict[str, Any]) -> Action:
    # Only run on ReScript files or when payload has no files (build-only)
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    res_files = [f for f in files if Path(f).suffix.lower() in {".res", ".resi"} and Path(f).exists()]
    if files and not res_files:
        return Action()

    # Try to fix JS-style comments in changed files
    js_comment_fixes: list[str] = []
    for fp in res_files:
        p = Path(fp)
        if p.exists():
            fixes = _fix_js_comments(p)
            for fx in fixes:
                js_comment_fixes.append(f"✅ {p.name}: {fx['description']} (line {fx['line']})")

    frontend_dir = _frontend_cwd(payload)
    warnings = _get_rescript_warnings(frontend_dir)

    fixes_applied = list(js_comment_fixes)
    # Auto-fix safe warnings (unused variable 26/27)
    for w in warnings:
        num = w.get("number")
        file_name = str(w.get("file") or "")
        message = str(w.get("message") or "")
        if num not in (26, 27):
            continue
        # Try matching file path under common src locations
        candidates = [
            Path(frontend_dir) / "src" / file_name,
            Path(frontend_dir) / "src" / "pages" / file_name,
            Path(frontend_dir) / "src" / "components" / file_name,
        ]
        file_path: Path | None = None
        for c in candidates:
            if c.exists():
                file_path = c
                break
        if file_path is None:
            continue
        m = re.search(r"line (\d+)", message)
        if not m:
            continue
        line_num = m.group(1)
        fx = _fix_unused_variable(file_path, line_num, message)
        if fx:
            fixes_applied.append(f"✅ {file_name}: {fx['description']}")

    # Build concise additional context
    if fixes_applied:
        lines = [
            f"Applied {len(fixes_applied)} automatic fix(es):",
            "",
            *fixes_applied[:10],
        ]
        if len(fixes_applied) > 10:
            lines.append(f"… and {len(fixes_applied) - 10} more fix(es)")
        return Action(add_context="\n".join(lines), severity="info", category="rescript")

    if warnings:
        return Action(add_context=f"ReScript: found {len(warnings)} warning(s) but none were auto-fixable", severity="warn", category="rescript")

    if js_comment_fixes:
        # Should have been caught above, but keep for completeness
        return Action(add_context=f"Applied {len(js_comment_fixes)} JS-style comment fix(es)", severity="info", category="rescript")

    return Action()
