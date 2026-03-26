---
name: budget-review
description: Interactive workflow for setting, reviewing, and adjusting budgets with status and forecast integration
version: 1.0.0
---

# Budget Review Skill

This skill provides a structured, interactive workflow for managing budgets — setting spending
limits per category, reviewing current utilization, comparing against actuals, and previewing
end-of-month forecasts.

## Purpose

Help the owner maintain spending discipline by making budget configuration, status, and
forecasting easy and interactive. The workflow supports first-time budget setup, periodic
reviews, and reactive adjustments when a category is nearing or over its limit.

## When to Use

Use this skill when:
- The owner asks to set, update, or remove a budget
- The owner asks "how am I doing on my budget?"
- The owner wants a summary of current budget status
- The owner wants a spending forecast for the rest of the month
- Any time a budget review or configuration is requested interactively

## Prerequisites

Before starting the budget review, gather context:

1. Fetch current budgets: `budget_list()` to understand what is configured
2. Fetch current status: `budget_status()` to get utilization across active budgets
3. Optionally fetch forecast: `spending_forecast()` for end-of-month projection

## Review Flow

Follow this structured flow. Adapt based on whether this is a setup session, a status check,
or an adjustment request.

---

### Mode A: Status Check

When the owner asks "how am I doing on my budget?" or similar:

#### Step 1: Fetch and Present Budget Status

```python
status = budget_status()
```

Present results grouped by status:

```
Budget Status — [current month]

🔴 EXCEEDED ([N])
- [Category]: $[spent] / $[budget] ([utilization]%) — over by $[over_amount]

🟠 WARNING ([N])
- [Category]: $[spent] / $[budget] ([utilization]%) — $[remaining] left

🟢 ON TRACK ([N])
- [Category]: $[spent] / $[budget] ([utilization]%)
```

Omit any section header if its bucket is empty.

If no budgets are configured, say: "No budgets are set up yet. Would you like to create one?"
and proceed to Mode B.

#### Step 2: Forecast Check (Optional)

If any category is in `warning` or `exceeded` status, offer to show the end-of-month forecast:

```
Bot: "You're running high on [Category]. Want to see the end-of-month forecast?"
User: "Yes"
```

If yes:

```python
forecast = spending_forecast()
```

Present per-category forecast for the categories with concerns:

```
End-of-month forecast for [Category]:
- Current spend: $[spent]
- Projected total: ~$[projected]
- Budget: $[budget]
- Projected overage: ~$[overage]
```

#### Step 3: Offer Adjustments

After presenting status and optional forecast, offer actions:

```
Bot: "Would you like to:
- Adjust a budget limit?
- Create a new budget?
- Remove a budget category?
- Review spending details for a flagged category?"
```

Route to Mode B (Configure) or Mode C (Deep Dive) based on the owner's choice.

---

### Mode B: Configure Budget

When the owner wants to set, update, or remove a budget:

#### Step 1: Determine Action

Ask what the owner wants to do:
- **Set/update a budget**: for a specific category, amount, and period
- **Remove a budget**: deactivate a category budget
- **Review existing**: show all configured budgets before making changes

#### Step 2: Set or Update a Budget

Collect required details:

```
Bot: "Which spending category do you want to budget for?"
User: "Dining"

Bot: "What's the monthly budget amount for Dining?"
User: "$300"

Bot: "Should I alert you when you reach a certain threshold? (e.g., 80% warning, 100% alert)"
User: "80% warning, 95% alert"
```

Call:

```python
budget_set(
    category="dining",
    amount=300.00,
    period="monthly",
    currency="USD",
    warn_threshold=0.80,   # 80%
    alert_threshold=0.95   # 95%
)
```

Confirm: "Budget set: Dining — $300/month. I'll warn you at $240 (80%) and alert at $285 (95%)."

**Defaults if not specified:**
- `period`: `"monthly"` (current month rolling)
- `currency`: `"USD"` (or infer from existing budgets)
- `warn_threshold`: `0.80` (80%)
- `alert_threshold`: `1.00` (100%)

#### Step 3: Remove a Budget

```
Bot: "Which category's budget would you like to remove?"
User: "Entertainment"
```

Call:

```python
budget_remove(category="entertainment", period="monthly")
```

Confirm: "Budget for Entertainment removed."

#### Step 4: Review All Budgets

```python
budgets = budget_list()
```

Present a clean list:

```
Active Budgets:

Category       Budget    Period    Warn    Alert
─────────────────────────────────────────────────
Dining         $300      monthly   80%     95%
Groceries      $500      monthly   80%     100%
Entertainment  $150      monthly   70%     90%
Subscriptions  $100      monthly   80%     100%
```

---

### Mode C: Deep Dive (Category Drill-Down)

When the owner wants to investigate why a specific category is over or near budget:

#### Step 1: Fetch Transactions for Category

```python
list_transactions(
    start_date=<current month start>,
    end_date=<today>,
    category=<category>,
    limit=50
)
```

Present top transactions:

```
[Category] transactions this month:

1. [Merchant] — $[amount] on [date]
2. [Merchant] — $[amount] on [date]
...

Total: $[sum] / $[budget] budget
```

#### Step 2: Identify Patterns

Note any patterns:
- Unusually large single transaction
- New merchant not seen before
- Increased visit frequency vs prior months
- Subscription charges mixed in (should those be in a "subscriptions" category instead?)

Present observations and ask if the owner wants to take any action (recategorize, adjust budget,
add a subscription record for a recurring charge).

---

## Summary and Storage

After completing any mode, offer to store preferences:

```python
# Example: Store budget preference
memory_store_fact(
    subject="user",
    predicate="budget_review_preference",
    content="prefers monthly budgets with 80% warning threshold",
    permanence="stable",
    importance=7.0,
    tags=["budget", "preference"]
)
```

## Error Handling

- If `budget_status()` returns empty list: prompt to set up first budget (route to Mode B)
- If `spending_forecast()` returns `status="insufficient_data"`: note "Not enough history for
  forecast — check back after more transactions are recorded"
- If `budget_set` fails: report the error clearly; do not retry silently
- If category name is ambiguous: ask user to clarify (e.g., "Did you mean 'dining' or 'food'?")

## Important Reminders

1. **Always check current status before suggesting changes** — show the owner where they are
   before asking them to act
2. **Forecast is optional** — only surface it when relevant (category is over/near budget)
3. **Respect category naming** — use lowercase category names matching transaction data conventions
4. **Never initiate payments or transfers** — budgets are tracking and alerting only
5. **Store preferences, not data** — use memory for user choices, not budget amounts (those live
   in the database)

---

## Version History

- v1.0.0 (2026-03-26): Initial skill creation with status check, configure, and deep-dive modes
