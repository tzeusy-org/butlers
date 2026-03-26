---
name: budget-status-check
description: Weekly scheduled task that checks budget status across all categories and alerts the user if any category is approaching or exceeding limits
version: 1.0.0
trigger_patterns:
  - scheduled task budget-status-check
---

# Budget Status Check Skill

This skill provides a weekly scheduled task that reviews spending against configured budgets, alerts on warnings and overages, and suggests corrective actions.

## Purpose

Track budget health across spending categories, identify categories at risk of exceeding limits, and proactively alert the user before overspending occurs.

## Prerequisites

Before running the budget status check:
1. Budgets are configured via `budget_set()` (e.g., $200/month for dining, $500/month for utilities)
2. Current spending data is available via `spending_summary()`
3. User has set alert thresholds (e.g., warn at 80%, alert at 100%)
4. Telegram notifications are enabled

## Check Flow

### 1. Retrieve Budget Configuration

Use `budget_list()` to get all configured budgets:
- Category name
- Limit amount
- Period (monthly, weekly, annual)
- Alert thresholds (warning %, exceeded %)
- Notes/purpose

### 2. Calculate Current Spending

For each budget category:
1. Use `spending_summary(start_date=<period_start>, end_date=<period_end>, group_by="category")` to get current spend
2. Calculate percentage of budget used: `current_spend / budget_limit * 100`
3. Determine status:
   - **Green (OK)**: <= 70% of budget
   - **Yellow (Warning)**: 71-90% of budget
   - **Orange (Caution)**: 91-99% of budget
   - **Red (Exceeded)**: 100%+ of budget

### 3. Generate Status Report

Compose a Telegram message grouped by status:

```
📊 Weekly Budget Status Check

🟢 ON TRACK (3 categories):
• Groceries: $180 / $250 (72%)
• Utilities: $120 / $150 (80%)
• Transport: $45 / $100 (45%)

🟡 WARNING (2 categories):
• Dining: $165 / $200 (82%)
  → Remaining: $35 — about 2 days left at current pace
• Entertainment: $45 / $50 (90%)
  → Remaining: $5 — be cautious this week

🔴 EXCEEDED (1 category):
• Subscriptions: $127 / $100 (127%)
  → Over by $27 — consider cancelling or pausing a service

**Summary**: 6 budgets active, 1 exceeded, 2 warning

Would you like to:
1. Review which subscriptions are contributing to the overage
2. Adjust budget limits
3. See daily spending breakdown for warning categories
```

### 4. Detailed Recommendations

For each warning or exceeded category, offer specific actions:

**Dining at 82%:**
- Recent transactions: Indicate highest spending merchants
- Suggestion: "Skip dining out 2–3 times to stay within budget"
- Action: "View recent dining transactions?" / "Adjust budget?"

**Subscriptions at 127% (exceeded):**
- Breakdown: List all active subscriptions and their costs
- Suggestion: "Consider cancelling unused services (e.g., Adobe if not used this month)"
- Action: "Review active subscriptions?" / "Contact providers to cancel?"

### 5. User Notification

Call `notify(channel="telegram", intent="send", message=<report>)` with the full status report.

### 6. No Issues Path

If all categories are under 70% of budget:
```
✅ All budgets on track this week. Great spending discipline!
```

No notification needed in this case.

## Memory Integration

Store budget-related insights:
- `memory_store_fact(subject="user", predicate="spending_discipline", content="consistent with budget discipline", permanence="volatile", ...)`
- `memory_store_fact(subject="<category>", predicate="spending_spike", content="exceeded budget by 27%", permanence="volatile", ...)`

## Scheduled Task Configuration

This skill runs as a weekly scheduled task (typically Monday morning):
- **Cron**: `0 9 * * MON` (Mondays at 9am) or custom per user preference
- **Dispatch mode**: `prompt`
- **Output**: Notification via Telegram if warnings or overages detected

## Error Handling

- If no budgets are configured: "No budgets set. Set up budget limits to start tracking."
- If spending data is unavailable: "Could not retrieve spending data. Try again later."
- If calculation fails: Log error, attempt to recover with fallback summary

## Example Scenarios

### Example 1: Healthy Budget Status

```
All 6 budgets within safe range (< 70%):
- Groceries: 65%
- Utilities: 52%
- Dining: 58%
- Entertainment: 41%
- Transport: 38%
- Subscriptions: 65%

Output: ✅ All budgets on track. No notification.
```

### Example 2: Warning State

```
Dining at 82% of $200 budget:
- Current: $164
- Remaining: $36
- Days left in month: 8
- Daily burn: $20.50 required to stay on budget
- Current pace: $3.21/day average

Action: "You're on track if you spend less than $3/day on dining for the rest of the month."
```

### Example 3: Exceeded State

```
Subscriptions at 127% of $100 budget:
- Current: $127
- Over by: $27
- Active subscriptions:
  - Netflix: $15.49/month
  - Spotify: $9.99/month
  - Adobe: $54.99/month
  - Dropbox: $9.99/month
  - Gym: $35/month

Action: "Consider cancelling Adobe ($55) to get back under budget."
```

### Example 4: Multiple Issues

```
🔴 Critical alerts (2 categories exceeded)
🟡 Warnings (3 categories in caution zone)
🟢 On track (1 category safe)

Focus on critical first, then review warnings.
```

## Version History

- v1.0.0 (2026-03-26): Initial stub for weekly budget status check task
