---
name: self-healing
description: How to report unexpected errors for automated investigation via the report_error and get_healing_status MCP tools.
---

# Self-Healing Protocol

When you encounter an unexpected error that appears to be a code bug, report it using the `report_error` tool. This triggers automated investigation and a proposed fix via PR — no human intervention needed to start the loop.

---

## When to Report

**DO report** when:
- An MCP tool raises an unexpected exception (not a validation error on your input)
- A database query fails with an unexpected error (table missing, constraint violation, type mismatch)
- An API call fails in a way that suggests a bug in the integration code
- A data processing step produces an internal error you cannot recover from
- You see an error that recurs across multiple attempts with the same input pattern

**DO NOT report** when:
- The error is caused by invalid user input (that is expected behaviour)
- It is a transient network error or rate limit (retry first; report only if it persists)
- You can handle and recover from the error cleanly
- It is an `asyncio.CancelledError` or `KeyboardInterrupt` (these are intentional)
- The error is in an external service you do not control (report it to the user instead)

---

## How to Report

Call `report_error` with as much structured context as possible:

```python
report_error(
    error_type="asyncpg.exceptions.UndefinedTableError",   # required: fully qualified class name
    error_message="relation \"butler_name.missing_table\" does not exist",  # required: exact message
    traceback="Traceback (most recent call last):...",      # recommended: full traceback
    call_site="src/butlers/modules/memory/tools/facts.py:memory_store_fact",  # your best guess
    context="I was storing a new fact for the memory module. The table appears to be missing "
            "from the schema — likely a migration that was not applied.",
    tool_name="memory_store_fact",                         # which MCP tool raised the error
    severity_hint="high",                                  # critical/high/medium/low
)
```

### Parameter guidance

| Parameter | What to include |
|---|---|
| `error_type` | Fully qualified exception class name. Check `type(exc).__name__` and `type(exc).__module__`. |
| `error_message` | The exact exception message, unmodified. |
| `traceback` | The full traceback string. Paste it verbatim — the system sanitises dynamic values. |
| `call_site` | `<relative-file-path>:<function-name>` of where the error occurred. Omit line number. |
| `context` | Your analysis (see below). |
| `tool_name` | The MCP tool name if the error came from a specific tool call. |
| `severity_hint` | `critical` = data loss/security; `high` = broken functionality; `medium` = degraded behaviour; `low` = cosmetic/non-blocking. |

### Writing the context field

The `context` field is the most valuable input for the healing agent. Include:
- What operation you were performing and why
- What you expected to happen vs. what actually happened
- Relevant parameter patterns (describe types/shapes, NOT actual values)
- Any hypotheses about the root cause
- Whether the error is reproducible or intermittent

Keep it under 500 words. Focus on what a developer would need to know to reproduce and fix the bug.

---

## Data Safety

**CRITICAL: Never include user data in error reports.**

The healing agent creates a public GitHub PR. Any data you include may become public.

**Never include:**
- Actual user data values (names, emails, messages, calendar events, financial data)
- The content of any session prompt or user instructions
- Credentials, API keys, tokens, or passwords
- Personally identifiable information of any kind
- Database contents, user IDs that could be linked to individuals

**Instead, describe patterns and types:**
- "user's email address" not "john@example.com"
- "the message body" not the actual message text
- "a UUID-shaped ID" not the actual UUID value
- "a date in ISO 8601 format" not the actual date

The system automatically sanitises error messages and tracebacks, but your `context` field is free-form — you are responsible for keeping it clean.

---

## Handling Responses

### Accepted

```json
{"accepted": true, "fingerprint": "abc123...", "attempt_id": "...", "message": "Healing agent dispatched"}
```

A healing agent has been dispatched to investigate. Continue your session — attempt a workaround if possible, or inform the user the issue has been flagged for investigation. You do not need to wait for the healing agent to finish.

### Already investigating

```json
{"accepted": false, "reason": "already_investigating", "attempt_id": "...", "message": "This error is already under investigation"}
```

This exact error is already being worked on. Continue your session — a fix may arrive via PR soon.

### Rejected (other reasons)

```json
{"accepted": false, "reason": "cooldown", "message": "Cooldown period active..."}
```

The system has decided not to investigate at this time (cooldown, concurrency cap, circuit breaker, or no model available). This is fine — continue your session normally and do not retry `report_error` for the same error.

---

## Checking Status

If you encounter an error you previously reported (same exception type and call site pattern), you can optionally check its status:

```python
# Check by fingerprint (from a previous report_error response)
get_healing_status(fingerprint="abc123...")

# List recent attempts for this butler
get_healing_status()
```

### Interpreting status

| Status | Meaning |
|---|---|
| `investigating` | Healing agent is actively working on a fix |
| `pr_open` | A fix PR has been created; awaiting human review |
| `pr_merged` | Fix was merged — the error should resolve after the next deployment |
| `failed` | Healing agent encountered an error or could not produce a fix |
| `unfixable` | Agent determined this is not a code bug (external service, data issue) |
| `timeout` | Agent exceeded the time limit |
| `anonymization_failed` | Fix was produced but PR was blocked by PII detection |

If status is `pr_merged`, note that a fix was deployed and the error may resolve after a restart.

---

## Examples

### Good report

```python
report_error(
    error_type="asyncpg.exceptions.ForeignKeyViolationError",
    error_message="insert or update on table \"events\" violates foreign key constraint",
    traceback="Traceback (most recent call last):\n  File \"src/butlers/modules/calendar/tools.py\", line 42, in create_event\n    ...",
    call_site="src/butlers/modules/calendar/tools.py:create_event",
    context=(
        "I was trying to create a calendar event for the butler's schedule. "
        "The foreign key violation suggests the referenced contact_id does not exist "
        "in the contacts table. This may be a race condition where the contact record "
        "is created after the event is inserted, or a missing ON CONFLICT clause. "
        "The error is consistent across multiple attempts with valid-looking contact IDs."
    ),
    tool_name="calendar_create_event",
    severity_hint="high",
)
```

### Bad report (contains user data)

```python
# DO NOT DO THIS
report_error(
    error_type="ValueError",
    error_message="Invalid email address",
    context="User john@example.com tried to schedule a meeting with alice@company.com at 2pm on March 15.",  # NEVER include actual user data
)
```

### Bad report (transient error — should not report)

```python
# DO NOT DO THIS for rate limits or transient network errors
report_error(
    error_type="httpx.TimeoutException",
    error_message="Request timed out",
    context="The API timed out.",  # Retry first; only report if it's a systemic bug
)
```
