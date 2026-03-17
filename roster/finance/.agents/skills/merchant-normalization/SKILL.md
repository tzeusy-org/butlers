---
name: merchant-normalization
description: Normalizes messy bank-imported merchant names into clean, consistent labels using aggregate GROUP BY queries. Writes to the normalized_merchant overlay field, preserving original merchant names for provenance. Memory-backed for auto-applying known mappings on future runs.
version: 1.0.0
---

# Merchant Normalization Skill

This skill guides the finance butler through normalizing messy bank-imported merchant
names (e.g., `"WHOLEFDS MKT #10456 AUSTIN TX"`) into clean, consistent labels
(e.g., `"Whole Foods"`). The canonical form is stored in the `normalized_merchant`
metadata overlay field; the original `merchant` field is never modified.

---

## CRITICAL CONSTRAINTS

**READ THIS SECTION FIRST. These are hard rules, not suggestions.**

### MUST NOT: Call list_transactions

You MUST NOT call `list_transactions` or any other tool that reads individual
transaction rows into context at any point during this workflow.

**Reason:** A typical account has hundreds or thousands of transaction rows.
Reading them into context would exhaust the token budget and likely abort the
session before any normalization is applied. The unit of work for normalization
is the *merchant name*, not the individual transaction.

**The only acceptable data source is `list_distinct_merchants`**, which returns a
compact GROUP BY result — typically 50–200 rows even for a year of transactions.

### MUST NOT: Read or re-record transaction rows

You MUST NOT attempt to normalize merchants by reading transactions and writing
them back one row at a time. This is both token-prohibitive and semantically
wrong — normalization is a metadata overlay, not a re-ingestion.

### MUST: Use list_distinct_merchants as the sole data source

All merchant data used in this workflow comes from `list_distinct_merchants`. This
tool returns deduplicated merchant names with transaction counts and totals. It is
the correct and only acceptable source for normalization input.

### MUST: Paginate if distinct merchant count exceeds 500

If the first call returns `total > 500`, you MUST process in paginated batches.
Use `limit=500, offset=0`, then `limit=500, offset=500`, etc., until all merchants
are processed. Do not skip pagination — large accounts can have hundreds of
distinct raw merchant strings.

### MUST: Write to normalized_merchant overlay only

Normalization MUST write to the `normalized_merchant` metadata field via
`bulk_update_transactions`. It MUST NOT modify the original `merchant` field, and
MUST NOT touch `subject`, `predicate`, `content`, or `embedding` columns. The
original `merchant` value is the immutable provenance anchor and deduplication key.

---

## Metadata Overlay Contract

The finance butler's fact layer is **write-once** on core columns. Normalization
uses the metadata overlay pattern instead:

| Field | Role |
|---|---|
| `merchant` | Original raw bank string — never modified, preserved for provenance |
| `normalized_merchant` | Clean canonical name set by this skill — preferred for display and aggregation |

Query and display tools (`spending_summary`, `list_transactions`, dashboard) prefer
`normalized_merchant` when present and fall back to `merchant` when absent. You do
not need to update any query logic — the overlay is transparently consumed.

---

## Workflow

### Step 1: Query Distinct Merchants

Call `list_distinct_merchants` to get the compact merchant list:

```python
result = list_distinct_merchants(
    unnormalized_only=True,   # only merchants without a normalized_merchant overlay
    min_count=2,              # skip one-off merchants unlikely to need normalization
)
```

Inspect the response:
- `total` — total number of distinct unnormalized merchants
- `merchants` — list of `{merchant, count, total_amount}` entries
- If `total > 500`, proceed in paginated batches (see CRITICAL CONSTRAINTS)

If `total == 0`, report "All merchants are already normalized." and stop.

### Step 2: Recall Known Mappings from Memory

Before running any LLM inference, check whether this butler has seen and mapped
any of these merchants before:

```python
memory_search(
    query="merchant normalization alias",
    tags=["merchant-alias"],
    limit=100,
)
```

Also try targeted recall for high-frequency merchants you see in the list:

