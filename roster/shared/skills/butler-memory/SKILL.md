---
name: butler-memory
description: Memory classification framework — entity resolution, permanence levels, tagging strategy, and extraction philosophy
version: 3.0.0
---

### Memory Classification

Extract facts from conversational messages and store them using the butler's domain tools and memory tools.

**Critical: Resolve before storing.** Every fact about an external entity — person, organization, place, or other — MUST be anchored to a resolved entity via `entity_id`. Never store facts using only a raw `subject` string. This ensures that facts about "Tze", "TzeHow Lee", and "Tze How" all resolve to the same identity and are retrievable together.

### Sender Entity Resolution

When the identity preamble contains an `entity_id` (e.g., `[Source: Owner (contact_id: ..., entity_id: <uuid>), via telegram]`), use that `entity_id` for facts **about the sender** — their preferences, health, habits, etc. Do not store `subject="user"` as a string; anchor to the sender's entity.

For unidentified senders (`[Source: Unknown sender (contact_id: ..., entity_id: <uuid>), via telegram -- pending disambiguation]`), an entity is auto-created. Use that `entity_id` to anchor facts. The entity will appear in the dashboard for the owner to identify later.

### Entity Resolution for All External Entities

Before calling `memory_store_fact` for any mention of an external entity — a **person** (other than the sender), **organization** (merchant, company, service), **place**, or any **other** named entity:

1. Call `memory_entity_resolve(name, entity_type=<inferred_type>, context_hints={...})` to get ranked candidates.
2. Apply the disambiguation policy:
   - **Zero candidates:** Create a transitory entity (see "Resolve-or-Create Protocol" below).
   - **Single candidate or top score leads by ≥30 points:** Use the top `entity_id`. If inferred, confirm to the user transparently.
   - **Multiple candidates, gap <30 points:** Ask the user for clarification before storing any facts.
3. Pass the resolved `entity_id` to `memory_store_fact`.

### Entity Type Inference

When creating a transitory entity, infer the correct `entity_type` from context:

| Signal | `entity_type` |
|--------|---------------|
| Merchant, company, brand, service, subscription provider | `organization` |
| Person name | `person` |
| City, venue, address, geographic location | `place` |
| Unknown or ambiguous | `other` |

Using the correct type improves future resolution accuracy — the unique constraint is on `(tenant_id, canonical_name, entity_type)`, so a well-typed entity avoids false collisions.

### Resolve-or-Create Protocol

When `memory_entity_resolve` returns zero candidates:

1. Call `memory_entity_create` with:
   - `canonical_name` — the entity's name as found in the message
   - `entity_type` — inferred from context (see "Entity Type Inference" above)
   - `metadata` — MUST include `unidentified: true` plus source provenance:
     ```json
     {
       "unidentified": true,
       "source": "fact_storage",
       "source_butler": "<butler_name>",
       "source_scope": "<scope>"
     }
     ```
2. Use the returned `entity_id` to anchor the `memory_store_fact` call.
3. The entity appears in the dashboard "Unidentified Entities" section for the owner to confirm, merge, or delete.

**Never fall back to bare string subjects.** A fact stored without `entity_id` is invisible in `/entities` and cannot be merged, linked, or promoted.

### Idempotency on Duplicate Entity Names

If `memory_entity_create` raises a unique constraint violation (entity already exists for this `(tenant_id, canonical_name, entity_type)`), this is not an error. It means the entity was already created by a prior session or concurrent processing:

1. Catch the `ValueError` from `memory_entity_create`.
2. Call `memory_entity_resolve(name, entity_type=<same_type>)` to obtain the existing `entity_id`.
3. Use that `entity_id` to anchor the fact.
4. Do not treat this as a failure — the outcome is correct entity anchoring.

### Permanence

Determines how long facts persist — choose the level that matches how stable the information is:
- `permanent`: Facts unlikely to ever change (identity, birth dates)
- `stable`: Facts that change slowly over months or years (workplace, location, chronic conditions)
- `standard` (default): Current state that may change over weeks or months (active projects, interests)
- `volatile`: Temporary states or rapidly changing information (acute symptoms, time-sensitive reminders)

### Tags

Enable cross-cutting queries and discovery. Choose tags that support finding facts across different contexts.
For `memory_store_fact`, `tags` must be a array of strings (for example `["work", "project-x"]`), not a comma-separated string and not a JSON-encoded string.

### memory_search Input Shape

When filtering by memory types, `types` must be a JSON array/list of singular values:
- Valid: `types=["episode"]`, `types=["fact"]`, `types=["rule"]`, or combinations.
- Invalid: `types="facts"` (string + plural), `types=["facts"]` (plural).

### Extraction Philosophy

Capture facts proactively from conversational messages, even if tangential to the main request. Use appropriate permanence and importance levels to ensure useful recall later. Always anchor to `entity_id` — never to raw name strings.

### Example: Entity-Keyed Fact Storage (Person)

```python
# Step 1: resolve the person mention
candidates = memory_entity_resolve(
    name="Sarah",
    entity_type="person",
    context_hints={"topic": "shellfish, allergy", "domain_scores": {"<uuid-sarah>": 50}}
)
# → single candidate: entity_id="<uuid-sarah>"

# Step 2: store the fact with entity_id
memory_store_fact(
    subject="Sarah",           # human-readable label only
    predicate="food_allergy",
    content="allergic to shellfish",
    entity_id="<uuid-sarah>",  # anchors the fact to the resolved entity
    permanence="stable",
    importance=7.0,
    tags=["health", "dietary"]
)
```

### Example: Transitory Entity for Unknown Organization

Email from "Nutrition Kitchen SG" — entity not yet in the graph:

```python
# Step 1: resolve — returns empty list
candidates = memory_entity_resolve(
    name="Nutrition Kitchen SG",
    entity_type="organization"
)
# → []

# Step 2: create transitory entity with unidentified=true and source provenance
result = memory_entity_create(
    canonical_name="Nutrition Kitchen SG",
    entity_type="organization",
    metadata={
        "unidentified": True,
        "source": "fact_storage",
        "source_butler": "finance",
        "source_scope": "finance"
    }
)
entity_id = result["entity_id"]

# Step 3: store the fact anchored to the new entity
memory_store_fact(
    subject="Nutrition Kitchen SG",     # human-readable label only
    predicate="merchant_category",
    content="meal delivery — weekly subscription",
    entity_id=entity_id,
    permanence="standard",
    importance=6.0,
    tags=["merchant", "food", "subscription"]
)
# The entity now appears in the dashboard "Unidentified Entities" section
# for the owner to confirm, merge, or delete.
```

### Example: Idempotency on Duplicate Creation

```python
try:
    result = memory_entity_create(
        canonical_name="Nutrition Kitchen SG",
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
    # Entity already exists — resolve to get the existing entity_id
    candidates = memory_entity_resolve(
        name="Nutrition Kitchen SG",
        entity_type="organization"
    )
    entity_id = candidates[0]["entity_id"]

# Proceed with fact storage using entity_id
memory_store_fact(
    subject="Nutrition Kitchen SG",
    predicate="merchant_category",
    content="meal delivery — weekly subscription",
    entity_id=entity_id,
    permanence="standard",
    importance=6.0,
    tags=["merchant", "food", "subscription"]
)
```
