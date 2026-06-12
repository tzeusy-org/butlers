# Relationship Merge Review

New capability. Single-pair merge review: the owner-facing flow that turns deterministic duplicate evidence into an informed merge-or-dismiss decision with an audit trail. Implements the **match** stage's resolution arm of `relationship-entity-lifecycle`. Scope guard (brief §0, binding): single-pair review only — free-form bulk merge remains rejected; no model participates anywhere in this capability.

## ADDED Requirements

### Requirement: Compare endpoint — structural diff only

The dashboard API SHALL expose `POST /api/relationship/entities/compare` accepting `{entity_a, entity_b}` (two entity UUIDs) under the existing owner-only authorization. The response MUST be a server-computed structural diff:

- `a`, `b`: per-entity blocks `{entity: {id, canonical_name, entity_type, aliases, tier (nullable), state}, identity_facts: [...], narrative_facts: [...]}` with full provenance (`src`, `conf`, `verified`, `primary`, `observed_at`, `last_seen` (nullable/omitted on narrative rows), `staleness_band`) on every fact, reading both stores per the `relationship-entity-lifecycle` layering.
- `shared`: **identity-store rows only** where both entities hold an active row with identical `(predicate, object)` — this is the duplicate evidence, listed per pair. Narrative facts never enter `shared` (free-form prose produces no meaningful equality).
- `divergent`: **identity-store rows only**, computed exclusively over predicates with `cardinality = 'single'` in `relationship.entity_predicate_registry` (e.g. two different `has-birthday` values) — the conflicts a merge must resolve. Multi-valued predicates (`cardinality = 'multi'`, e.g. `has-email`) union on merge and MUST NOT appear as divergences — two different emails are two legitimate rows (the three-emails-three-rows rule). Narrative facts appear only in the per-entity `a`/`b` blocks.

The endpoint MUST contain **no scoring, no ranking, no similarity percentage beyond the existing deterministic queue evidence, and no generated text of any kind**.

#### Scenario: Compare returns shared evidence and divergences
- **WHEN** the owner compares two entities that share `(has-email, "alice@x.com")` and hold different `has-birthday` objects
- **THEN** `shared` MUST contain the email pair and `divergent` MUST contain the birthday conflict
- **AND** every fact in `a`, `b`, `shared`, `divergent` MUST carry full provenance

#### Scenario: Compare is owner-only
- **WHEN** a request without owner authorization calls the compare endpoint
- **THEN** the response MUST be the standard `owner_required` error envelope

### Requirement: No model involvement — guardrail-tested

No LLM-provider client, spawner invocation, embedding call, or generated prose MAY appear in the compare, merge, or merge-review code paths. A guardrail test MUST source-scan these handler paths (pattern precedent: the chronicler-boundary test) and fail on any model-call import or invocation. Spec language proposing "summarize the differences", "suggest a merge verdict", or any model-assisted variant MUST be rejected at review and re-enters LLM-cost review (Phase D) if escalated.

#### Scenario: Source-scan keeps the path model-free
- **WHEN** the guardrail test scans the compare/merge handler implementations
- **THEN** it MUST fail if any LLM client, spawner, or embedding import appears in them

### Requirement: Merge-review audit table

The relationship schema SHALL gain `relationship.merge_reviews` (DDL home: the `relationship-facts` delta, which owns relationship-schema storage; this capability defines its semantics): `entity_a`/`entity_b` reference `public.entities(id)`, `shared_facts JSONB` (the evidence snapshot at review time), `divergent_facts JSONB`, `outcome` (`'merged' | 'dismissed'`), `reviewed_at NOT NULL`, `created_at`. Rows are written at commit time only (no `pending` state). Audit rows MUST survive entity tombstoning — after a merge, `entity_b` may be tombstoned; the review row is retained as history (FK without cascade delete). Every merge executed through the review flow and every dismissal MUST write a row. The dismissal row is the suppression key for the queue (per `relationship-entity-lifecycle` queue derivation): the pair stays out of the duplicate bucket until a `{predicate, shared_value}` not present in `shared_facts` arises.

#### Scenario: Merge writes an audit row
- **WHEN** the owner confirms a merge from the review flow
- **THEN** the existing `POST /api/relationship/entities/{id}/merge` MUST execute the merge
- **AND** a `merge_reviews` row with `outcome = 'merged'` and the evidence snapshot MUST be written

#### Scenario: Dismissal suppresses the pair
- **WHEN** the owner dismisses a compared pair
- **THEN** a `merge_reviews` row with `outcome = 'dismissed'` MUST be written
- **AND** the queue MUST stop listing that pair as duplicate-candidate until new shared evidence arises

### Requirement: Single-pair review UX

The review flow SHALL be reachable from these enumerated entry points, and no others: (1) the queue's duplicate-candidate card (its merge action opens the compare view for that pair); (2) the Workbench duplicate warning panel and "shares identifiers with" hint (per `dashboard-relationship`); (3) the Index bulk gutter's merge action, enabled only when exactly two rows are selected; (4) the detail page's `m` key when duplicate evidence exists for the entity; (5) the queue's unidentified-card merge action (standing queue requirement), which opens the compare view for the unidentified entity and an owner-selected target entity. Every entry point routes through the same compare view. The compare view renders the structural diff two-column with shared evidence and divergences grouped; its commit actions are `merge` (choosing the surviving entity) and `dismiss`. Among dashboard entity-merge surfaces, there is no path that merges more than one pair at a time, and no merge without the compare view having been shown. Additionally, `POST /api/relationship/entities/{id}/merge` itself MUST write a `merge_reviews` audit row regardless of entry path, so merges executed outside the dashboard flow (e.g. session-side tooling) still leave history; when no compare context exists, the merge endpoint computes the shared/divergent snapshot server-side at merge time.

#### Scenario: Bulk gutter merge requires exactly two
- **WHEN** the owner selects one row, or three rows, in the Index
- **THEN** the gutter's merge action MUST be disabled
- **WHEN** exactly two rows are selected and merge is clicked
- **THEN** the compare view for that pair MUST open before any merge can be committed

#### Scenario: No merge bypasses review
- **WHEN** any UI surface initiates an entity merge
- **THEN** the compare view MUST have been displayed for that pair in the same flow
- **AND** the resulting merge MUST write its `merge_reviews` audit row
