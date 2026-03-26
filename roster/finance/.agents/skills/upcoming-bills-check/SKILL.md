---
name: upcoming-bills-check
description: Scheduled task skill — weekly bills check with pattern-based predictions, urgency-ranked digest sent via notify intent=send
version: 1.1.0
trigger_patterns:
  - scheduled task upcoming-bills-check
---

# Skill: Upcoming Bills Check

## Purpose

Weekly scheduled bill check. Surface bills due in the next 14 days (and any overdue), rank by
urgency, compute totals, and include pattern-based bill predictions from historical transaction
data. Deliver a concise digest to the owner via Telegram. If no bills are due, overdue, or
predicted, send nothing.

## When to Use

Use this skill when:
- The `upcoming-bills-check` scheduled task fires (cron: `15 21 * * 0`, weekly on Sunday at 21:15)

## Execution Protocol

### Step 1: Fetch Bills and Predictions

Run both calls:

```python
# Tracked bills due in next 14 days plus any overdue
bills = upcoming_bills(days_ahead=14, include_overdue=True)

# Pattern-based bill predictions from transaction history (30-day horizon)
predictions = predict_bills(days_ahead=30)
```

`upcoming_bills` returns tracked bills with fields: `payee`, `amount`, `currency`, `due_date`,
`urgency` (`overdue`, `due_today`, `due_soon`, `due_upcoming`), `status`.

`predict_bills` returns predictions with fields: `payee`, `predicted_amount`, `predicted_date`,
`is_tracked` (bool — whether a tracked bill already exists for this payee), `amount_drift`
(percentage difference from tracked amount if `is_tracked=true`), `confidence`.

### Step 2: Early Exit — Nothing to Report

If `upcoming_bills` returns empty **and** `predict_bills` returns no predictions with
`is_tracked=false`, **do not send a notification**. Exit the session immediately.

(Predictions where `is_tracked=true` without urgency in `upcoming_bills` do not need reporting —
they are already captured in the tracked bills list.)

### Step 3: Classify Tracked Bills by Urgency

Group tracked bill results into buckets:
- **Overdue**: `urgency == "overdue"` — past due date
- **Due today**: `urgency == "due_today"` — due date is today
- **Due soon**: `urgency == "due_soon"` — due within 3 days
- **Upcoming**: `urgency == "due_upcoming"` — due within 14 days

Sort each bucket by amount descending (largest obligation first within each tier).

### Step 4: Classify Predictions

From `predict_bills` results:
- **New patterns** (`is_tracked=false`): recurring payments detected in transaction history not
  yet tracked as bills — worth surfacing to the owner so they can decide to create a bill record
- **Amount drift** (`is_tracked=true` and `amount_drift` is non-zero): tracked bills where the
  predicted amount differs from the stored bill amount by a meaningful margin (>5%)

Focus on predictions within the 14-day window for the digest. Include 15-30 day predictions
only if the `is_tracked=false` pattern is high-confidence.

### Step 5: Compose Digest

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

Total due: $[sum of all tracked amounts]

📈 PREDICTED (untracked recurring):
- [Payee]: ~$[predicted_amount] — expected [predicted_date] (pattern-based)
```

Omit any section header if its bucket is empty. If only "Upcoming" bills exist (nothing urgent),
a shorter format is acceptable:

```
Bills — next 14 days: [N] due, $[total]
- [Payee]: $[amount] — [date]
```

Omit the "PREDICTED" section if `predict_bills` returns no untracked patterns.

Include an `amount_drift` note inline for tracked bills where drift exceeds 5%:
```
- [Payee]: $[amount] — due [date] (tracked $[tracked_amount], predicted ~$[predicted_amount])
```

### Step 6: Deliver Notification

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
- `predict_bills(days_ahead=30)` called to surface pattern-based predictions
- If no bills and no untracked predictions: session exits without sending
- If bills or untracked predictions exist: urgency-classified digest with prediction section
  sent via `notify(intent="send")`
- Session exits after delivery — no interactive follow-up in this session
