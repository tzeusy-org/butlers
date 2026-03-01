---
name: spending-review
description: Guided workflow for reviewing recent spending by category, time period, and anomalies
version: 1.0.0
---

# Spending Review Skill

This skill provides a structured, interactive workflow for reviewing your recent spending. Use this for daily, weekly, or monthly spending analysis and pattern detection.

## Purpose

Guide you through a comprehensive review of your spending that covers time period selection, category breakdown, merchant analysis, and anomaly detection. The review adapts based on your chosen time period and generates insights at the end.

## Prerequisites

Before starting the spending review, gather context:
1. Get transaction count: `list_transactions(limit=1)` to verify data exists
2. Determine available date range: ask user for preferred review period (daily, weekly, monthly, or custom range)
3. Retrieve recent spending summary: `spending_summary()` to understand baseline

## Review Flow

Follow this structured flow in order. Use progressive disclosure — present findings one section at a time and wait for user response before proceeding to the next section.

### 1. Time Period Selection

Ask the user what period they want to review:
- **"Daily"**: Review today's spending only
- **"Weekly"**: Review the past 7 days
- **"Monthly"**: Review the current month (or specify: "this month" vs "last month")
- **"Custom"**: Allow user to specify start and end dates

Store the selected period for queries below.

**Example conversation:**
```
Bot: "Let's review your spending. What time period would you like to look at?"
User: "Last month"
Bot: "Got it, I'll show you January 1–31 spending."
```

### 2. Spending Summary by Category

Use `spending_summary(start_date=<period_start>, end_date=<period_end>, group_by="category")` to aggregate spending.

Present the results in this format:

```
# Spending Summary — [Period]

Total Spent: $[amount]

By Category:
- [Category 1]: $[amount] ([% of total]) — [count] transactions
- [Category 2]: $[amount] ([% of total]) — [count] transactions
- [Category 3]: $[amount] ([% of total]) — [count] transactions
  ...

Top Merchant in [Top Category]: [Merchant name] ($[amount])
```

**Analysis questions to ask yourself (do not push on user unless they ask):**
- Is the top category expected for this period?
- Are there any new categories that didn't appear before?
- Does the total spending feel aligned with what the user might have guessed?

If the user asks "Is this normal?" or "Did I spend a lot?", try to provide context:
- Use `memory_recall(topic="spending patterns")` to retrieve baseline expectations
- Compare current period to previous period if available
- Flag unusually high or low totals

### 3. Top Merchants and Transaction Breakdown

Use `list_transactions(start_date=<period_start>, end_date=<period_end>, limit=20)` to get top transactions.

Present top merchants by transaction count:

```
# Top Merchants

1. [Merchant 1] — [count] transactions totaling $[amount]
   - Most recent: $[amount] on [date]
2. [Merchant 2] — [count] transactions totaling $[amount]
   - Most recent: $[amount] on [date]
...
```

**Follow-up questions (if appropriate):**
- For recurring merchants (same merchant, similar amounts): "Would you like me to create a subscription record for [Merchant]?" (if not already tracked)
- For unusual merchants: "What is [Merchant]? Is this something new?"
- For high-frequency merchants: "You shopped at [Merchant] 5 times this week. Would you like to set a spending limit or goal for this category?"

### 4. Anomaly Detection and Notable Patterns

Run `spending_summary()` for two consecutive periods (current and previous) to detect anomalies:

**Anomaly types to flag:**

1. **Unusually High Single Transaction**: If any single transaction is > 1.5× the category average or > $500 (configurable), flag it:
   ```
   ⚠️ Large transaction detected: $[amount] at [Merchant] on [date]
   Is this planned? Should I note this as an unusual expense?
   ```

2. **Category Spending Spike**: If a category's spending increased > 50% vs previous period, flag it:
   ```
   📈 Spending spike: [Category] increased from $[previous_amount] to $[current_amount]
   What's behind the increase? New subscription? One-time purchase?
   ```

3. **Unusual Merchant**: If a transaction is from a merchant not seen before, note it:
   ```
   New merchant detected: [Merchant name] ($[amount])
   Is this a new subscription, recurring payment, or one-time?
   ```

4. **Subscriptions Not Tracked**: If the same merchant appears multiple times at similar amounts/intervals, suggest creating a subscription record:
   ```
   I noticed [Merchant] charged you $[amount] on [dates]. This looks like a recurring subscription.
   Would you like me to track this as "[Merchant] subscription"?
   ```

**Positive patterns to acknowledge:**
- "Your spending this week was $[amount], down from $[previous_amount] last week — great job!"
- "You had no transactions in [Category] this period. Keeping up with your goals?"

### 5. Time-Based Trends (Optional)

If reviewing a longer period (weekly or monthly), offer to show spending by week or day:

```
Bot: "Would you like to see how your spending breaks down by week?"
User: "Sure"
Bot: [Show spending by week, highlighting any week that stands out]
```

Use `spending_summary(group_by="week")` to break down by week, or `group_by="day"` for daily detail.

### 6. Summary and Action Items

After presenting all findings, generate a summary:

```
# Spending Review Summary — [Period]

**Total Spent**: $[amount]
**Top Category**: [Category] ($[amount])
**Transaction Count**: [N]
**Highest Transaction**: $[amount] at [Merchant]

## Highlights
- [Key finding 1]
- [Key finding 2]
- [Key finding 3]

## Action Items
- [Suggested action 1, if applicable]
- [Suggested action 2, if applicable]

## Next Steps
- Would you like to set a spending limit for any category?
- Would you like to create subscription records for recurring charges?
- Should I send you weekly spending summaries?
```

**Suggested actions** (only if appropriate):
- "Consider setting a budget for [Category]" (if unusually high)
- "Create subscription record for [Merchant]" (if recurring)
- "Review subscription list and cancel unused services" (if subscriptions are high)
- "Track spending limit for [Category]" (if user expresses concern)

### 7. Memory Storage

After the user acknowledges or completes the review, optionally store insights:

```
Bot: "Would you like me to remember any spending patterns or goals from this review?"
```

If yes, use `memory_store_fact()` to capture:

```python
# Example: Store spending pattern
memory_store_fact(
    subject="user",
    predicate="spending_pattern",
    content="tends to spend $300-400/week on groceries at Whole Foods and local markets",
    permanence="standard",
    importance=6.0,
    tags=["spending", "groceries", "recurring"]
)

# Example: Store spending concern
memory_store_fact(
    subject="dining",
    predicate="spending_spike",
    content="dining expenses increased from $200 to $350 in [month] — user investigating",
    permanence="volatile",
    importance=5.0,
    tags=["spending", "dining", "anomaly"]
)

# Example: Store spending goal
memory_store_fact(
    subject="user",
    predicate="spending_goal",
    content="wants to keep subscriptions under $50/month total",
    permanence="standard",
    importance=7.0,
    tags=["spending", "goal", "subscriptions"]
)
```

## Adaptive Tips

**For daily reviews:**
- Keep it light: show top 3 transactions and one anomaly if present
- Focus on "did I spend more today than typical?"
- 2–3 minutes

**For weekly reviews:**
- Show category breakdown and top merchants
- Highlight any notable patterns (spike, new merchant, subscription suggestion)
- Check subscription trend (are recurring charges increasing?)
- 5–7 minutes

**For monthly reviews:**
- Full category breakdown with week-by-week trends
- Compare to previous month (if available)
- Review all detected subscriptions and flag any that are new or changed
- Surface top 3 merchants and high-transaction anomalies
- 10–15 minutes

**For custom date range reviews:**
- Allow any start/end date pair
- Adapt feedback based on range length (e.g., "You spent $X over Y days, averaging $Z/day")

## Error Handling

- If no transactions exist for the period: "You don't have any recorded spending for [period]. Would you like to review a different time range?"
- If date range is invalid: "I need a valid date range. Could you clarify the dates?"
- If category lookup fails: Use generic category or merchant name
- If spending_summary times out: Fall back to displaying raw transaction list with manual category grouping

## Example Review Flows

### Example 1: Quick Daily Review

```
Bot: "Daily spending review! Today you had 3 transactions totaling $47.32."
- Coffee at Blue Bottle: $5.50
- Lunch at Panera: $14.99
- Groceries at Whole Foods: $26.83

Nothing out of the ordinary. Have a good day!
```

### Example 2: Weekly Review with Spike Detection

```
Bot: "Here's your weekly spending summary (Jan 15–21):"

Total Spent: $684.50

By Category:
- Groceries: $185.40 (27%) — 6 transactions
- Dining: $156.20 (23%) — 8 transactions
- Transport: $120.00 (18%) — 2 transactions
- Entertainment: $98.50 (14%) — 3 transactions
- Subscriptions: $44.40 (6%) — 4 transactions

Top Merchants:
1. Whole Foods — 4 transactions, $126.30
2. DoorDash — 5 transactions, $89.40
3. Uber — 2 transactions, $120.00

📈 Alert: Your dining spending jumped to $156 this week, up from $89 last week. Special occasion or new routine?
```

### Example 3: Monthly Review with Memory Integration

```
Bot: "January Spending Summary:"

Total Spent: $2,847.60

By Category:
- Rent: $1,800 (63%)
- Groceries: $520 (18%)
- Dining: $287 (10%)
- Subscriptions: $148 (5%)
- Transport: $92 (3%)

⚠️ Subscription Alert: Netflix ($15.99), Spotify ($12.99), Adobe Cloud ($54.99), and 2 others.
Total subscriptions: $148/month. Same as last month—no new ones.

🎯 Reminder: You mentioned wanting to keep subscriptions under $150. You're right at that level!

Would you like me to continue tracking this goal and alert you if subscriptions exceed $150?
```

### Example 4: Custom Range with Comparison

```
User: "Show me spending from Feb 1–10"

Bot: "February 1–10 Spending:"

Total: $547.30 (averaging $54.73/day)

By Category:
- Groceries: $185 (34%)
- Dining: $198 (36%)
- Transport: $98 (18%)
- Entertainment: $66 (12%)

Compared to Jan 1–10:
- Groceries: similar ($192)
- Dining: ↑ 23% ($161 → $198)
- Transport: ↓ 15% ($115 → $98)

💡 Your dining spending is trending upward. Anything new going on?
```

---

## Important Reminders

1. **Always have data ready**: Confirm transactions exist before starting review
2. **Progress gradually**: Don't dump all data at once — use progressive disclosure
3. **Personalize patterns**: Use memory to recall user preferences and previous concerns
4. **Proactive anomaly detection**: Surface unusual spending without judgment
5. **Respect privacy**: Keep spending data private; never assume the "why" behind spending
6. **Storage discipline**: Use memory facts only for patterns the user approves, not every observation

---

## Version History

- v1.0.0 (2026-02-23): Initial skill creation with daily/weekly/monthly review flows, anomaly detection, and memory integration
