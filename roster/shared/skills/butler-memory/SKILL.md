---
name: butler-memory
description: Memory classification framework — entity resolution, permanence levels, tagging strategy, and extraction philosophy
version: 2.0.0
---

### Memory Classification

Extract facts from conversational messages and store them using the butler's domain tools and memory tools.

**Critical: Resolve before storing.** Every fact about a person MUST be anchored to a resolved entity via `entity_id`. Never store facts using only a raw `subject` string. This ensures facts about "Chloe", "Chloe Wong", and "Chlo" all resolve to the same identity and are retrievable together.

### Entity Resolution Before Fact Storage

Before calling `memory_store_fact` for any person mention:

1. Call `entity_resolve(name, entity_type="person", context_hints={...})` to get ranked candidates.
2. Apply the disambiguation policy:
   - **Zero candidates:** Call `entity_create` to register the new person, then use the returned `entity_id`.
   - **Single candidate or top score leads by ≥30 points:** Use the top `entity_id`. If inferred, confirm to the user transparently.
   - **Multiple candidates, gap <30 points:** Ask the user for clarification before storing any facts.
3. Pass the resolved `entity_id` to `memory_store_fact`.

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

### Example: Entity-Keyed Fact Storage

```python
# Step 1: resolve the person mention
candidates = entity_resolve(
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
