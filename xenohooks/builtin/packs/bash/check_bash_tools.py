"""
Bash tool violations checker.
Detects commands that use forbidden bash tools instead of Claude Code tools.
"""

import re
import shlex
from typing import Any

from xenohooks.common.types import Action
from xenohooks.common.utils import get_command

# Output limits for concise feedback
MAX_ISSUES_TO_SHOW = 3


def run(payload: dict[str, Any]) -> Action:
    """Advise on better tool usage patterns; never blocks."""
    if str(payload.get("tool_name") or "") != "Bash":
        return Action()

    command = get_command(payload)
    if not command:
        return Action()

    # Tokenize safely to avoid matching words inside quoted strings
    try:
        tokens = shlex.split(command)
    except Exception:
        tokens = command.split()

    # Helper: exact command/token present
    def has_token(name: str) -> bool:
        return any(tok == name for tok in tokens)

    # Helper: look for a bash for-loop structure outside quotes by stripping quoted content
    def _strip_quoted(s: str) -> str:
        # Remove single/double quoted chunks to avoid false positives
        return re.sub(r"(['\"]).*?\1", "", s)

    unquoted = _strip_quoted(command)

    # for-loop detection: 'for ... do' or C-style 'for ((...)) do'
    if re.search(r"(^|[;&|])\s*for(\s|\(\()", unquoted) and re.search(r"\bdo\b", unquoted):
        return Action(add_context=f"💡 **Tool suggestion:** Use `Glob` or `Grep` tools instead of bash loops\n   → `{command}`")

    # Check if command uses pipes or chaining - these are legitimate bash scenarios
    has_pipes_or_chaining = any(op in command for op in ("|", "&&", "||", ";"))

    # Search command violations (should use Grep/Glob tools instead)
    # Skip warnings for piped/chained commands as tools don't support those patterns
    if not has_pipes_or_chaining:
        if has_token("find"):
            return Action(add_context=f"💡 **Tool suggestion:** Use `Glob` tool instead of `find` for file searches\n   → `{command}`")
        if any(has_token(n) for n in ("grep", "egrep", "fgrep", "rg")):
            return Action(add_context=f"💡 **Tool suggestion:** Use `Grep` tool instead of `{tokens[0]}`\n   → `{command}`")

    # Read command guidance (only when used alone against explicit file args)
    # If command includes pipes or redirections, skip read hints
    if any(ch in command for ch in ("|", ">", "<", "&&", "||", ";")):
        pass
    else:
        def _has_file_operand(start_index: int) -> bool:
            # Look ahead for first non-option token that isn't '-' (stdin)
            for tok in tokens[start_index + 1:]:
                if tok == "--":
                    continue
                if tok.startswith("-"):
                    # for head/tail numeric flags, continue
                    continue
                return True
            return False

        if tokens and tokens[0] == "cat" and _has_file_operand(0):
            return Action(add_context=f"💡 **Tool suggestion:** Use `Read` tool instead of `cat` for reading files\n   → `{command}`")
        if tokens and tokens[0] == "head" and _has_file_operand(0):
            return Action(add_context=f"💡 **Tool suggestion:** Use `Read` tool instead of `head` (supports offset/limit)\n   → `{command}`")
        if tokens and tokens[0] == "tail" and _has_file_operand(0):
            return Action(add_context=f"💡 **Tool suggestion:** Use `Read` tool instead of `tail` (use offset parameter)\n   → `{command}`")
        if tokens and tokens[0] == "ls":
            # warn only if a path operand is provided (not just flags or no args)
            if _has_file_operand(0):
                return Action(add_context=f"💡 **Tool suggestion:** Use `Glob` tool instead of `ls` for file listing\n   → `{command}`")

    # Git interactive command violations (system prompt anti-pattern)
    # Simple token scan for interactive git modes
    if tokens and tokens[0] == "git" and "-i" in tokens:
        return Action(add_context=f"⚠️ **Not supported:** Git interactive mode (`-i` flag) cannot be used\n   → `{command}`", severity="warn", category="unsupported")
    if tokens[:2] == ["git", "rebase"] and "-i" in tokens:
        return Action(add_context=f"⚠️ **Not supported:** `git rebase -i` requires interactive input\n   → `{command}`", severity="warn", category="unsupported")
    if tokens[:2] == ["git", "add"] and "-i" in tokens:
        return Action(add_context=f"⚠️ **Not supported:** `git add -i` requires interactive input\n   → `{command}`", severity="warn", category="unsupported")

    return Action()
