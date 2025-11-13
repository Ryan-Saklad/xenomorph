"""
Minimal, strict hook runner for Claude Code hooks.

Usage:
  xenohooks --config /path/to/hooks.yml < input.json

Reads JSON hook input on stdin, routes by hook_event_name, selects tasks from
the provided config, runs them in parallel with timeouts, and only prints a
deny decision when any task blocks for events that support decisions.
"""

import json
import sys
import importlib.metadata as ilmd
import importlib.resources as ilres
from pathlib import Path
from typing import Any

from xenohooks.common.config import load_config
from xenohooks.common.filematch import collect_changed_files, collect_changed_files_from_payload
from xenohooks.common.runner import run_parallel
from xenohooks.common.selectors import select_tasks
from xenohooks.common.types import Action
from xenohooks.common.feedback import record_feedback, should_show_feedback, mark_shown, cleanup_session
from xenohooks.common.task_queue import import_from_incoming, cleanup_session as cleanup_task_session
from xenohooks.common.task_runner import spawn_tasks, check_running_tasks, process_completed_tasks


# Note: We exclusively use structured JSON outputs. Exit codes are 0 unless
# the process itself fails (invalid input). This avoids mixed signaling.


def main() -> None:
    # Parse CLI flags
    config_paths: list[str] | None = None
    check_config: bool = False
    queue_task: bool = False
    queue_session: str = ""
    queue_source: str = "cli"
    queue_timeout: int = 120
    queue_command: list[str] = []
    args = list(sys.argv[1:])

    def _print_help() -> None:
        msg = (
            "xenohooks — Claude Code hooks runner\n\n"
            "Usage: xenohooks [--config SOURCE] < input.json\n"
            "       xenohooks --queue-task [OPTIONS] -- COMMAND...\n\n"
            "Options:\n"
            "  --config PATH|example:NAME   Load hooks config (repeat or comma-list)\n"
            "  --check-config               Print resolved merged config and exit\n"
            "  --list-examples              List packaged example configs and exit\n"
            "  --queue-task                 Queue a background task and exit\n"
            "  --session ID                 Session ID for task queue (with --queue-task)\n"
            "  --source NAME                Source identifier (default: cli)\n"
            "  --timeout SECONDS            Task timeout in seconds (default: 120)\n"
            "  -V, --version                Print version and exit\n"
            "  -h, --help                   Show this help and exit\n"
        )
        print(msg)

    i = 0
    while i < len(args):
        tok = args[i]
        match tok:
            case "-h" | "--help":
                _print_help()
                return
            case "-V" | "--version":
                try:
                    ver = ilmd.version("xenomorph")
                except Exception:
                    ver = "unknown"
                print(ver)
                return
            case "--list-examples":
                try:
                    base = ilres.files("xenohooks.examples")
                    names = []
                    for p in base.iterdir():
                        n = p.name
                        if n.endswith((".yml", ".yaml")):
                            names.append(n.rsplit(".", 1)[0])
                    for name in sorted(set(names)):
                        print(name)
                except Exception as e:
                    print(f"error listing examples: {e}")
                return
            case "--check-config":
                check_config = True
                i += 1
            case "--config":
                if i + 1 >= len(args):
                    raise SystemExit("xenohooks: --config requires a path argument")
                val = args[i + 1]
                config_paths = (config_paths or []) + [val]
                i += 2
            case "--queue-task":
                queue_task = True
                i += 1
            case "--session":
                if i + 1 >= len(args):
                    raise SystemExit("xenohooks: --session requires an ID argument")
                queue_session = args[i + 1]
                i += 2
            case "--source":
                if i + 1 >= len(args):
                    raise SystemExit("xenohooks: --source requires a name argument")
                queue_source = args[i + 1]
                i += 2
            case "--timeout":
                if i + 1 >= len(args):
                    raise SystemExit("xenohooks: --timeout requires a seconds argument")
                try:
                    queue_timeout = int(args[i + 1])
                except ValueError:
                    raise SystemExit(f"xenohooks: --timeout must be an integer, got: {args[i + 1]}")
                i += 2
            case "--":
                # Everything after -- is the command
                queue_command = args[i + 1:]
                break
            case _:
                i += 1

    # --check-config can run without stdin
    if check_config:
        try:
            cfg = load_config(config_paths)
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"xenohooks: {e}")
        return

    # --queue-task can run without stdin
    if queue_task:
        from xenohooks.common.task_queue import queue_task as queue_task_fn

        if not queue_command:
            raise SystemExit("xenohooks: --queue-task requires a command after --")

        try:
            task_id = queue_task_fn(
                command=queue_command,
                session_id=queue_session,
                source=queue_source,
                task_type="external",
                timeout=queue_timeout
            )
            print(f"Queued task: {task_id}")
            print(f"Session: {queue_session or 'default'}")
            print(f"Command: {' '.join(queue_command)}")
        except Exception as e:
            raise SystemExit(f"xenohooks: failed to queue task: {e}")
        return

    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("xenohooks: missing JSON hook input on stdin")
    try:
        payload: dict[str, Any] = json.loads(raw)
    except Exception as e:
        raise SystemExit(f"xenohooks: invalid JSON on stdin: {e}")
    if not isinstance(payload, dict):
        raise SystemExit("xenohooks: JSON input must be an object")

    event = payload.get("hook_event_name")
    if not isinstance(event, str) or not event:
        raise SystemExit("xenohooks: missing 'hook_event_name' in input")

    # Early guard: detect project-root namespace collisions that can shadow this package
    # Only check on SessionStart to avoid noisy mid-session interruptions
    if event == "SessionStart":
        try:
            cwd = Path(str(payload.get("cwd") or ".")).resolve()
        except Exception:
            cwd = Path.cwd()
        offenders: list[str] = []
        for name in ("xenohooks", "xenomorph"):
            p = cwd / name
            try:
                if p.exists() and p.is_dir():
                    offenders.append(name)
            except Exception:
                continue
        if offenders:
            msg = (
                "Reserved package namespace found at project root: "
                + ", ".join(offenders)
                + ".\nThese folders can shadow the installed xenohooks package and cause import/runtime issues.\n"
                "Please rename or move these directories out of the project root."
            )
            payload_out = {
                "continue": False,
                "stopReason": msg,
                "systemMessage": msg,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": msg,
                },
            }
            print(json.dumps(payload_out))
            return

    try:
        cfg = load_config(config_paths)
    except Exception as e:
        # No config found or other load error → stop the session with a clear message
        msg = f"xenohooks: {e}"
        payload_out = {
            "continue": False,
            "stopReason": msg,
            "systemMessage": msg,
            "hookSpecificOutput": {
                "hookEventName": str(event),
                "additionalContext": msg,
            },
        }
        print(json.dumps(payload_out))
        return
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    files = collect_changed_files_from_payload(payload) if event == "PostToolUse" else []

    # Augment payload for tasks/runner
    payload["files"] = files
    payload["config"] = cfg
    payload["timeout_seconds"] = int(cfg.get("default_timeout", 12))

    # Select tasks for event via unified selector
    tasks = select_tasks(event, cfg=cfg, tool_name=tool_name, files=files)

    if not tasks:
        # Always emit a well-formed JSON response so hosts don't treat output as plain text
        print(json.dumps({"continue": True}))
        return

    results = run_parallel(tasks, payload)

    # Process background tasks
    session_id = str(payload.get("session_id", ""))
    cwd = str(payload.get("cwd", "."))

    # Import any externally queued tasks
    import_from_incoming(session_id)

    # Spawn pending tasks if slots available
    spawn_tasks(session_id, max_concurrent=2, cwd=cwd)

    # Check running tasks and update their status
    check_running_tasks(session_id)

    # Process completed tasks and convert to feedback
    completed_feedback = process_completed_tasks(session_id)
    for feedback_data in completed_feedback:
        # Convert feedback to Action and add to results
        action = Action(
            add_context=feedback_data.get("content", ""),
            severity=feedback_data.get("severity", "info"),
            category=feedback_data.get("category", "background-task"),
            task_id=feedback_data.get("task_id", "background"),
        )
        results.append(action)

    # Normalize actions
    # Policy-based blocking (opt-in)
    policy = dict(cfg.get("policy", {})) if isinstance(cfg.get("policy"), dict) else {}
    block_on = policy.get("block_on") if isinstance(policy.get("block_on"), list) else []

    def _matches_policy(a: Action) -> bool:
        if not block_on:
            return False
        sev = (a.severity or "").lower()
        cat = (a.category or "").lower()
        for rule in block_on:
            try:
                s = str(rule).lower().strip()
            except Exception:
                continue
            if not s:
                continue
            if ":" in s:
                rcat, rsev = s.split(":", 1)
                if (not rcat or rcat == cat) and (not rsev or rsev == sev):
                    return True
            else:
                if s in {"info", "warn", "error"}:
                    if sev == s:
                        return True
                elif s == cat:
                    return True
        return False

    blocks = [a for a in results if a.block or _matches_policy(a)]
    adds = [a for a in results if (a.add_context and not a.block and not _matches_policy(a))]
    pre_decisions = [a for a in results if a.permission_decision]
    enders = [a for a in results if a.end_turn]
    suppress_output = any(bool(getattr(a, "suppress_output", False)) for a in results)
    sys_msgs = [str(getattr(a, "system_message", "")).strip() for a in results if isinstance(getattr(a, "system_message", None), str) and str(getattr(a, "system_message", "")).strip()]

    # Apply deduplication for PostToolUse to prevent redundant messages on sequential edits
    if event == "PostToolUse":
        session_id = str(payload.get("session_id", ""))
        deduped_adds: list[Action] = []
        for a in adds:
            if not a.add_context:
                continue
            # Record feedback and check if should show
            item = record_feedback(
                content=a.add_context,
                task_id=a.task_id or "unknown",
                session_id=session_id,
                file_path=getattr(a, "file_path", None),
                severity=a.severity,
                category=a.category,
                strategy="show_once"
            )
            if should_show_feedback(item):
                mark_shown(item.issue_id, session_id)
                deduped_adds.append(a)
        adds = deduped_adds

    # Exit codes are not used for decision signaling; keep at 0

    def _truncate_context(text: str, max_lines: int = 50) -> str:
        """Truncate overly verbose context to prevent spam.

        Safety net to catch any hook that outputs too many lines.
        """
        if not text:
            return text

        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text

        truncated = "\n".join(lines[:max_lines])
        truncated += f"\n\n... ({len(lines) - max_lines} more lines truncated for brevity)"
        return truncated

    def _reason_from_blocks(event_name: str) -> str:
        """Build detailed reason from blocking actions.

        For PostToolUse: Uses informative language since operation already succeeded.
        For PreToolUse: Uses blocking language since operation is being prevented.
        """
        # First, try to collect detailed reasons or contexts
        texts: list[str] = []
        for a in blocks:
            # Prefer explicit reason
            if isinstance(a.reason, str) and a.reason.strip():
                texts.append(a.reason.strip())
            # Fall back to add_context for detailed info (common with policy-based blocks)
            elif isinstance(a.add_context, str) and a.add_context.strip():
                texts.append(a.add_context.strip())

        if texts:
            detailed = "\n\n".join(texts)
            detailed = _truncate_context(detailed)  # Apply truncation safety net
            # Use appropriate framing based on event
            if event_name == "PostToolUse":
                return f"Issues found:\n{detailed}"
            else:
                return detailed

        # Final fallback: task identifiers
        ids = [a.task_id for a in blocks if a.task_id]
        if ids:
            if event_name == "PostToolUse":
                return f"Issues found in: {', '.join(ids)}"
            else:
                return f"Blocked by: {', '.join(ids)}"
        return "Issues found" if event_name == "PostToolUse" else "Blocked"

    def _merged_context() -> str:
        lines = [a.add_context for a in adds if isinstance(a.add_context, str) and a.add_context.strip()]
        merged = "\n".join(lines)
        return _truncate_context(merged)

    # Build output payload once
    payload_out: dict[str, Any]
    exit_code: int = 0

    # Map to schema using a single event switch
    match event:
        case "PreToolUse":
            if blocks:
                reason = _reason_from_blocks(event)
                payload_out = {
                    "continue": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    },
                }
            else:
                payload_out = {"continue": True}
                if pre_decisions:
                    d = pre_decisions[0]
                    payload_out["hookSpecificOutput"] = {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": d.permission_decision,
                        "permissionDecisionReason": d.permission_decision_reason or "",
                    }
                else:
                    ctx = _merged_context()
                    if ctx:
                        payload_out["hookSpecificOutput"] = {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "permissionDecisionReason": ctx,
                        }
                if enders:
                    reason = next((a.reason for a in enders if a.reason), "Requested stop")
                    payload_out = {"continue": False, "stopReason": reason}

        case "PostToolUse":
            if blocks:
                payload_out = {"decision": "block", "reason": _reason_from_blocks(event)}
            else:
                payload_out = {"continue": True}
                ctx = _merged_context()
                if ctx:
                    payload_out["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "additionalContext": ctx}

        case "UserPromptSubmit":
            if blocks:
                payload_out = {"decision": "block", "reason": _reason_from_blocks(event)}
            else:
                payload_out = {"continue": True}
                ctx = _merged_context()
                if ctx:
                    payload_out["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}

        case "Stop" | "SubagentStop" | "PreCompact":
            if blocks:
                payload_out = {"decision": "block", "reason": _reason_from_blocks(event)}
            else:
                payload_out = {"continue": True}
                ctx = _merged_context()
                if ctx:
                    payload_out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": ctx}

        case "SessionStart":
            if blocks:
                payload_out = {"continue": False, "stopReason": _reason_from_blocks(event)}
            else:
                payload_out = {"continue": True}
                ctx = _merged_context()
                if ctx:
                    payload_out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": ctx}
                if enders:
                    reason = next((a.reason for a in enders if a.reason), "Requested stop")
                    payload_out = {"continue": False, "stopReason": reason}

        case "SessionEnd" | "Notification":
            # Cannot block SessionEnd; Notification is info-only
            payload_out = {"continue": True}
            ctx = _merged_context()
            if ctx:
                payload_out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": ctx}

            # Clean up session feedback and tasks on SessionEnd
            if event == "SessionEnd":
                session_id = str(payload.get("session_id", ""))
                cleanup_session(session_id)
                cleanup_task_session(session_id)
        case _:
            payload_out = {"continue": True}

    # Attach common fields if present
    if suppress_output:
        payload_out["suppressOutput"] = True
    if sys_msgs:
        payload_out["systemMessage"] = "\n".join(sys_msgs)

    # Single print only
    print(json.dumps(payload_out))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