```python
memory_recall(topic="merchant_alias:WHOLEFDS")
```

**Memory fact schema for aliases** (written in Step 7):
- `subject`: `"merchant_alias:<RAW_PATTERN>"` — the raw bank string or a prefix/pattern
- `predicate`: `"normalizes_to"`
- `content`: `"Whole Foods"` — the clean canonical name
- `tags`: `["merchant-alias", "normalization"]`

Collect all known mappings into a lookup table before proceeding.

### Step 3: Auto-Apply Known Mappings

For every merchant in the list that matches a known alias, call
`bulk_update_transactions` immediately — no LLM review needed:

```python
bulk_update_transactions(updates=[
    {
        "match": {"merchant_pattern": "WHOLEFDS%"},
        "set": {"normalized_merchant": "Whole Foods"},
    },
    {
        "match": {"merchant_pattern": "AMZN MKTP US%"},
        "set": {"normalized_merchant": "Amazon"},
    },
    # ... one entry per known alias
])
```

Track which merchants from the list have been handled so they are excluded from
the LLM review in Step 4.

### Step 4: LLM Normalizes Unknown Merchants

Present the **remaining** merchants (those not covered by known aliases) to
yourself for grouping. For each merchant, you have `count` (transaction frequency)
and `total_amount` (aggregate spend) as context for prioritization.

Group obvious variants into a single canonical name. Apply these heuristics:

- **Strip store numbers and location suffixes**: `"WHOLEFDS MKT #10456 AUSTIN TX"` → `"Whole Foods"`
- **Expand well-known abbreviations**: `"SQ *"` prefix → Square payment, keep merchant after `*`; `"AMZN"` → `"Amazon"`
- **Clean up all-caps**: `"NETFLIX.COM"` → `"Netflix"`, `"SPOTIFY AB"` → `"Spotify"`
- **Group card-reader variants**: `"PAYPAL *MERCHANTNAME"`, `"PAYPAL*MERCHANTNAME"` → same merchant
- **Preserve intentional multi-word names**: do not over-collapse — `"Blue Bottle Coffee"` and `"Philz Coffee"` are separate merchants
- **Use title case** for the canonical form, unless the merchant's brand style is otherwise (e.g., `"eBay"`, `"YouTube"`)
- **When unsure**, use the most human-readable form without inventing information

Produce a mapping list: `[(raw_pattern, canonical_name), ...]`

### Step 5: Apply LLM Normalization

For each group from Step 4, call `bulk_update_transactions`:

```python
bulk_update_transactions(updates=[
    {
        "match": {"merchant_pattern": "SQ *BLUE BOTTLE%"},
        "set": {"normalized_merchant": "Blue Bottle Coffee"},
    },
    {
        "match": {"merchant_pattern": "NETFLIX%"},
        "set": {"normalized_merchant": "Netflix"},
    },
    # ... one entry per canonical group
])
```

**Pattern guidance for `merchant_pattern`:**
- Use SQL ILIKE syntax: `%` matches any sequence of characters, `_` matches one character
- Prefer prefix patterns (`"WHOLEFDS%"`) over exact matches to catch minor variant strings
- For Square-prefixed merchants: `"SQ *BLUE BOTTLE%"` or `"SQ*BLUE BOTTLE%"` covers both space variants
- Avoid overly broad patterns like `"AMAZON%"` if it would also match unrelated merchants; prefer `"AMZN MKTP US%"` or `"AMAZON.COM%"` for precision

Accumulate the `total_matched` and `total_updated` counts returned by each
`bulk_update_transactions` call.

### Step 6: Report to User

After all updates are applied, report a concise summary:

```
Merchant normalization complete.

Distinct merchants processed:  87
  Auto-applied (known aliases):  12  (from memory)
  LLM-normalized:                71
  Skipped (single-occurrence):    4

Transactions updated: 1,243
```

If pagination was used, note the batch count:
```
Processed in 2 batches (1,100 distinct merchants total).
```

### Step 7: Remember New Normalization Rules

