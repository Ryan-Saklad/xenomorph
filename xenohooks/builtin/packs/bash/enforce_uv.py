"""
UV usage enforcement task (ported to dict payload API).

Blocks commands that use raw 'python', 'python3', 'pip', or manual venv activation.
Suggests uv equivalents where possible.
"""

import re
from typing import Any

from xenohooks.common.types import Action
from xenohooks.common.utils import get_command


def run(payload: dict[str, Any]) -> Action:
    """Enforce UV usage instead of other Python package managers."""
    # Only analyze Bash tool invocations
    if str(payload.get("tool_name") or "") != "Bash":
        return Action()

    command = get_command(payload)
    if not command:
        return Action()

    # pip → uv pip
    if re.search(r"\bpip\s+", command) and not re.search(r"\buv\s+pip\s+", command):
        suggested = re.sub(r"\bpip\s+", "uv pip ", command)
        return Action(block=True, reason=f"Use 'uv pip' instead of 'pip'. Try: {suggested}")

    # python3 → uv run
    if re.search(r"(^|[;&|]\s*)python3\s+", command):
        suggested = re.sub(r"(^|\s+)python3\s+", r"\1uv run ", command)
        return Action(block=True, reason=f"Use 'uv run' instead of 'python3'. Try: {suggested}")

    # python → uv run
    if re.search(r"(^|[;&|]\s*)python\s+", command):
        suggested = re.sub(r"(^|\s+)python\s+", r"\1uv run ", command)
        return Action(block=True, reason=f"Use 'uv run' instead of 'python'. Try: {suggested}")

    # manual venv activation → uv run
    if re.search(r"(source|\.)\s+(\.venv|venv)/bin/activate\b", command):
        return Action(block=True, reason="Don't activate venv manually. Use 'uv run' and uv manages the environment.")

    return Action()
