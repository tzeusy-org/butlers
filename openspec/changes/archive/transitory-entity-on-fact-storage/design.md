## Context

Today, when a butler agent stores a fact about a previously-unknown entity (e.g. "Nutrition Kitchen SG" from an email), the flow is:

1. Agent calls `memory_entity_resolve(name="Nutrition Kitchen SG")` → returns empty list
2. Agent calls `memory_store_fact(subject="Nutrition Kitchen SG", ...)` **without** `entity_id`
3. Fact is stored with string-only anchoring — no entity row in `shared.entities`

The contacts system already has the right pattern: `create_temp_contact()` creates an entity with `metadata.unidentified = true`, which surfaces in the dashboard's "Unidentified Entities" section for user review (merge, confirm, or archive). Memory fact storage needs to follow the same pattern.

The tools (`entity_create`, `entity_resolve`, `store_fact`) already support all the needed mechanics. The gap is purely in the **documented contract** for how agents compose these tools.

## Goals / Non-Goals

**Goals:**
- Ensure every fact stored by an agent is anchored to an `entity_id`, never a bare string subject
- Reuse the existing `metadata.unidentified = true` convention so transitory entities appear in the dashboard without UI changes
- Formalize `metadata.unidentified` as part of the entity-identity spec (currently implicit)
- Define a clear resolve-or-create protocol that all butler agents follow

**Non-Goals:**
- Modifying `memory_entity_resolve` to auto-create entities (it should remain a pure lookup)
- Modifying `memory_entity_create` (it already accepts arbitrary metadata)
- Modifying `memory_store_fact` (it already accepts `entity_id`)
- Building new UI for transitory entity approval (the existing "Unidentified Entities" section + merge/confirm/delete already covers this)
- Backfilling existing string-anchored facts (separate migration task)

## Decisions

### 1. Protocol lives in agent instructions, not in tool code

**Decision:** The resolve-or-create pattern is a behavioral contract documented in specs and butler skill prompts, not enforced programmatically inside `store_fact`.

**Rationale:** `store_fact` is a general-purpose primitive — some callers (like consolidation) legitimately store facts with pre-resolved entity IDs. Forcing entity resolution inside `store_fact` would couple it to `entity_resolve` and break the single-responsibility boundary. The spec requirement applies to *agents storing facts about external entities*, not to all `store_fact` callers.

**Alternative considered:** Adding an `auto_create_entity` flag to `store_fact` that triggers entity creation when `entity_id` is omitted. Rejected because it mixes concerns (fact storage shouldn't know about entity lifecycle) and adds hidden side effects to a storage primitive.

### 2. Reuse `metadata.unidentified = true` — no new status field

**Decision:** Transitory entities use the existing `metadata.unidentified = true` marker, identical to the contacts system's pattern.

**Rationale:** The dashboard already filters on this field to show the "Unidentified Entities" section. Adding a separate `status` column or a different metadata key would require frontend changes and fragment the concept of "pending approval" across two patterns.

### 3. Source provenance in entity metadata

**Decision:** When an agent creates a transitory entity during fact storage, it SHOULD include provenance metadata indicating how the entity was discovered:

```json
{
  "unidentified": true,
  "source": "fact_storage",
  "source_butler": "finance",
  "source_scope": "finance"
}
```

**Rationale:** The contacts system stores `source_channel` and `source_value`. For fact-storage-originated entities, the useful provenance is which butler/scope surfaced the entity. This helps the user understand *why* an unidentified entity appeared when reviewing it in the dashboard.

### 4. Entity type inference

**Decision:** Agents SHOULD infer `entity_type` from context when creating transitory entities:
- Merchant/company names → `organization`
- Person names → `person`
- Locations → `place`
- Unknown → `other`

**Rationale:** Even for transitory entities, having the correct type improves entity resolution for future facts about the same entity (the unique constraint is `(tenant_id, canonical_name, entity_type)`). The agent already has enough context from the fact being stored to make a reasonable inference.

### 5. Idempotency on repeated resolve-miss

**Decision:** If `entity_resolve` returns empty but `entity_create` raises a unique constraint violation (race or repeat processing), the agent MUST catch the error and resolve the existing entity instead of failing.

**Rationale:** Email reprocessing or concurrent butler sessions could hit the same unknown entity name. The `entity_create` function already raises `ValueError` on duplicate `(tenant_id, canonical_name, entity_type)`. The agent should handle this as "entity already exists, use it" rather than an error.

## Risks / Trade-offs

- **Entity sprawl** → Users may accumulate many unidentified entities from email processing. Mitigation: the existing dashboard UI lets users merge or delete them. A future enhancement could auto-merge entities with high-confidence fuzzy matches, but that's out of scope.
- **Name quality** → Agent-inferred canonical names may be inconsistent (e.g. "Nutrition Kitchen SG" vs "Nutrition Kitchen Singapore"). Mitigation: entity resolution's fuzzy matching (trigram similarity) will catch near-duplicates on subsequent resolve calls. Users can also rename via the dashboard.
- **No enforcement** → Since this is a behavioral contract rather than programmatic enforcement, agents could still store string-anchored facts if their prompts aren't updated. Mitigation: update all butler skill prompts and the CRUD-to-SPO predicate taxonomy as part of the rollout tasks.