For each new mapping produced by the LLM in Step 4 (i.e., not already in memory),
store a memory fact so future runs can auto-apply it:

```python
# Resolve or create the merchant entity first (follow memory-classification skill).
# resolve_or_create_merchant_entity is shorthand for the try/except pattern below —
# use the real MCP calls (as shown in the worked example's Step 7):
try:
    result = memory_entity_create(canonical_name="Whole Foods", entity_type="organization",
                                   metadata={"source": "fact_storage", "source_butler": "finance"})
    entity_id = result["entity_id"]
except ValueError:
    candidates = memory_entity_resolve(name="Whole Foods", entity_type="organization")
    entity_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="merchant_alias:WHOLEFDS",
    predicate="normalizes_to",
    content="Whole Foods",
    entity_id=entity_id,
    permanence="stable",
    importance=7.0,
    tags=["merchant-alias", "normalization"],
)
```

Store one fact per distinct raw-pattern-to-canonical mapping. Use the shortest
prefix that uniquely identifies the merchant family as the subject (e.g.,
`"merchant_alias:WHOLEFDS"` covers all Whole Foods store numbers).

**Entity resolution for merchant facts:** Follow the `memory-classification` skill's
Resolve-or-Create protocol. The canonical name (e.g., `"Whole Foods"`) is the
entity's `canonical_name` with `entity_type="organization"`.

---

## Pagination Reference

When `total > 500`, loop over batches:

```
offset = 0
while True:
    result = list_distinct_merchants(
        unnormalized_only=True,
        min_count=2,
        limit=500,
        offset=offset,
    )
    # ... process result.merchants (Steps 2–5 for this batch)
    if offset + 500 >= result.total:
        break
    offset += 500
```

Process each batch through the full Steps 2–5 pipeline before moving to the next.
Memory recall (Step 2) carries over across batches — load known aliases once before
the loop and reuse them.

---

## Worked Example

**Scenario**: A Chase Checking account was imported last week with 3 months of
transactions. Running `list_distinct_merchants(unnormalized_only=True, min_count=2)`
returns 22 merchants.

### Step 1: Query

```python
result = list_distinct_merchants(unnormalized_only=True, min_count=2)
# Returns:
# total: 22
# merchants: [
#   {merchant: "WHOLEFDS MKT #10456 AUSTIN TX",   count: 14, total_amount: "623.40"},
#   {merchant: "WHOLEFDS MKT #10457 AUSTIN TX",   count:  3, total_amount: "142.10"},
#   {merchant: "WHOLE FOODS #10456",               count:  2, total_amount:  "89.50"},
#   {merchant: "AMZN MKTP US*2K4H3G0",            count:  8, total_amount: "312.17"},
#   {merchant: "AMZN MKTP US*9R2P1Q5",            count:  5, total_amount: "188.44"},
#   {merchant: "NETFLIX.COM",                      count:  3, total_amount:  "47.97"},
#   {merchant: "SQ *BLUE BOTTLE COFFEE",           count:  6, total_amount:  "33.00"},
#   {merchant: "SQ *BLUE BOTTLE COF",              count:  2, total_amount:  "11.00"},
#   {merchant: "SPOTIFY AB",                       count:  3, total_amount:  "29.97"},
#   ... 13 more ...
# ]
```

### Step 2: Recall Known Aliases

```python
memory_search(query="merchant normalization alias", tags=["merchant-alias"], limit=100)
# Returns one prior fact:
# subject: "merchant_alias:AMZN MKTP US", predicate: "normalizes_to", content: "Amazon"
```

Known alias found: `AMZN MKTP US%` → `"Amazon"`

### Step 3: Auto-Apply Known Aliases

```python
bulk_update_transactions(updates=[
    {"match": {"merchant_pattern": "AMZN MKTP US%"}, "set": {"normalized_merchant": "Amazon"}},
])
# Returns: {total_matched: 13, total_updated: 13}
```

Mark the two Amazon variants as handled.

### Step 4: LLM Normalizes Unknowns

