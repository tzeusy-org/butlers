---
name: category-inference
description: Assigns spending categories to uncategorized transactions using aggregate GROUP BY queries. Writes to the inferred_category overlay field, preserving explicit category values. Memory-backed for auto-applying known merchant→category mappings on future runs. Should run after merchant normalization.
version: 1.0.0
---

# Category Inference Skill

This skill guides the finance butler through assigning spending categories to
transactions that lack an explicit category. Categories are inferred per-merchant
and stored in the `inferred_category` metadata overlay field. The original
`category` field (set during CSV import or email ingestion) is never modified.

---

## CRITICAL CONSTRAINTS

**READ THIS SECTION FIRST. These are hard rules, not suggestions.**

### MUST NOT: Call list_transactions

You MUST NOT call `list_transactions` or any other tool that reads individual
transaction rows into context at any point during this workflow.

**Reason:** Categorization is per-merchant, not per-transaction. "Whole Foods" is
always "groceries" regardless of which specific transaction. A typical account has
hundreds or thousands of transaction rows — reading them into context would exhaust
the token budget and likely abort the session before any categories are applied.

**The only acceptable data source is `list_distinct_merchants`**, which returns a
compact GROUP BY result — typically 50–200 rows even for a year of transactions.

### MUST NOT: Read or re-record transaction rows

You MUST NOT attempt to categorize by reading transactions and writing them back
one row at a time. This is both token-prohibitive and semantically wrong —
category inference is a metadata overlay, not a re-ingestion.

### MUST: Use list_distinct_merchants as the sole data source

All merchant data used in this workflow comes from `list_distinct_merchants`. This
tool returns deduplicated merchant names with transaction counts and totals. It is
the correct and only acceptable source for categorization input.

### MUST: Paginate if distinct merchant count exceeds 500

If the first call returns `total > 500`, you MUST process in paginated batches.
Use `limit=500, offset=0`, then `limit=500, offset=500`, etc., until all merchants
are categorized. Do not skip pagination — large accounts can have hundreds of
distinct merchant names.

### MUST: Write to inferred_category overlay only

Category inference MUST write to the `inferred_category` metadata field via
`bulk_update_transactions`. It MUST NOT overwrite the original `category` field,
and MUST NOT touch `subject`, `predicate`, `content`, or `embedding` columns.

---

## Metadata Overlay Contract

The finance butler's fact layer is **write-once** on core columns. Category
inference uses the metadata overlay pattern instead:

| Field | Role |
|---|---|
| `category` | Original explicit category from CSV or email ingestion — never modified |
| `inferred_category` | LLM-assigned category set by this skill — preferred when no explicit category exists |

Query and display tools (`spending_summary`, `list_transactions`, dashboard) apply
the following precedence when resolving a transaction's effective category:

```
category (explicit) > inferred_category (LLM-assigned) > uncategorized
```

You do not need to update any query logic — the overlay is transparently consumed.

---

## Standard Category Taxonomy

Use exactly these category labels. Do not invent new categories.

