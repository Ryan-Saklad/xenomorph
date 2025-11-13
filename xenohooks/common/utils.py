"""
Small helpers for extracting common fields from hook payloads.
"""

from typing import Any


def get_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    v = payload.get("tool_input")
    return v if isinstance(v, dict) else {}


def get_command(payload: dict[str, Any]) -> str:
    tool_input = get_tool_input(payload)
    v = tool_input.get("command")
    return v if isinstance(v, str) else ""
