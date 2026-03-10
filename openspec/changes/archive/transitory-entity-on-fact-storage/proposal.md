## Why

When `memory_entity_resolve` returns no matches during fact storage, agents fall back to legacy string-subject anchoring — the fact's `subject` is a bare string with no `entity_id`. These facts are invisible in `/entities` and cannot be merged, linked, or promoted. The contacts system already solves this: unknown senders get a transitory entity (`metadata.unidentified = true`) that appears in the dashboard for user review. Memory fact storage needs the same pattern so that entities discovered through email processing, transactions, and other ingestion flows are surfaced for approval rather than silently lost as strings.

## What Changes

- **Transitory entity creation on resolve-miss**: When an agent stores a fact and `memory_entity_resolve` returns no candidates, the agent MUST call `memory_entity_create` with `metadata.unidentified = true` before storing the fact, and pass the returned `entity_id` into `memory_store_fact`. This reuses the existing "unidentified entity" pattern from contacts.
- **Predicate taxonomy update**: The CRUD-to-SPO entity resolution cascade's "Unresolved actors" row must be updated to require `metadata.unidentified = true` on auto-created entities, aligning it with the contacts pattern.
- **No new tables or UI changes**: The `/entities` page already has an "Unidentified Entities" section that displays entities with `metadata.unidentified = true`. No frontend work is needed — the transitory entities will appear there automatically.
- **No changes to `memory_entity_resolve` or `memory_entity_create`**: These tools already support the needed behavior. The change is to the documented contract for how agents use them together during fact storage.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `module-memory`: Add a requirement specifying the entity resolution contract for fact storage — when `entity_resolve` returns empty, agents MUST create a transitory entity (`metadata.unidentified = true`) via `entity_create` and anchor the fact to it, rather than falling back to bare string subjects.
- `entity-identity`: Document `metadata.unidentified = true` as the canonical marker for transitory/pending-approval entities (currently only implied by the contacts implementation, not specified in the entity spec itself).

## Impact

- **Specs**: `module-memory/spec.md` (new requirement for entity resolution during fact storage), `entity-identity/spec.md` (formalize unidentified entity metadata convention)
- **Agent behavior**: All butler agents that store facts (finance, health, relationship, home) must follow the resolve-or-create pattern. This is a behavioral contract change, not a code change to the memory module itself.
- **CRUD-to-SPO migration**: The predicate taxonomy's entity resolution cascade (§1.2, §1.4) already describes create-on-miss behavior but doesn't mandate the `unidentified` metadata flag. The delta spec aligns it.
- **Existing data**: String-anchored facts already stored (like the Nutrition Kitchen SG example) will need a one-time backfill to create entities and re-link them. This is a migration task, not a spec change.
