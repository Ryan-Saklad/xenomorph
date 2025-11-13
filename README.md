Xenomorph
=========

Xenomorph is a toolkit for LLM‑driven development. This repository currently provides the hooks runner and task packs for Claude Code.

- `xenomorph`: the Python distribution name
- `xenohooks`: the Python package and CLI implementing Claude Code hooks

Quick Start
-----------

1) Install (Python >= 3.10):

    pip install xenomorph

2) Wire into Claude Code settings (project-local `.claude/settings.json`):

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "*",
            "hooks": [
              { "type": "command", "command": "uv run xenohooks" }
            ]
          }
        ]
      }
    }

3) Configuration discovery: `xenohooks` looks for a hooks config in this order:

- `$CLAUDE_HOOKS_CONFIG` (explicit path)
- `./hooks.yml|yaml` or `./config/hooks.yml|yaml`
- `./.claude/hooks/config/hooks.yml`
- `./hooks.json`
- If no config is found, `xenohooks` exits with guidance to provide one

Tasks & Config
--------------

Tasks are small Python callables that accept a dict payload and return an `Action` (or list of `Action`s). Configure tasks under event sections. Example (YAML):

    PostToolUse:
      tasks:
        - ref: xenohooks.bash.hygiene
          id: bash-hygiene
          tools: [Bash]
          timeout: 2

You can pass task-specific parameters via `params` (available at `payload.task.params`):

    PostToolUse:
      tasks:
        - ref: xenohooks.vcs.protect_branch
          id: protect-branch
          tools: [Bash]
          params: { branches: ["main", "release/*"] }

Selection filters:

- `tools`: only run when the hook payload’s `tool_name` matches
- `file_types`: only run when changed files include an extension

Action contract (subset):

- `add_context: str` – appends context for Claude to consider
- `block: bool` – request to block (use sparingly)
- `permission_decision: allow|deny|ask` – PreToolUse decision
- `severity: info|warn|error` and `category: str` – optional policy metadata

Extensibility (Plugins)
-----------------------

You can reference tasks by module path (`package.module:run`) or via entry points. To ship a task plugin, expose an entry point group `xenohooks.tasks` in your package:

    # pyproject.toml of your-plugin
    [project.entry-points."xenohooks.tasks"]
    my_cool_task = "your_plugin.tasks:run"

Then in hooks config, refer to `ref: my_cool_task` and `xenohooks` will resolve it via entry points.

Policy Model
------------

Default policy is "quiet when OK" and non-blocking. You can opt in to policy-based blocking via `policy.block_on` rules:

    policy:
      block_on:
        - error            # any action with severity=error
        - security:error   # category=security AND severity=error
        - lint             # any action with category=lint

Router merges task results and applies block_on rules. If nothing matches, it emits `{ "continue": true }`.

Output Contract
---------------

`xenohooks` always prints a single JSON object to stdout. By event:

- PreToolUse:
  - Deny (policy/task-triggered):
    - `{ "continue": true, "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..." } }`
  - Allow + optional guidance:
    - `{ "continue": true }` or with context via `permissionDecisionReason`/`allow` when present
  - End session (rare): `{ "continue": false, "stopReason": "..." }`

- PostToolUse / UserPromptSubmit / Stop / SubagentStop / PreCompact:
  - Quiet success: `{ "continue": true }`
  - With guidance: `{ "continue": true, "hookSpecificOutput": { "hookEventName": "<Event>", "additionalContext": "..." } }`

- SessionStart:
  - Quiet success or guidance (like above)
  - On fatal guardrails (e.g., reserved namespace folders): `{ "continue": false, "stopReason": "...", "hookSpecificOutput": { "hookEventName": "SessionStart", "additionalContext": "..." } }`

Notes:
- `additionalContext` is appended to Claude’s working context; be concise.
- We support documented common fields for compatibility.
 
Common Fields (all events):
- `continue: boolean` – default true; when false, Claude stops after the hook.
- `stopReason: string` – user-facing reason accompanying `continue: false`.
- `suppressOutput: boolean` – hide stdout/stderr from transcript mode.
- `systemMessage: string` – optional user-visible warning/information.

Background Tasks
----------------

Xenohooks includes a background task system that lets long-running work happen outside of the synchronous hook cycle. Queued tasks run alongside regular hooks so they do not delay the current event.

**Key Features:**

- **SQLite-based queue**: Reliable storage in `~/.cache/xenohooks/tasks/{session}/`
- **On-demand execution**: Tasks spawn automatically on next hook (no daemon required)
- **External registration**: Multiple integration methods for any tool or script
- **Process monitoring**: PID tracking with output capture
- **Session-scoped**: Automatic cleanup on SessionEnd

**Usage Methods:**

1. **JSON Drop-in** (lowest barrier):
   ```bash
   cat > ~/.cache/xenohooks/tasks/$SESSION_ID/incoming/$(uuidgen).json <<EOF
   {
     "command": ["pip-audit", "--format", "json"],
     "source": "cron-scanner",
     "timeout": 120
   }
   EOF
   ```

2. **CLI Command**:
   ```bash
   xenohooks --queue-task --session $SESSION_ID --source "my-scanner" --timeout 180 -- pip-audit --format json
   ```

3. **Python API**:
   ```python
   from xenohooks.common.task_queue import queue_task

   task_id = queue_task(
       command=["pip-audit", "--format", "json"],
       session_id=session_id,
       source="my-scanner",
       timeout=120
   )
   ```

**Task Output Format:**

Tasks can output structured JSON for rich feedback:

```json
{
  "feedback": [
    {
      "content": "Found 3 vulnerabilities",
      "severity": "warn",
      "category": "security"
    }
  ]
}
```

Or plain text, which will be shown as-is. Results automatically integrate with the feedback deduplication system.

Built‑in Tasks
--------------

The repo includes a handful of example tasks (Bash hygiene, Dockerfile check, ReScript helpers, Python linters, secrets scanning, etc.). These are meant as reference implementations and can be enabled/disabled via config. External tools are optional; tasks degrade gracefully when a tool is missing.

Design Principles
-----------------

- **Background principle**: Silent on success; only surface actionable guidance
- **Feedback deduplication**: Prevent alert fatigue by tracking shown messages per session
- **PostToolUse guidance over hard gates**: Unless a change would be irreversible
- **Small, composable tasks**: Parallel execution with per-task timeouts
- **Extensible via Python entry points**: Plugin system for custom tasks
- **Graceful degradation**: External tools are optional; tasks handle missing dependencies

CLI Reference
-------------

**Hook Execution** (reads JSON from stdin):
```bash
echo '{"hook_event_name":"SessionStart","cwd":"$PWD"}' | xenohooks [OPTIONS]
```

**Background Task Queueing**:
```bash
xenohooks --queue-task [OPTIONS] -- COMMAND...
```

**Options:**
- `--config PATH|example:NAME` - Load hooks config (can repeat or use comma-list)
- `--check-config` - Print resolved merged config and exit
- `--list-examples` - List packaged example configs and exit
- `--queue-task` - Queue a background task and exit
- `--session ID` - Session ID for task queue (with --queue-task)
- `--source NAME` - Source identifier for task (default: cli)
- `--timeout SECONDS` - Task timeout in seconds (default: 120)
- `-V, --version` - Print version and exit
- `-h, --help` - Show help and exit

Development
-----------

Run locally:

    echo '{"hook_event_name":"SessionStart","cwd":"$PWD"}' | uv run xenohooks

Format / lint / test: see `pyproject.toml` and add your tooling of choice. Tests should stub STDIN inputs and assert the JSON output.

Reserved Names
--------------

- Avoid folders named `xenohooks` or `xenomorph` at your project root. They can shadow the installed package and cause import/runtime issues. The CLI checks this on `SessionStart` and stops with a clear message if detected.

Compatibility
-------------

- Python >= 3.10 (uses pattern matching and modern type hints)
- On Python < 3.11 we require `tomli` for TOML parsing (added automatically)

Examples
--------

Packaged example configs make it easy to try different rule sets. Use the special `example:<name>` syntax with `--config`:

- Minimal: `xenohooks --config example:minimal`
- Python: `xenohooks --config example:python`
- Frontend: `xenohooks --config example:frontend`
- ReScript: `xenohooks --config example:rescript`
- Docker: `xenohooks --config example:docker`
- Security: `xenohooks --config example:security`
- All Packs: `xenohooks --config example:all-packs`

You can also point to a project file with `--config /path/to/hooks.yml`.

Local Config
------------

You can load your own hooks config in several ways:

- Auto‑discovery (no flags needed):
  - `./hooks.yml` or `./hooks.yaml`
  - `./config/hooks.yml` or `./config/hooks.yaml`
  - `./.claude/hooks/config/hooks.yml`
  - `./hooks.json`

- Explicit path via CLI:
  - `uv run xenohooks --config ./hooks.yml`
  - Compose multiple sources left→right: `uv run xenohooks --config ./hooks.yml,example:security`

- Environment variable:
  - `CLAUDE_HOOKS_CONFIG=/abs/path/hooks.yml uv run xenohooks`

Claude Code settings example (local file):

```
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "uv run xenohooks --config ./hooks.yml" }
        ]
      }
    ]
  }
}
```

Composing Configs
-----------------

You can compose multiple configs via:

- Top‑level `extends` inside a config (left→right):

  ```yaml
  extends: [example:python, example:security, ./hooks.local.yml]
  ```

  Relative paths resolve from the file that declares `extends`. Examples can also extend other examples.

- Multiple `--config` flags or comma list (left→right):

  `xenohooks --config example:python --config example:security`

  `xenohooks --config example:python,example:security`

Merge rules:

- Scalars (`concurrency`, `default_timeout`): last‑wins
- `policy`: deep‑merge; `block_on` concatenates with de‑dupe
- Events (e.g., `PostToolUse`): append `tasks`; de‑dupe by `id` (or by `ref` if no `id`) with last‑wins
- Overrides: set `disabled: true` on a task entry, or use `remove: [id, ...]` at the event section

Roadmap
-------

The following are not implemented yet and are tracked as roadmap items:
- Sub‑agents: lightweight task runners and agent orchestration beyond hooks
- MCP servers: packaged servers and client helpers for Model Context Protocol
- RAG utilities: local indexers/retrievers and prompt routing helpers
- Rule packs: curated, pluggable packs (python, frontend, docker, rescript, security)
- Plugin manager: list/enable/disable packs from the CLI
- Config schema: JSON Schema + validation helpers and rich error messages
- Test harness: payload fixtures and snapshot testing for rules/packs

License
-------

MIT. See LICENSE.