Remaining 20 merchants (22 minus 2 Amazon variants). The LLM groups:

| Raw strings | Canonical name | Pattern |
|---|---|---|
| `WHOLEFDS MKT #10456 AUSTIN TX`, `WHOLEFDS MKT #10457 AUSTIN TX`, `WHOLE FOODS #10456` | `Whole Foods` | `WHOLEFDS%` + `WHOLE FOODS%` |
| `NETFLIX.COM` | `Netflix` | `NETFLIX%` |
| `SQ *BLUE BOTTLE COFFEE`, `SQ *BLUE BOTTLE COF` | `Blue Bottle Coffee` | `SQ *BLUE BOTTLE%` |
| `SPOTIFY AB` | `Spotify` | `SPOTIFY%` |
| ... 16 remaining merchants individually reviewed ... | | |

### Step 5: Apply

```python
bulk_update_transactions(updates=[
    {"match": {"merchant_pattern": "WHOLEFDS%"},        "set": {"normalized_merchant": "Whole Foods"}},
    {"match": {"merchant_pattern": "WHOLE FOODS%"},     "set": {"normalized_merchant": "Whole Foods"}},
    {"match": {"merchant_pattern": "NETFLIX%"},         "set": {"normalized_merchant": "Netflix"}},
    {"match": {"merchant_pattern": "SQ *BLUE BOTTLE%"}, "set": {"normalized_merchant": "Blue Bottle Coffee"}},
    {"match": {"merchant_pattern": "SPOTIFY%"},         "set": {"normalized_merchant": "Spotify"}},
    # ... remaining mappings ...
])
# Returns cumulative: {total_matched: 344, total_updated: 344}
```

### Step 6: Report

```
Merchant normalization complete.

Distinct merchants processed:  22
  Auto-applied (known aliases):   2  (Amazon, from memory)
  LLM-normalized:                20
  Skipped (single-occurrence):    0

Transactions updated: 357
  (344 from LLM mapping + 13 from known aliases)
```

### Step 7: Remember

```python
# Resolve Whole Foods entity first
try:
    result = memory_entity_create(canonical_name="Whole Foods", entity_type="organization",
                                   metadata={"unidentified": True, "source": "fact_storage",
                                             "source_butler": "finance", "source_scope": "finance"})
    wf_entity_id = result["entity_id"]
except ValueError:
    candidates = memory_entity_resolve(name="Whole Foods", entity_type="organization")
    wf_entity_id = candidates[0]["entity_id"]

memory_store_fact(
    subject="merchant_alias:WHOLEFDS",
    predicate="normalizes_to",
    content="Whole Foods",
    entity_id=wf_entity_id,
    permanence="stable",
    importance=7.0,
    tags=["merchant-alias", "normalization"],
)

# Repeat for Netflix, Blue Bottle Coffee, Spotify ...
# (Amazon was already in memory — skip)
```

---

## Error Reference

| Situation | Action |
|---|---|
| `list_distinct_merchants` returns `total: 0` with `unnormalized_only=True` | Report "All merchants already normalized." and stop |
| `bulk_update_transactions` returns `total_matched: 0` for a pattern | Pattern may be too specific; broaden with `%` or check for leading/trailing spaces in raw merchant string |
| Memory search returns no results | Proceed directly to LLM normalization; memory will be populated after Step 7 |
| `total > 500` merchants | Paginate in batches of 500 (see Pagination Reference above) |
| Merchant name is ambiguous (could be two different businesses) | Keep separate — do not collapse unless you are certain they are the same merchant |

---

## Relationship to Other Skills

- **Run before `category-inference`**: Normalization collapses merchant variants first.
  The category-inference skill then operates on a cleaner, deduplicated merchant list,
  producing more accurate category assignments.
- **`memory-classification`**: Follow its Resolve-or-Create protocol when storing
  merchant alias facts in Step 7.
- **`tool-reference`**: Consult for exact parameter names and types for
  `list_distinct_merchants` and `bulk_update_transactions`.
