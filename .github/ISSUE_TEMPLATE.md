# Feature Request: Background Task Support & Feedback Queue

## Problem
Hooks are currently stateless and synchronous, which causes several issues:
1. **Redundant messages**: Sequential edits to the same file produce identical warnings
2. **Blocking expensive checks**: Long-running checks (pip-audit, comprehensive tests) block the edit loop
3. **No progressive disclosure**: Can't show summary on repeat ("3 previous warnings still apply")
4. **No deferred feedback**: Can't accumulate non-critical issues and present them at appropriate times

## Proposed Solution

### Background Task Queue
Support for asynchronous background tasks that don't block the hook execution:

```python
# Hook can spawn background task
return Action(
    background_task={
        "command": "uv run pip-audit --format json",
        "timeout": 120,
        "on_complete": "queue_for_next_hook"  # or "show_immediately"
    }
)
```

When completed, results are queued and injected into the next hook execution of the same event type.

### Feedback Management System
Replace `dedupe.py` with `feedback.py` that supports:

1. **Stable IDs**: Each feedback item has a stable ID based on:
   - Task ID (e.g., "python-ruff")
   - File path (if applicable)
   - Issue type/hash

2. **Presentation Strategies**:
   ```python
   FeedbackItem(
       id="ruff:app.py:F841",
       content="Line 45: F841 unused variable 'data'",
       strategy="show_once",  # or "always", "summary_after_first", "defer_until_commit"
       severity="error",
       category="lint",
       first_seen=timestamp,
       last_seen=timestamp,
       occurrence_count=3
   )
   ```

3. **Query Interface**:
   ```python
   # Get pending feedback for this session
   feedback = get_pending_feedback(session_id)

   # Mark feedback as shown
   mark_shown(feedback_id)

   # Get summary of repeated issues
   summary = get_feedback_summary(session_id, min_occurrences=2)
   ```

4. **Progressive Disclosure**:
   - First occurrence: Show full message
   - Subsequent: "3 previous Ruff warnings still apply (use /show-feedback to view)"
   - At commit: Show accumulated deferred issues

## Use Cases

### 1. Expensive Background Checks
```yaml
PostToolUse:
  tasks:
    - ref: xenohooks.python.pip_audit
      background: true  # Run async, queue results
      trigger: once_per_session  # Don't run on every edit
```

### 2. Progressive Disclosure
```python
# First edit to app.py
❌ **Ruff:** 1 file with issues
   app.py:
     Line 45:12: F841 unused variable 'data'

# Second edit to app.py (same issue)
⚠️ **Previous warnings:** Ruff issues in app.py (unchanged)
   💡 Run `/show-feedback ruff` to review
```

### 3. Deferred Non-Critical Issues
```python
# Design tokens don't show during edits
# But at commit:
💡 **Deferred feedback:**
   • Design tokens: 104 opportunities in 3 files
   • Code style: 2 suggestions
```

## Implementation Considerations

### Storage
- Session-scoped: `~/.cache/xenohooks/feedback/{session_id}/`
- Files: `items.json` (all feedback), `shown.json` (what's been displayed)
- Auto-cleanup on SessionEnd or after N days

### ID Generation
Two-level system:
1. **Instance ID**: Content hash (for exact deduplication)
2. **Issue ID**: `{task_id}:{file}:{issue_type}` (for tracking same issue over time)

### Background Task Runner
Options:
1. Simple: Fork process, write results to file, router checks on next invocation
2. Advanced: Small daemon that manages task pool and can push results
3. Hybrid: Fork with file-based result storage (no daemon needed)

### Configuration
```yaml
feedback:
  deduplication_window: 300  # 5 minutes
  strategies:
    lint:error: show_once
    lint:warn: summary_after_first
    quality:info: defer_until_commit
  background_tasks:
    enabled: true
    max_concurrent: 2
```

## Benefits
- Eliminates alert fatigue from redundant messages
- Allows expensive checks without blocking edit loop
- Better UX with progressive disclosure
- Foundation for more sophisticated feedback management
- Cleaner separation between "urgent now" vs "fix eventually"

## Migration Path
1. Phase 1: Implement feedback storage/retrieval (current `dedupe.py` → `feedback.py`)
2. Phase 2: Add progressive disclosure for existing hooks
3. Phase 3: Add background task runner
4. Phase 4: Add deferred feedback collection and presentation

## Questions
- Should feedback IDs be UUIDs or content-derived hashes?
- Should background tasks have access to file change history?
- How to handle feedback for files that no longer exist?
- Should we support cross-session feedback (e.g., project-wide technical debt tracking)?