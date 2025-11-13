"""
File classification and extraction helpers for hook payloads.
"""

from typing import Any

def collect_changed_files(tool_input: dict[str, Any]) -> list[str]:
    """
    Extract a normalized list of file paths from common shapes in tool_input.
    Supported keys: "file_path", "file_paths", "files", "edits": [{"path": ...}].
    De-duplicates while preserving order.
    """
    paths: list[str] = []

    # Singular
    v = tool_input.get("file_path")
    if isinstance(v, str) and v:
        paths.append(v)

    # Plurals
    for key in ("file_paths", "files"):
        vv = tool_input.get(key)
        if isinstance(vv, list):
            for item in vv:
                if isinstance(item, str) and item:
                    paths.append(item)

    # Edits payloads
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                p = e.get("path") or e.get("file_path")
                if isinstance(p, str) and p:
                    paths.append(p)

    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def collect_changed_files_from_payload(payload: dict[str, Any]) -> list[str]:
    """
    Extract file paths from both tool_input and tool_response.

    Recognizes keys:
    - snake_case: file_path, file_paths
    - lists: files
    - edits: [{ path | file_path }]
    - camelCase: filePath, filePaths (commonly in tool_response)

    De-duplicates while preserving order.
    """
    paths: list[str] = []

    def _pull(d: dict[str, Any]) -> None:
        if not isinstance(d, dict):
            return
        # Normalize camelCase to existing expectations
        norm: dict[str, Any] = dict(d)
        if "filePath" in norm and "file_path" not in norm:
            norm["file_path"] = norm.get("filePath")
        if "filePaths" in norm and "file_paths" not in norm:
            norm["file_paths"] = norm.get("filePaths")
        for p in collect_changed_files(norm):
            paths.append(p)

    _pull(payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {})
    _pull(payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {})

    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paths:
        if isinstance(p, str) and p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq
