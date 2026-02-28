---
name: memory-classification
description: Finance domain memory taxonomy — subject/predicate conventions, permanence levels, tagging strategy, and example facts
version: 1.0.0
---

# Finance Memory Classification

Domain-specific taxonomy for `memory_store_fact()` calls in the finance butler. Consult this
skill when storing memory facts to ensure consistent subject/predicate/tag usage across sessions.

For entity resolution protocol (resolving person mentions to entity IDs before storing facts),
see the `butler-memory` shared skill.

## Subject Conventions

- **User preferences and habits**: `"user"` or the user's name
- **Merchants**: merchant name (e.g., `"Amazon"`, `"Whole Foods"`)
- **Accounts**: account label (e.g., `"Chase Sapphire"`, `"Ally Savings"`)
- **Subscription services**: service name (e.g., `"Netflix"`, `"Spotify"`, `"Adobe Creative Cloud"`)
- **Spending categories**: category name (e.g., `"dining"`, `"subscriptions"`)

## Predicate Conventions

| Predicate | When to use |
|-----------|-------------|
| `preferred_payment_method` | Preferred card or payment method for a category or merchant |
| `financial_institution` | Primary bank or credit union |
| `spending_habit` | Observed recurring behavior pattern (e.g., weekly grocery runs) |
| `budget_preference` | User-stated budget limit for a category |
| `bill_reminder_preference` | How many days before due date the user wants reminders |
| `subscription_status` | Current status of a subscription service |
| `price_change` | Noted price change event for a service |
| `merchant_category` | Canonical category for a merchant |
| `account_last_four` | Masked identifier for an account |
| `spending_spike` | Notable spending increase detected in a category or merchant |

## Permanence Levels

| Level | When to use |
|-------|-------------|
| `stable` | Recurring financial obligations (rent, insurance), account registrations, institution relationships |
| `standard` | Active subscription states, current spending patterns, budget preferences (default) |
| `volatile` | One-time transactions, price change events, temporary payment method changes, anomalies |

## Tags

Use tags like: `subscription`, `bill`, `budget`, `account`, `merchant`, `price-change`,
`cancelled`, `recurring`, `preference`, `housing`, `payment-method`

## Example Facts

```python
# From: "I always use my Amex for travel"
memory_store_fact(
    subject="user",
    predicate="preferred_payment_method",
    content="American Express for travel purchases",
    permanence="standard",
    importance=7.0,
    tags=["payment-method", "travel"]
)

# From: "My rent is $2,200 due on the 1st every month"
memory_store_fact(
    subject="user",
    predicate="spending_habit",
    content="rent $2,200/month due on the 1st",
    permanence="stable",
    importance=9.0,
    tags=["bill", "recurring", "housing"]
)

# From: "Remind me 5 days before bills are due"
memory_store_fact(
    subject="user",
    predicate="bill_reminder_preference",
    content="5 days before due date",
    permanence="stable",
    importance=8.0,
    tags=["preference", "bill"]
)

# From detecting a pattern of weekly Whole Foods charges
memory_store_fact(
    subject="Whole Foods",
    predicate="merchant_category",
    content="groceries — user shops weekly, typically $80-$150",
    permanence="standard",
    importance=6.0,
    tags=["merchant", "groceries", "recurring"]
)

# From: "I bank with Ally for savings"
memory_store_fact(
    subject="user",
    predicate="financial_institution",
    content="Ally Bank for savings account",
    permanence="stable",
    importance=7.0,
    tags=["account", "savings"]
)
```
