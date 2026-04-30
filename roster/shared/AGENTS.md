# Shared Butler Instructions

## Tool Execution Contract

**You MUST use MCP tools for ALL data access and mutations. This is non-negotiable.**

- NEVER attempt to connect to databases directly (no `asyncpg.connect`, no SQL queries, no `psql`)
- NEVER run shell commands to query data or check connectivity
- NEVER read source code to understand how tools work internally
- NEVER fabricate response schemas — call the tool and return its actual output
- If a tool call fails, report the error. Do not attempt workarounds via shell or code execution.
- If a required tool is not available in your MCP tool list, report that explicitly: "Tool `X` is not available in this session." Do not attempt to replicate its behavior.

Your MCP tools handle all database access, validation, and serialization. You are a caller of tools, not an implementer.

## MCP Tool Naming

Your tools are exposed via an MCP server. Depending on your runtime, tool names
may appear with a namespace prefix:

- Bare name: `notify`
- Namespaced: `mcp__<butler_name>__notify` (e.g. `mcp__finance__notify`)

Both refer to the same tool. **Use whichever form appears in your tool list.**

When a system prompt or skill references a tool by bare name (e.g. "call
`fact_set()`"), look for it in your tool list under either form. Do NOT attempt
to invoke tools via shell commands, source code inspection, or `grep` — they
are MCP tools, not CLI commands.

## Calendar Usage

- Write butler-managed events to the shared butler calendar configured in `butler.toml`, not the user's primary calendar.
- Default conflict behavior is `suggest`: propose alternative time slots when overlaps are detected.
- Only use overlap overrides when the user explicitly asks to keep the conflict.
- Attendee invites are out of scope for v1. Do not add attendees or send invitations.

### Butler-Managed Calendar Contract

Butler-internal scheduled tasks (e.g. `memory_consolidation`, `memory_episode_cleanup`,
`memory_purge_superseded`) must **never** appear on the user's primary Google Calendar or
in the Chronicle Calendar lane.

**Enforcement (two independent layers):**

1. **Writer-side guard (Track A — `calendar.py:_project_scheduler_source`)**: Only
   `dispatch_mode != 'job'` scheduled tasks are projected into `calendar_event_instances`.
   Job-dispatch tasks are internal automation and are explicitly excluded from the
   scheduler projection loop.

2. **Adapter-side guard (Track B — `chronicler/adapters/calendar.py:_fetch_instances`)**:
   `CalendarCompletedAdapter` joins `calendar_sources` and adds `AND cs.lane != 'butler'`
   to all fetch queries. This ensures that even if a butler-managed event reaches
   `calendar_event_instances` (e.g. through a future code path), it will never be
   projected into a user-visible Chronicle episode.

**`calendar_sources.lane` values:**
- `"user"` — provider events from the user's Google Calendar (project normally)
- `"butler"` — internal sources (`internal_scheduler`, `internal_reminders`); always excluded
  from Chronicle projection

If you add a new internal calendar source, set `lane="butler"` in `_ensure_calendar_source()`
to benefit from automatic exclusion.

## Scheduled Task Output Contract

When a scheduled task fires with `dispatch_mode="prompt"`, you are running in an ephemeral session with **no interactive user present**. Your text output goes nowhere — it is logged but never seen.

**If the task should communicate with the user, you MUST call `notify()`.** There is no other way to reach them.

- Use `intent="send"` for proactive scheduled messages (no `request_context` needed)
- Use `channel="telegram"` unless the prompt specifies otherwise
- If the task produces no actionable output (e.g., a cleanup with nothing to clean), you may exit silently without calling `notify()`

**Example:**
```python
notify(channel="telegram", intent="send", message="Your weekly health summary: ...")
```

For full notify() usage — interactive responses, scheduled notifications, and response modes — consult the `butler-notifications` shared skill.

## Creating Scheduled Tasks at Runtime

When you call `schedule_create()` with `dispatch_mode="prompt"`, the prompt you provide will run in a **fresh ephemeral session with no memory of the current conversation**. That future session has no interactive user and no access to `request_context`.

**If the scheduled task should message the user, you MUST embed an explicit `notify()` instruction in the prompt text itself.** The future session cannot infer this — it only sees the prompt you write now.

Include in the prompt:
- The exact `notify()` call with `channel` and `intent="send"` parameters
- What data to gather and include in the message
- When to skip notification (no-op path)

**Good — explicit notify() in prompt:**
```python
schedule_create(
    name="reminder-xyz",
    cron="0 9 * * *",
    prompt="Check X. If actionable, send via notify(channel='telegram', intent='send', message=<summary>). If nothing to report, exit silently.",
)
```

**Bad — no notify() instruction (message will be lost):**
```python
schedule_create(
    name="reminder-xyz",
    cron="0 9 * * *",
    prompt="Check X and tell the user about it.",  # "tell the user" means nothing in a headless session
)
```
