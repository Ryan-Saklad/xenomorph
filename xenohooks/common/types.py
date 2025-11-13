"""
Shared types for Claude Hooks (Action-based contract).
"""

from dataclasses import dataclass
from typing import Any, Callable, Literal


# All events Claude Code can emit (expandable)
Event = Literal[
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
]

@dataclass(slots=True)
class Action:
    """
    Unified action returned by tasks; router maps these to Claude's schema.
    """
    block: bool = False
    reason: str | None = None
    end_turn: bool = False
    add_context: str | None = None
    permission_decision: Literal["allow", "deny", "ask"] | None = None
    permission_decision_reason: str | None = None
    user_message: str | None = None
    suppress_output: bool | None = None
    system_message: str | None = None
    exit2: bool | None = None
    file_path: str | None = None
    task_id: str | None = None
    elapsed_seconds: float | None = None
    # Optional policy metadata for routing/severity handling
    severity: Literal["info", "warn", "error"] | None = None
    category: str | None = None


TaskFn = Callable[[dict[str, Any]], Action | list[Action]]


@dataclass(slots=True, frozen=True)
class TaskDescriptor:
    """
    A resolved task ready for execution.

    - id: stable identifier (usually "package.module:func")
    - fn: call target
    - timeout_seconds: overrides Context.timeout_seconds if set (>0)
    """
    id: str
    fn: TaskFn
    timeout_seconds: int | None = None
    params: dict[str, Any] | None = None


flatten_actions = lambda value: value if isinstance(value, list) else [value]
