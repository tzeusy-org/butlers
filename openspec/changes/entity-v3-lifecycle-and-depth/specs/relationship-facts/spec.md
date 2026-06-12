# Relationship Facts — Entity v3 Delta

This delta extends `relationship-facts` additively: two nullable columns on `relationship.entity_facts`, an immutability rule for `conf`, read-time staleness derivation, and `observed_at` stamping by the central writer. It does not change the existing table contract, indexes, uniqueness, predicate catalog, or migration-safety requirements. Lifecycle semantics that consume these columns live in `relationship-entity-lifecycle`.

## ADDED Requirements

### Requirement: `observed_at` and `metadata` columns

`relationship.entity_facts` SHALL gain two additive nullable columns: `observed_at TIMESTAMPTZ NULL` (when the fact was actually observed, as distinct from `created_at` assertion time) and `metadata JSONB NULL` (structured provenance, e.g. correction lineage `{correction_source, corrected_from}`). The migration MUST be additive-only (no table rewrite, no default backfill in the DDL). A separate idempotent, batched backfill MUST set `observed_at := COALESCE(last_seen, created_at)` for existing rows where `observed_at IS NULL`.

#### Scenario: Additive columns present without rewrite
- **WHEN** the relationship migration chain is at head
- **THEN** `relationship.entity_facts` MUST include nullable `observed_at` and `metadata`
- **AND** existing rows MUST be readable throughout the migration (no exclusive table rewrite lock)

#### Scenario: Backfill is idempotent and batched
- **WHEN** the backfill script runs twice
- **THEN** the second run MUST be a no-op
- **AND** each batch MUST bound its row count so production writes are not starved

### Requirement: Central writer stamps `observed_at`

`relationship_assert_fact()` SHALL accept an optional `observed_at` argument and MUST default it to `now()` when omitted. Supersession carries the new row's own `observed_at`; superseded rows keep theirs.

#### Scenario: Default stamping on assert
- **WHEN** a butler calls `relationship_assert_fact()` without `observed_at`
- **THEN** the written row's `observed_at` MUST be the assertion time
- **WHEN** the caller supplies an explicit `observed_at` (e.g. a backdated import)
- **THEN** the written row MUST carry the supplied value

### Requirement: `conf` is immutable after write

`conf` SHALL be immutable after write: no code path MAY execute an in-place `UPDATE` of `conf` on an existing `relationship.entity_facts` row. Changed certainty is expressed only through the central writer's supersession (prior row `validity = 'superseded'`, new row inserted with the new `conf`). Rationale (binding, brief Phase D amendment 3): merge conflict-resolution keeps higher-`conf` facts; in-place mutation — including any time-based decay — would silently change merge outcomes and contradict the retract-and-replace correction model.

#### Scenario: Decay-style update is rejected
- **WHEN** a change proposes a job that lowers `conf` on rows older than N days
- **THEN** review MUST reject it as a violation of this requirement
- **AND** the correct expression of reduced certainty is supersession or retraction

### Requirement: Predicate cardinality in the registry

`relationship.entity_predicate_registry` SHALL gain a `cardinality TEXT NOT NULL DEFAULT 'multi' CHECK (cardinality IN ('single','multi'))` column, seeded: `single` for `has-birthday` and `dunbar_tier_override`; `multi` for all other contact and relational predicates. Cardinality is the registry-sourced answer to "can an entity legitimately hold two active values for this predicate" — consumed by merge-review divergence computation (`relationship-merge-review`) and available to future validation. No predicate cardinality MAY be hardcoded outside the registry (consistent with the standing no-hardcoded-predicates rule).

#### Scenario: Cardinality drives divergence classification
- **WHEN** two entities both hold active `has-email` facts with different values
- **THEN** the registry's `cardinality = 'multi'` MUST classify this as union-on-merge data, not a conflict
- **WHEN** two entities hold different active `has-birthday` values
- **THEN** `cardinality = 'single'` MUST classify it as a divergence requiring resolution

### Requirement: Supporting tables — `entity_view_marks` and `merge_reviews`

The relationship schema SHALL gain two supporting tables (DDL home is this capability; semantics live in `dashboard-relationship` and `relationship-merge-review` respectively):

- `relationship.entity_view_marks`: `id UUID PK DEFAULT gen_random_uuid()`, `entity_id UUID NOT NULL UNIQUE REFERENCES public.entities(id)`, `marked_at TIMESTAMPTZ NOT NULL`. One mark per entity (owner-only system).
- `relationship.merge_reviews`: `id UUID PK DEFAULT gen_random_uuid()`, `entity_a UUID NOT NULL REFERENCES public.entities(id)`, `entity_b UUID NOT NULL REFERENCES public.entities(id)`, `shared_facts JSONB NOT NULL`, `divergent_facts JSONB NOT NULL`, `outcome TEXT NOT NULL CHECK (outcome IN ('merged','dismissed'))`, `reviewed_at TIMESTAMPTZ NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. FKs MUST NOT cascade-delete: audit rows survive entity tombstoning (post-merge, `entity_b` is tombstoned but the review row is retained as history).

#### Scenario: Audit rows survive the merged-away entity
- **WHEN** entities X and Y are merged with Y tombstoned
- **THEN** the `merge_reviews` row for (X, Y) MUST remain readable
- **AND** no cascade MUST remove it

### Requirement: Read-time staleness derivation is available on fact reads

Endpoints and tools returning `relationship.entity_facts` rows with provenance SHALL be able to return the derived `staleness_band` per the `relationship-entity-lifecycle` identity-store formula (`COALESCE(observed_at, last_seen, created_at)`; fresh ≤ 30d, aging ≤ 180d, stale > 180d; the narrative store has its own column mapping defined there). The derivation MUST happen at read time (SQL expression or application code); no staleness value is ever stored on the row.

#### Scenario: Same row, different bands over time
- **WHEN** a fact observed 20 days ago is read
- **THEN** its `staleness_band` MUST be `fresh`
- **WHEN** the same row is read 200 days after observation with no new writes
- **THEN** its `staleness_band` MUST be `stale`
- **AND** the stored row MUST be byte-identical in both reads
