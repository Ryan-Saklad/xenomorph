"""
Default branch protection.
Blocks direct pushes/merges to protected branches.
"""

import os
import re
from typing import Any

from xenohooks.common.types import Action
from xenohooks.common.utils import get_command


def _protected_branches(payload: dict[str, Any]) -> list[str]:
    # Params override (preferred), then env, then defaults
    t = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    params = t.get("params") if isinstance(t, dict) else None
    if isinstance(params, dict):
        branches = params.get("branches") or params.get("protected_branches")
        if isinstance(branches, list):
            vals = [str(b).strip() for b in branches]
            vals = [v for v in vals if v]
            if vals:
                return vals
    # Env overrides (comma-separated) or default common names
    env = os.environ.get("PROTECTED_BRANCHES") or os.environ.get("GIT_DEFAULT_BRANCH")
    if env:
        items = [s.strip() for s in env.split(",") if s.strip()]
        if items:
            return items
    return ["main", "master"]


def run(payload: dict[str, Any]) -> Action:
    if str(payload.get("tool_name") or "") != "Bash":
        return Action()

    command = get_command(payload)
    if not command:
        return Action()

    branches = _protected_branches(payload)
    br_pat = "|".join(re.escape(b) for b in branches)
    dangerous_patterns: list[tuple[str, str]] = [
        (rf"\bgit\s+push\s+(?:origin\s+)?(?:{br_pat})\b", "Direct push to a protected branch is not allowed"),
        (rf"\bgit\s+push\s+(?:-f|--force)\s+(?:origin\s+)?(?:{br_pat})\b", "Force push to a protected branch is not allowed"),
        (rf"\bgit\s+push\s+(?:origin\s+)?[^:\s]*:(?:{br_pat})\b", "Pushing to a protected branch is not allowed"),
        (rf"\bgit\s+checkout\s+(?:{br_pat})\s*&&.*(?:merge|push)", "Switching to a protected branch and immediately merging/pushing is not allowed"),
    ]

    for pattern, message in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            guidance = (
                f"{message}\n\nCommand: {command}\n\n"
                "💡 Use a feature branch workflow:\n"
                "   1. git checkout -b feature/your-feature\n"
                "   2. Make changes and commit\n"
                "   3. git push origin feature/your-feature\n"
                "   4. Open a pull request for review"
            )
            return Action(block=True, reason=guidance)

    return Action()
