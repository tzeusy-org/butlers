---
name: monthly-spending-summary
description: Scheduled task skill — monthly spending summary with trend data, budget status, anomaly count, subscription audit, and net worth prompt, sent via notify intent=send
version: 1.1.0
trigger_patterns:
  - scheduled task monthly-spending-summary
---

# Skill: Monthly Spending Summary

## Purpose

Monthly scheduled spending digest. Aggregate spend for the previous calendar month by category,
compare to the month before that using trend data, include budget status, anomaly count, and a
subscription audit summary. Deliver a structured overview to the owner via Telegram. Runs on the
1st of each month to cover the month just closed.

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

### Step 2: Fetch Spending and Intelligence Data

Run all data calls in parallel where possible:

```python
# Spending summary for current and prior months
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

# Month-over-month trend data (2-month comparison)
trends = spending_trends(comparison="month_over_month", months=2)

# Budget utilization status across all active budgets
budget = budget_status()

# Anomaly scan for the full prior month
anomalies = anomaly_scan(days_back=30, sensitivity="medium")

# Subscription audit
audit = subscription_audit()
```

### Step 3: Compute Spending Deltas

For each category present in `current`:
- Look up the same category in `prior`
- Delta = `current_amount - prior_amount`
- Delta pct = `(delta / prior_amount) * 100` if `prior_amount > 0`, else "new this month"

For categories in `prior` but absent from `current`:
- Note them as "no spend this month" (not in the digest unless significant)

Prefer trend data from `spending_trends` if it provides richer per-category deltas; fall back
to manual delta computation if `spending_trends` returns `status="insufficient_data"`.

### Step 4: Summarize Budget Status

From `budget_status()`:
- Count categories by status: `on_track`, `warning`, `exceeded`
- Collect all `warning` and `exceeded` categories with their utilization percentage
- If all categories are `on_track`, note "All budgets on track"

### Step 5: Summarize Anomalies

From `anomaly_scan(days_back=30)`:
- Count total anomalies detected
- Identify the top 2-3 most significant anomalies (by severity or amount)
- If `status="insufficient_data"`, note "Insufficient data for anomaly detection"
- If zero anomalies: note "No anomalies detected this month"

### Step 6: Summarize Subscription Audit

From `subscription_audit()`:
- Total active subscriptions count
- Combined monthly cost and annual cost projection
- Any subscriptions with price changes since last audit
- Any newly detected recurring charges not yet formally tracked as subscriptions
- If `status="insufficient_data"`, note "Subscription data unavailable"

### Step 7: Compose Summary

Format for Telegram — concise, scannable:

```
[Month] Monthly Summary

💰 Spending: $[current_total]  ([+/-]$[delta] vs [prior_month])

By category:
- [Category]: $[amount]  ([+N%] or [-N%] vs prior)
- [Category]: $[amount]  (new this month)
- [Category]: $[amount]  (unchanged)

Top merchant: [merchant_name] ($[amount])

📊 Budget Status: [N on_track] / [N warning] / [N exceeded]
[If warnings/exceeded:]
- ⚠️ [Category]: [utilization]% ([status])

🔍 Anomalies: [N] flagged this month
[Top anomalies:]
- [Type]: [merchant] — [brief note]

🔄 Subscriptions: [N] active — $[monthly_total]/mo ($[annual_total]/yr)
[If changes:]
- Price change: [Service] $[old] → $[new]
- Untracked recurring: [Merchant] ($[amount])

📌 Reminder: Update net worth snapshot if account balances changed this month.
```

Include at most the top 6-8 categories by spend. Collapse smaller categories into "Other: $[N]"
if more than 8 categories exist.

**Formatting rules:**
- Use `+` prefix for increases, `-` for decreases
- Round percentages to the nearest whole number
- Show deltas only when prior period data is available; omit delta column if prior data is missing
- If total spend is zero for the current month, send a brief "No spending recorded for [month]" message
- Include budget section only if budgets are configured (skip if `budget_status()` returns empty list)
- Include anomaly section even if zero anomalies — "No anomalies detected" is valuable signal

### Step 8: Deliver Notification

```python
notify(
    channel="telegram",
    intent="send",
    message=<formatted_summary>,
    request_context=<session_request_context>
)
```

Use `intent="send"` (not `reply`) — this is a proactive scheduled delivery.

### Step 9: Optional — Store Notable Pattern in Memory

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
- `spending_trends(comparison="month_over_month", months=2)` called for trend data
- `budget_status()` called and results included if budgets are configured
- `anomaly_scan(days_back=30)` called; anomaly count and highlights included
- `subscription_audit()` called; audit summary included
- Deltas computed per category
- Full summary sent via `notify(intent="send")` with all sections
- If notable anomaly detected: `memory_store_fact` called to record it
- Session exits after delivery — no interactive follow-up in this session
