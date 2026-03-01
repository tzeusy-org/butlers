---
name: monthly-spending-summary
description: Scheduled task skill — monthly spending summary with category comparison to prior month, sent via notify intent=send
version: 1.0.0
trigger_patterns:
  - scheduled task monthly-spending-summary
---

# Skill: Monthly Spending Summary

## Purpose

Monthly scheduled spending digest. Aggregate spend for the previous calendar month by category,
compare to the month before that, compute deltas, and deliver a structured overview to the owner
via Telegram. Runs on the 1st of each month to cover the month just closed.

## When to Use

Use this skill when:
- The `monthly-spending-summary` scheduled task fires (cron: `0 9 1 * *`, 1st of each month at 09:00)

## Execution Protocol

### Step 1: Determine Date Ranges

Compute the two date ranges at session start:

```
Current period (month just closed):
  start = first day of prior calendar month (e.g., Feb 1 if today is Mar 1)
  end   = last day of prior calendar month  (e.g., Feb 28)

Prior period (for comparison):
  start = first day of month before that (e.g., Jan 1)
  end   = last day of month before that  (e.g., Jan 31)
```

### Step 2: Fetch Spending Data

Call `spending_summary` for both periods:

```python
current = spending_summary(
    start_date=<current_period_start>,
    end_date=<current_period_end>,
    group_by="category"
)

prior = spending_summary(
    start_date=<prior_period_start>,
    end_date=<prior_period_end>,
    group_by="category"
)
```

Each call returns category-level totals and a grand total.

### Step 3: Compute Deltas

For each category present in `current`:
- Look up the same category in `prior`
- Delta = `current_amount - prior_amount`
- Delta pct = `(delta / prior_amount) * 100` if `prior_amount > 0`, else "new this month"

For categories in `prior` but absent from `current`:
- Note them as "no spend this month" (not in the digest unless significant)

### Step 4: Compose Summary

Format for Telegram — concise, scannable:

```
[Month] Spending Summary

Total: $[current_total]  ([+/-]$[delta] vs [prior_month])

By category:
- [Category]: $[amount]  ([+N%] or [-N%] vs prior)
- [Category]: $[amount]  (new this month)
- [Category]: $[amount]  (unchanged)

Top merchant: [merchant_name] ($[amount])

[If significant delta] Notable changes:
- [Category] up $[N] ([+N%]) — [brief note if reason known]
- [Category] down $[N] ([-N%])
```

Include at most the top 6-8 categories by spend. Collapse smaller categories into "Other: $[N]"
if more than 8 categories exist.

**Formatting rules:**
- Use `+` prefix for increases, `-` for decreases
- Round percentages to the nearest whole number
- Show deltas only when prior period data is available; omit delta column if prior data is missing
- If total spend is zero for the current month, send a brief "No spending recorded for [month]" message

### Step 5: Deliver Notification

```python
notify(
    channel="telegram",
    intent="send",
    message=<formatted_summary>,
    request_context=<session_request_context>
)
```

Use `intent="send"` (not `reply`) — this is a proactive scheduled delivery.

### Step 6: Optional — Store Notable Pattern in Memory

If the digest reveals a significant shift (category up >50% or new high-spend merchant), record it:

```python
memory_store_fact(
    subject=<category_or_merchant>,
    predicate="spending_spike",
    content="[Category] up $[N] ([+N%]) in [month] vs prior — review recommended",
    permanence="volatile",
    importance=6.0,
    tags=["spending", "monthly-summary", "anomaly"]
)
```

Store only when the shift is notable. Do not store routine monthly summaries in memory.

## Exit Criteria

- `spending_summary` called for current period (month just closed) grouped by category
- `spending_summary` called for prior period for comparison (if available)
- Deltas computed per category
- Summary sent via `notify(intent="send")` with category breakdown and deltas
- If notable anomaly detected: `memory_store_fact` called to record it
- Session exits after delivery — no interactive follow-up in this session
