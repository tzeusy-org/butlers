---
name: memory-classification
description: Finance domain memory taxonomy — subject/predicate conventions, permanence levels, tagging strategy, and example facts with entity resolution
version: 2.0.0
---

# Finance Memory Classification

Domain-specific taxonomy for `memory_store_fact()` calls in the finance butler. Consult this
skill when storing memory facts to ensure consistent subject/predicate/tag usage across sessions.

For the full entity resolution protocol — including the resolve-or-create transitory pattern,
disambiguation policy, and idempotency handling — see the `butler-memory` shared skill.

## Resolve Before Storing

**Every fact about a merchant, financial institution, or service provider MUST be anchored to a
resolved entity via `entity_id`.** Never call `memory_store_fact` with only a raw `subject` string
for external entities.

### Finance Domain Entity Type Inference

When calling `memory_entity_resolve` or creating a transitory entity, infer the correct
`entity_type` from context:

| Finance entity | `entity_type` |
|----------------|---------------|
| Merchant (Amazon, Whole Foods, Blue Bottle) | `organization` |
| Financial institution (Ally Bank, Chase, Amex) | `organization` |
| Subscription service (Netflix, Spotify, Adobe) | `organization` |
| Service provider (landlord company, utility, insurer) | `organization` |
| Person (user themselves) | `person` — resolved from sender's `entity_id` in preamble |
| Spending category (dining, subscriptions) | *(no entity required — store as user preference fact)* |

### Resolve-or-Create for Finance Entities

When a merchant or institution is not yet in the entity graph, create a transitory entity:

```python
try:
    result = memory_entity_create(
        canonical_name="Whole Foods",
        entity_type="organization",
        metadata={
            "unidentified": True,
            "source": "fact_storage",
            "source_butler": "finance",
            "source_scope": "finance"
        }
    )
    entity_id = result["entity_id"]
except ValueError:
    # Entity already exists — resolve to get entity_id
    candidates = memory_entity_resolve(name="Whole Foods", entity_type="organization")
    entity_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="Whole Foods",
    predicate="merchant_category",
    content="groceries — user shops weekly, typically $80-$150",
    entity_id=entity_id,
    permanence="standard",
    importance=6.0,
    tags=["merchant", "groceries", "recurring"]
)
```

The entity appears in the dashboard "Unidentified Entities" section for the owner to confirm,
merge, or delete. **Never fall back to bare string subjects.**

## Subject Conventions

- **User preferences and habits**: `"user"` or the user's name — use sender `entity_id` from preamble
- **Merchants**: merchant name (e.g., `"Amazon"`, `"Whole Foods"`) — resolve to `organization` entity
- **Accounts**: account label (e.g., `"Chase Sapphire"`, `"Ally Savings"`) — financial institution is `organization`
- **Subscription services**: service name (e.g., `"Netflix"`, `"Spotify"`) — resolve to `organization` entity
- **Spending categories**: category name (e.g., `"dining"`, `"subscriptions"`) — no entity needed; store as user preference

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
# (user entity_id comes from sender preamble)
memory_store_fact(
    subject="user",
    predicate="preferred_payment_method",
    content="American Express for travel purchases",
    entity_id="<sender_entity_id>",   # from REQUEST CONTEXT preamble
    permanence="standard",
    importance=7.0,
    tags=["payment-method", "travel"]
)

# From: "My rent is $2,200 due on the 1st every month"
memory_store_fact(
    subject="user",
    predicate="spending_habit",
    content="rent $2,200/month due on the 1st",
    entity_id="<sender_entity_id>",
    permanence="stable",
    importance=9.0,
    tags=["bill", "recurring", "housing"]
)

# From: "Remind me 5 days before bills are due"
memory_store_fact(
    subject="user",
    predicate="bill_reminder_preference",
    content="5 days before due date",
    entity_id="<sender_entity_id>",
    permanence="stable",
    importance=8.0,
    tags=["preference", "bill"]
)

# From detecting a pattern of weekly Whole Foods charges
# Step 1: resolve the merchant
candidates = memory_entity_resolve(name="Whole Foods", entity_type="organization")
# → single candidate or create transitory (see Resolve-or-Create above)

memory_store_fact(
    subject="Whole Foods",
    predicate="merchant_category",
    content="groceries — user shops weekly, typically $80-$150",
    entity_id="<whole_foods_entity_id>",   # resolved or created transitory entity
    permanence="standard",
    importance=6.0,
    tags=["merchant", "groceries", "recurring"]
)

# From: "I bank with Ally for savings"
# Step 1: resolve the institution
candidates = memory_entity_resolve(name="Ally Bank", entity_type="organization")
# → use entity_id or create transitory

memory_store_fact(
    subject="Ally Bank",
    predicate="financial_institution",
    content="Ally Bank for savings account",
    entity_id="<ally_entity_id>",
    permanence="stable",
    importance=7.0,
    tags=["account", "savings"]
)

# From: "I cancelled my Adobe subscription"
# Step 1: resolve the subscription service
candidates = memory_entity_resolve(name="Adobe Creative Cloud", entity_type="organization")

memory_store_fact(
    subject="Adobe Creative Cloud",
    predicate="subscription_status",
    content="cancelled by user",
    entity_id="<adobe_entity_id>",
    permanence="standard",
    importance=7.0,
    tags=["subscription", "cancelled"]
)
```
