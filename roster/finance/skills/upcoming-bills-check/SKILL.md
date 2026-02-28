---
name: upcoming-bills-check
description: Scheduled task skill — daily bills check, urgency-ranked digest sent via notify intent=send
version: 1.0.0
trigger_patterns:
  - scheduled task upcoming-bills-check
---

# Skill: Upcoming Bills Check

## Purpose

Daily scheduled bill check. Surface bills due in the next 14 days (and any overdue), rank by
urgency, compute totals, and deliver a concise digest to the owner via Telegram. If no bills
are due or overdue, send nothing.

## When to Use

Use this skill when:
- The `upcoming-bills-check` scheduled task fires (cron: `0 8 * * *`, daily at 08:00)

## Execution Protocol

### Step 1: Fetch Bills

```python
upcoming_bills(days_ahead=14, include_overdue=True)
```

Returns a list of bills with fields: `payee`, `amount`, `currency`, `due_date`, `urgency`
(`overdue`, `due_today`, `due_soon`, `due_upcoming`), `status`.

### Step 2: Early Exit — No Bills

If the result is empty (no upcoming or overdue bills), **do not send a notification**. Exit
the session immediately.

### Step 3: Classify by Urgency

Group results into buckets:
- **Overdue**: `urgency == "overdue"` — past due date
- **Due today**: `urgency == "due_today"` — due date is today
- **Due soon**: `urgency == "due_soon"` — due within 3 days
- **Upcoming**: `urgency == "due_upcoming"` — due within 14 days

Sort each bucket by amount descending (largest obligation first within each tier).

### Step 4: Compose Digest

Format the message concisely — readable on mobile Telegram:

```
Bills update — [Date]

🚨 OVERDUE ([N])
- [Payee]: $[amount] — [N] days overdue

🔴 DUE TODAY ([N])
- [Payee]: $[amount]

🟠 DUE SOON ([N])
- [Payee]: $[amount] — due [date] ([N] days)

🟡 UPCOMING ([N])
- [Payee]: $[amount] — due [date]

Total due: $[sum of all amounts]
```

Omit any section header if its bucket is empty. If only "Upcoming" bills exist (nothing urgent),
a shorter format is acceptable:

```
Bills — next 14 days: [N] due, $[total]
- [Payee]: $[amount] — [date]
- [Payee]: $[amount] — [date]
```

### Step 5: Deliver Notification

```python
notify(
    channel="telegram",
    intent="send",
    message=<formatted_digest>,
    request_context=<session_request_context>
)
```

Use `intent="send"` (not `reply`) — this is a proactive scheduled delivery, not a response to
a user message.

## Exit Criteria

- `upcoming_bills(days_ahead=14, include_overdue=True)` called
- If no bills: session exits without sending
- If bills exist: urgency-classified digest sent via `notify(intent="send")`
- Session exits after delivery — no interactive follow-up in this session
