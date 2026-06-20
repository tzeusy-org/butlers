---
name: upcoming-bills-check
description: Scheduled task skill — weekly bills check with reconciliation sweep, pattern-based predictions, urgency-ranked digest sent via notify intent=send
version: 2.0.0
trigger_patterns:
  - scheduled task upcoming-bills-check
---

# Skill: Upcoming Bills Check

## Purpose

Weekly scheduled bill check. **First**, run the reconciliation sweep to auto-settle any bills
matched by existing transactions (the backstop — catches cases the inline hook missed). Then
surface remaining pending/overdue bills due in the next 14 days, rank by urgency, compute totals,
and include pattern-based bill predictions from historical transaction data. Deliver a concise
digest to the owner via Telegram. If nothing to report across all sections, send nothing.

## When to Use

Use this skill when:
- The `upcoming-bills-check` scheduled task fires (cron: `15 21 * * 0`, weekly on Sunday at 21:15)

## Execution Protocol

### Step 0: Run Reconciliation Sweep (FIRST)

Before fetching upcoming bills, run the deterministic reconciliation sweep. This is the weekly
**backstop** — it settles bills that have matching payment transactions but were missed by the
inline hook (e.g. a transaction recorded before the bill existed).

```python
reconciliation = reconcile_bills(lookback_days=90)
```

`reconcile_bills` returns:
- `auto_settled`: bills just settled in this sweep — each has `bill_id`, `payee`, `amount`,
  `paid_at`, `txn_id`
- `candidates`: ambiguous matches needing confirmation — each has `bill_id`, `payee`, `due_date`,
  `amount`, `candidates` (list of possible matching transactions)

Capture these results. **Do not surface `auto_settled` bills in the unpaid urgency sections** —
they are now paid. Confirmed ambiguous candidates must still be reported so the owner can decide.

### Step 1: Fetch Bills and Predictions

Run both calls (these now reflect the post-reconciliation state):

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

If ALL of the following are true, **do not send a notification**. Exit the session immediately.

- `reconcile_bills` returned no `auto_settled` entries
- `reconcile_bills` returned no `candidates`
- `upcoming_bills` returns empty
- `predict_bills` returns no predictions with `is_tracked=false`

(Predictions where `is_tracked=true` without urgency in `upcoming_bills` do not need reporting —
they are already captured in the tracked bills list.)

### Step 3: Classify Tracked Bills by Urgency

Group tracked bill results into buckets:
- **Overdue**: `urgency == "overdue"` — past due date
- **Due today**: `urgency == "due_today"` — due date is today
- **Due soon**: `urgency == "due_soon"` — due within 3 days
- **Upcoming**: `urgency == "due_upcoming"` — due within 14 days

Sort each bucket by amount descending (largest obligation first within each tier).

Bills auto-settled in Step 0 are **not** in these buckets — they've been removed from the
pending/overdue list by the reconciliation sweep.

### Step 4: Classify Predictions

From `predict_bills` results:
- **New patterns** (`is_tracked=false`): recurring payments detected in transaction history not
  yet tracked as bills — worth surfacing to the owner so they can decide to create a bill record
- **Amount drift** (`is_tracked=true` and `amount_drift` is non-zero): tracked bills where the
  predicted amount differs from the stored bill amount by a meaningful margin (>5%)

Focus on predictions within the 14-day window for the digest. Include 15-30 day predictions
only if the `is_tracked=false` pattern is high-confidence.

### Step 5: Compose Digest

Format the message concisely — readable on mobile Telegram. Lead with reconciliation results,
then unpaid obligations, then predictions.

```
Bills update — [Date]

✅ AUTO-SETTLED ([N]) — matched and marked paid this sweep
- [Payee]: $[amount] — paid [paid_at date]

❓ CONFIRM NEEDED ([N]) — ambiguous matches, please verify
- [Payee]: $[bill_amount] due [due_date] — possible match: $[txn_amount] at [merchant] on [posted_at]

🚨 OVERDUE ([N])
- [Payee]: $[amount] — [N] days overdue

🔴 DUE TODAY ([N])
- [Payee]: $[amount]

🟠 DUE SOON ([N])
- [Payee]: $[amount] — due [date] ([N] days)

🟡 UPCOMING ([N])
- [Payee]: $[amount] — due [date]

Total still due: $[sum of remaining unpaid amounts]

📈 PREDICTED (untracked recurring):
- [Payee]: ~$[predicted_amount] — expected [predicted_date] (pattern-based)
```

**Section rules:**
- Omit any section header if its bucket is empty.
- Omit "Total still due" line if there are no remaining unpaid bills.
- If nothing overdue/urgent and only upcoming bills exist, a shorter format is acceptable:
  ```
  Bills — next 14 days: [N] due, $[total]
  - [Payee]: $[amount] — [date]
  ```
- Omit the "PREDICTED" section if `predict_bills` returns no untracked patterns.
- Include an `amount_drift` note inline for tracked bills where drift exceeds 5%:
  ```
  - [Payee]: $[amount] — due [date] (tracked $[tracked_amount], predicted ~$[predicted_amount])
  ```

**Confirm-needed guidance:** For each `candidates` entry, include the payee, bill amount, due
date, and the best-matching transaction candidate's merchant/amount/date. The owner can confirm
via the `bill-reminder` skill or by directly updating the bill status.

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

- `reconcile_bills(lookback_days=90)` called first (settlement backstop)
- `upcoming_bills(days_ahead=14, include_overdue=True)` called after
- `predict_bills(days_ahead=30)` called to surface pattern-based predictions
- If nothing to report across all sections: session exits without sending
- If any reconciliation results or bills or untracked predictions exist: urgency-classified
  digest with reconciliation and prediction sections sent via `notify(intent="send")`
- Session exits after delivery — no interactive follow-up in this session