| Label | Examples |
|---|---|
| `groceries` | Supermarkets, grocery delivery, wholesale clubs (Costco, Sam's Club) |
| `dining` | Restaurants, cafes, bars, food delivery (Uber Eats, DoorDash, Grubhub) |
| `transport` | Gas stations, ride-share (Uber, Lyft), parking, tolls, public transit, auto services |
| `subscriptions` | Streaming (Netflix, Spotify, Disney+), SaaS, news, cloud storage |
| `entertainment` | Movie theaters, concerts, events, gaming, amusement parks |
| `utilities` | Electricity, gas, water, internet, mobile phone, waste collection |
| `healthcare` | Pharmacies, doctors, dentists, labs, insurance premiums, gym (when wellness-focused) |
| `shopping` | Retail (Amazon, Target, department stores), online marketplaces, home goods |
| `travel` | Hotels, airlines, travel agencies, car rentals, vacation bookings |
| `education` | Tuition, course platforms (Coursera, Udemy), textbooks, tutoring |
| `personal` | Haircuts, beauty, spa, laundry, personal care not covered elsewhere |
| `other` | Anything that does not fit the above — use sparingly; prefer a specific label |

---

## Ordering

**This skill SHOULD run AFTER merchant normalization.** Running after normalization
means distinct merchants are already collapsed into canonical names (e.g., the
three Whole Foods store variants are a single `"Whole Foods"` entry). This reduces
the merchant list size and produces more accurate, consistent categorization.

---

## Workflow

### Step 1: Query Distinct Merchants

Call `list_distinct_merchants` to get the compact merchant list:

```python
result = list_distinct_merchants(min_count=1)
```

Use `normalized_merchant` values when available (post-normalization). The tool
returns `normalized_merchant` in each entry when the overlay has been set.

Inspect the response:
- `total` — total number of distinct merchants
- `merchants` — list of `{merchant, normalized_merchant (if set), count, total_amount}` entries
- If `total > 500`, proceed in paginated batches (see CRITICAL CONSTRAINTS)

If `total == 0`, report "No merchants found." and stop.

### Step 2: Recall Known Categories from Memory

Before running any LLM inference, check whether this butler has seen and
categorized any of these merchants before:

```python
memory_search(
    query="merchant category inference",
    tags=["merchant-category"],
    limit=100,
)
```

Also try targeted recall for high-frequency merchants you see in the list:

```python
memory_recall(topic="merchant_category:Netflix")
```

**Memory fact schema for merchant categories** (written in Step 7):
- `subject`: `"merchant_category:<CANONICAL_NAME>"` — the clean canonical merchant name
- `predicate`: `"merchant_category"`
- `content`: `"subscriptions"` — the assigned category label
- `tags`: `["merchant-category", "category-inference", "<category-label>"]`

Collect all known mappings into a lookup table before proceeding.

### Step 3: Auto-Apply Known Categories

For every merchant in the list that matches a known category mapping, call
`bulk_update_transactions` immediately — no LLM review needed:

```python
bulk_update_transactions(updates=[
    {
        "match": {"merchant_pattern": "Netflix%"},
        "set": {"inferred_category": "subscriptions"},
    },
    {
        "match": {"merchant_pattern": "Whole Foods%"},
        "set": {"inferred_category": "groceries"},
    },
    # ... one entry per known merchant→category mapping
])
```

Track which merchants from the list have been handled so they are excluded from
the LLM review in Step 4.

### Step 4: LLM Categorizes Unknown Merchants

Present the **remaining** merchants (those not covered by known mappings) to
yourself for categorization. For each merchant, you have `count` (transaction
frequency) and `total_amount` (aggregate spend) as context.

Assign each merchant exactly one category from the standard taxonomy above.
Apply these heuristics:

- **Use normalized_merchant when available** — it is the canonical name and gives
  better signal than the raw bank string
- **High-frequency merchants deserve careful review** — sort by `count` descending
  and prioritize the top entries; a miscategorization at 50 transactions has more
  impact than one at 2 transactions
- **Grocery vs shopping boundary**: wholesale clubs (Costco, Sam's Club) → `groceries`;
  general merchandise retailers (Target, Walmart, Amazon) → `shopping` unless the
  merchant is clearly a grocery-only outlet
- **Delivery services**: food delivery apps (DoorDash, Uber Eats) → `dining`; package
  delivery companies (FedEx, UPS) → `shopping` (proxy for the underlying purchase)
- **Uber/Lyft**: default to `transport`; if you see strong evidence of Uber Eats
  charges, use `dining`
- **Amazon**: default to `shopping` (catches most cases); do not attempt to
  subcategorize Amazon purchases
- **Gym memberships**: `healthcare` if clearly a fitness facility; `subscriptions`
  if it is a streaming/digital wellness app
- **When genuinely ambiguous**: prefer `other` over a forced fit, and note the
  ambiguity in the report

Produce a list of assignments: `[(merchant_pattern, category), ...]`

### Step 5: Apply Category Assignments

For each assignment from Step 4, call `bulk_update_transactions`:

```python
bulk_update_transactions(updates=[
    {
        "match": {"merchant_pattern": "Trader Joe%"},
        "set": {"inferred_category": "groceries"},
    },
    {
        "match": {"merchant_pattern": "UBER%"},
        "set": {"inferred_category": "transport"},
    },
    # ... one entry per merchant
])
```

**Pattern guidance for `merchant_pattern`:**
- Use SQL ILIKE syntax: `%` matches any sequence, `_` matches one character
- When operating on normalized merchants (post-normalization run), prefer exact
  prefix matches — the names are already clean: `"Netflix%"`, `"Whole Foods%"`
- When operating on raw merchants (normalization skipped), use broader patterns
  to catch bank-string variants: `"NETFLIX%"`, `"WHOLEFDS%"`, `"WHOLE FOODS%"`
- Keep patterns specific enough to avoid cross-merchant collisions

Accumulate the `total_matched` and `total_updated` counts returned by each
`bulk_update_transactions` call.

### Step 6: Report to User

After all updates are applied, report a concise summary:

```
Category inference complete.

Distinct merchants processed:  54
  Auto-applied (known mappings):  8  (from memory)
  LLM-categorized:               46

Transactions updated: 892

Breakdown by category:
  groceries      187  (21%)
  dining         162  (18%)
  subscriptions   94  (11%)
  shopping       201  (23%)
  transport       89  (10%)
  utilities       47   (5%)
  healthcare      38   (4%)
  entertainment   31   (3%)
  other           43   (5%)
```

If pagination was used, note the batch count:
```
Processed in 2 batches (1,100 distinct merchants total).
```

### Step 7: Remember New Category Mappings

For each new merchant→category mapping produced by LLM in Step 4 (i.e., not
already in memory), store a memory fact so future runs can auto-apply it:

```python
# Resolve or create the merchant entity first (follow memory-classification skill).
try:
    result = memory_entity_create(
        canonical_name="Trader Joe's",
        entity_type="organization",
        metadata={
            "unidentified": True,
            "source": "fact_storage",
            "source_butler": "finance",
            "source_scope": "finance",
        }
    )
    entity_id = result["entity_id"]
except ValueError:
    candidates = memory_entity_resolve(name="Trader Joe's", entity_type="organization")
    entity_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="merchant_category:Trader Joe's",
    predicate="merchant_category",
    content="groceries",
    entity_id=entity_id,
    permanence="stable",
    importance=7.0,
    tags=["merchant-category", "category-inference", "groceries"],
)
```

Store one fact per distinct merchant→category mapping. Use the canonical
(normalized) merchant name as the subject key.

**Entity resolution for merchant facts:** Follow the `memory-classification` skill's
Resolve-or-Create protocol. The canonical name (e.g., `"Trader Joe's"`) is the
entity's `canonical_name` with `entity_type="organization"`.

---

## Pagination Reference

When `total > 500`, loop over batches:

```
offset = 0
known_mappings = {}  # Load once from memory (Step 2) before loop

while True:
    result = list_distinct_merchants(
        min_count=1,
        limit=500,
        offset=offset,
    )
    # Process result.merchants through Steps 3–5 for this batch
    # (known_mappings already loaded before the loop — do NOT re-run Step 2 here)

    if offset + 500 >= result.total:
        break
    offset += 500
```

Memory recall (Step 2) should be performed once before the loop and the resulting
lookup table reused across batches. This avoids redundant memory queries and
ensures consistent auto-apply decisions across the full merchant list.

---

## Worked Example

**Scenario**: A Chase Checking account was imported two weeks ago and
merchant normalization was already run. Running `list_distinct_merchants(min_count=1)`
returns 18 normalized merchants ready for categorization.

### Step 1: Query

```python
result = list_distinct_merchants(min_count=1)
# Returns:
# total: 18
# merchants: [
#   {merchant: "WHOLEFDS MKT #10456", normalized_merchant: "Whole Foods",    count: 17, total_amount: "765.50"},
#   {merchant: "AMZN MKTP US*2K4H3G0", normalized_merchant: "Amazon",        count: 13, total_amount: "500.61"},
#   {merchant: "NETFLIX.COM",           normalized_merchant: "Netflix",       count:  3, total_amount:  "47.97"},
#   {merchant: "SQ *BLUE BOTTLE COFFEE", normalized_merchant: "Blue Bottle Coffee", count: 8, total_amount:  "44.00"},
#   {merchant: "SPOTIFY AB",            normalized_merchant: "Spotify",       count:  3, total_amount:  "29.97"},
#   {merchant: "UBER * TRIP",           normalized_merchant: "Uber",          count: 11, total_amount: "187.40"},
#   {merchant: "DOORDASH*CHIPOTLE",     normalized_merchant: "DoorDash",      count:  5, total_amount:  "94.25"},
#   {merchant: "TRADER JOE S",          normalized_merchant: "Trader Joe's",  count:  9, total_amount: "312.80"},
#   {merchant: "PGE BILL PAYMENT",      normalized_merchant: "PG&E",          count:  1, total_amount: "124.00"},
#   ... 9 more ...
# ]
```

### Step 2: Recall Known Categories

```python
memory_search(query="merchant category inference", tags=["merchant-category"], limit=100)
# Returns two prior facts:
# subject: "merchant_category:Netflix",      predicate: "merchant_category", content: "subscriptions"
# subject: "merchant_category:Whole Foods",  predicate: "merchant_category", content: "groceries"
```

Known mappings found:
- `Whole Foods` → `groceries`
- `Netflix` → `subscriptions`

### Step 3: Auto-Apply Known Mappings

```python
bulk_update_transactions(updates=[
    {"match": {"merchant_pattern": "Whole Foods%"}, "set": {"inferred_category": "groceries"}},
    {"match": {"merchant_pattern": "Netflix%"},     "set": {"inferred_category": "subscriptions"}},
])
# Returns: {total_matched: 20, total_updated: 20}
# (17 Whole Foods + 3 Netflix)
```

Mark Whole Foods and Netflix as handled.

### Step 4: LLM Categorizes Unknowns

Remaining 16 merchants. The LLM assigns:

| Normalized merchant | Category | Reasoning |
|---|---|---|
| `Amazon` | `shopping` | General merchandise marketplace |
| `Blue Bottle Coffee` | `dining` | Café |
| `Spotify` | `subscriptions` | Music streaming service |
| `Uber` | `transport` | Ride-share (no Uber Eats signal in merchant name) |
| `DoorDash` | `dining` | Food delivery app |
| `Trader Joe's` | `groceries` | Grocery store |
| `PG&E` | `utilities` | Electric utility |
| ... 9 remaining merchants ... | | |

### Step 5: Apply

```python
bulk_update_transactions(updates=[
    {"match": {"merchant_pattern": "Amazon%"},         "set": {"inferred_category": "shopping"}},
    {"match": {"merchant_pattern": "Blue Bottle%"},    "set": {"inferred_category": "dining"}},
    {"match": {"merchant_pattern": "Spotify%"},        "set": {"inferred_category": "subscriptions"}},
    {"match": {"merchant_pattern": "Uber%"},           "set": {"inferred_category": "transport"}},
    {"match": {"merchant_pattern": "DoorDash%"},       "set": {"inferred_category": "dining"}},
    {"match": {"merchant_pattern": "Trader Joe%"},     "set": {"inferred_category": "groceries"}},
    {"match": {"merchant_pattern": "PG&E%"},           "set": {"inferred_category": "utilities"}},
    # ... remaining 9 merchants ...
])
# Returns cumulative: {total_matched: 471, total_updated: 471}
```

### Step 6: Report

```
Category inference complete.

Distinct merchants processed:  18
  Auto-applied (known mappings):   2  (Whole Foods, Netflix — from memory)
  LLM-categorized:                16

Transactions updated: 491
  (20 from known mappings + 471 from LLM categorization)

Breakdown by category:
  groceries       242  (49%)
  shopping        178  (36%)
  dining           24   (5%)
  transport        11   (2%)
  subscriptions     6   (1%)
  utilities         1   (<1%)
  other            29   (6%)
```

### Step 7: Remember

```python
# Resolve Amazon entity
try:
    result = memory_entity_create(canonical_name="Amazon", entity_type="organization",
                                   metadata={"unidentified": True, "source": "fact_storage",
                                             "source_butler": "finance", "source_scope": "finance"})
    amazon_id = result["entity_id"]
except ValueError:
    candidates = memory_entity_resolve(name="Amazon", entity_type="organization")
    amazon_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="merchant_category:Amazon",
    predicate="merchant_category",
    content="shopping",
    entity_id=amazon_id,
    permanence="stable",
    importance=7.0,
    tags=["merchant-category", "category-inference", "shopping"],
)

# Repeat for Blue Bottle Coffee, Spotify, Uber, DoorDash, Trader Joe's, PG&E ...
# (Netflix and Whole Foods were already in memory — skip)
```

---

## Error Reference

| Situation | Action |
|---|---|
| `list_distinct_merchants` returns `total: 0` | Report "No merchants found." and stop |
| `bulk_update_transactions` returns `total_matched: 0` for a pattern | Pattern may be too specific or merchant has no transactions; skip and note in report |
| Memory search returns no results | Proceed directly to LLM categorization; memory will be populated after Step 7 |
| `total > 500` merchants | Paginate in batches of 500 (see Pagination Reference above) |
| Merchant name is ambiguous (could be multiple categories) | Use the most common interpretation; note ambiguity in report; use `other` only as a last resort |
| Transaction already has explicit `category` field | Do not overwrite it — `bulk_update_transactions` writes to `inferred_category` only; the overlay contract handles precedence automatically |

---

## Relationship to Other Skills

- **Run after `merchant-normalization`**: Normalization collapses merchant variants
  into canonical names first. Category inference then operates on a cleaner,
  deduplicated list, producing more accurate and consistent assignments.
- **`monthly-spending-summary`**: This scheduled skill groups transactions by
  effective category. `inferred_category` directly improves its output quality
  by ensuring uncategorized transactions appear under the correct category bucket.
- **`memory-classification`**: Follow its Resolve-or-Create protocol when storing
  merchant category facts in Step 7. The `merchant_category` predicate is the
  canonical predicate for merchant→category mappings.
- **`tool-reference`**: Consult for exact parameter names and types for
  `list_distinct_merchants` and `bulk_update_transactions`.
