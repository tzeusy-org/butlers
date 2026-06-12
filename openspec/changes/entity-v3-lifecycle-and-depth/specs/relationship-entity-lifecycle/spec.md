# Relationship Entity Lifecycle

New capability. Normative semantics for the entity data lifecycle — **ingest → match → assert → look up → age** — that every entity endpoint, MCP tool, and dashboard view traces to. Source of intent: `docs/redesigns/2026-06-12-entity-brief-v3.md` §0 (binding) and §3 (lifecycle current-state audit). This spec names the semantics; storage details remain in `relationship-facts`; view behavior remains in `dashboard-relationship`.

## ADDED Requirements

### Requirement: Lifecycle stages are normative

The entity data lifecycle SHALL be specified as five stages — **ingest** (signals become entities and facts), **match** (observations resolve to existing entities or surface ambiguity), **assert** (facts land through a single writer with provenance), **look up** (facts are read by the owner UI, switchboard resolution, and butler MCP lookup), **age** (facts acquire read-time staleness; entities acquire queue states). Every new entity-surface endpoint, tool, or view MUST identify which stage(s) it implements, and behavior not traceable to a stage of this spec is a review-rejectable spec gap.

#### Scenario: New endpoint must cite its lifecycle stage
- **WHEN** a PR adds an entity-scoped endpoint or MCP tool to the relationship butler
- **THEN** its spec delta MUST name the lifecycle stage(s) it implements
- **AND** review MUST reject endpoints whose behavior contradicts the named stage's requirements

### Requirement: Ingest — entity and fact creation paths

Entities and facts SHALL be created only through these paths: (a) the fact-extraction pipeline running inside already-triggered relationship sessions (`roster/relationship/.agents/skills/fact-extraction/SKILL.md`), using `memory_entity_resolve()` for deterministic candidate scoring and `memory_entity_create()` (with `metadata.unidentified = true` when resolution returns no candidate); (b) the central writer `relationship_assert_fact()` for identity/relational triples (per `relationship-facts`); (c) owner actions in the dashboard (promote, edit, merge); (d) the Switchboard's standing temporary-contact creation flow for unknown senders (entity + contact rows only — never facts; per `switchboard-identity`). Connectors MUST stamp `point_events.entity_id` when the sender resolves; connectors MAY read `relationship.entity_facts` (e.g. priority-contact policy lookups) but MUST NOT create entities or write facts.

#### Scenario: Unknown sender enters the lifecycle through the temp-contact path
- **WHEN** an inbound message's sender cannot be resolved to an existing entity
- **THEN** the Switchboard's temp-contact flow MAY create the entity (path d) before routing
- **AND** fact assertion for the observed identifier MUST happen inside the routed relationship session (path a/b)
- **AND** the unidentified entity MUST appear in the curation queue's unidentified bucket

#### Scenario: Connectors never write facts
- **WHEN** the source tree under `src/butlers/connectors/` is scanned by the guardrail test
- **THEN** no connector code MUST call `relationship_assert_fact` or issue `INSERT`/`UPDATE`/`DELETE` against `relationship.entity_facts`
- **AND** read-only SQL against `relationship.entity_facts` (existing policy lookups) remains permitted

### Requirement: Match — deterministic matching only

Entity matching SHALL be deterministic at every point in the system. Permitted signals: exact shared-identifier detection on contact predicates (`has-email`, `has-phone`, and any future members of the duplicate-detection predicate set), explicit metadata flags, and rule-based salience scoring in `memory_entity_resolve()`. **No LLM, embedding service, or model call MAY participate in matching, duplicate detection, or merge-candidate scoring — including background or scheduled jobs.** (Brief §0 binding rejection; the user explicitly declined LLM matching.)

#### Scenario: Duplicate candidates arise only from deterministic evidence
- **WHEN** two entities hold active facts with the same predicate in the duplicate-detection set and an identical `object` value
- **THEN** both entities MUST be classified `duplicate-candidate` with evidence `{predicate, shared_value, peer_entity_ids}`
- **AND** the evidence string MUST be deterministic (no generated prose)

#### Scenario: No model call in any matching path
- **WHEN** the matching, queue-derivation, and compare code paths are source-scanned
- **THEN** no LLM-provider client, spawner invocation, or embedding call MUST appear in them

### Requirement: Match — queue bucket derivation

The curation queue SHALL derive exactly three buckets, in priority order **unidentified > duplicate-candidate > stale** (one bucket per entity): unidentified = `metadata->>'unidentified' = 'true'`; duplicate-candidate = explicit flag OR shared-identifier evidence; stale = no active fact with `last_seen` within 365 days. A dismissed duplicate pair (per `relationship-merge-review`) MUST be suppressed from the duplicate bucket until new shared evidence (a different `{predicate, shared_value}`) arises.

#### Scenario: One bucket per entity, highest priority wins
- **WHEN** an entity is both unidentified and stale
- **THEN** the queue MUST list it once, in the unidentified bucket

#### Scenario: Dismissed pair re-raises only on new evidence
- **WHEN** a duplicate pair was dismissed with evidence `{has-email, "a@x.com"}` and the same shared value is observed again
- **THEN** the pair MUST NOT re-enter the duplicate bucket
- **WHEN** the same pair later shares a different value (e.g. `{has-phone, "+65..."}`)
- **THEN** the pair MUST re-enter the duplicate bucket with the new evidence

### Requirement: Assert — supersession and immutable confidence

Fact assertion SHALL follow the central-writer contract in `relationship-facts`, with these lifecycle semantics binding: `conf` is **assertion-time certainty and is immutable** — no code path MAY update `conf` in place after a fact row is written; changed certainty is expressed by superseding (old row `validity = 'superseded'`, new row inserted). Corrections are explicit retract-and-replace (per `module-memory` correction-driven retraction), never gradual mutation. Rationale: merge conflict-resolution keeps higher-`conf` facts (`src/butlers/modules/memory/tools/entities.py:834-837`); in-place decay would silently flip merge winners.

#### Scenario: Confidence never changes in place
- **WHEN** a fact is re-asserted with a different `conf`
- **THEN** the prior row MUST transition to `validity = 'superseded'`
- **AND** a new row MUST be inserted carrying the new `conf`
- **AND** no `UPDATE` of `conf` on an existing row MUST occur anywhere in the codebase

### Requirement: Look up — three read surfaces with declared store layering

Entity-knowledge lookup SHALL be served by exactly three lookup surfaces (connector read-only policy checks under the Ingest requirement are not lookup surfaces — they return no entity knowledge to a caller): (1) the owner UI via the relationship dashboard endpoints (`dashboard-relationship`); (2) switchboard channel resolution via `resolve_contact_by_channel()` (read-only, per `relationship-facts` and `switchboard-identity`); (3) butler programmatic lookup via the `relationship_lookup` MCP tool (`relationship-entity-lookup`). Read surfaces that return an entity's knowledge MUST read **both stores with declared layering**: `relationship.entity_facts` is canonical for identity-contact triples AND for all relational predicates registered in `relationship.entity_predicate_registry` (`knows`, `family-of`, ...); the memory-module `facts` table (unqualified table name — it lives in the mounting butler's schema, `relationship` in production) is canonical for narrative facts, episodes, and memory edge-facts **outside the predicate registry** (free-form relationship records such as `works_at` narrative context, per `entity-identity` dual-mode facts; registry-predicate relationships always live in `relationship.entity_facts`). For identity questions (who is this, how do I reach them, verified channels) identity facts rank above narrative facts. The two stores MUST NOT be cross-joined in SQL (existing `relationship-facts` schema boundary — guardrails target the table identities, not a `memory.` schema prefix); layering happens in application code.

#### Scenario: Lookup returns layered facts from both stores
- **WHEN** `relationship_lookup` resolves an entity that has both identity triples and narrative facts
- **THEN** the response MUST include facts from both stores, each labeled with its store of origin
- **AND** identity facts MUST be ranked before narrative facts in the response ordering

#### Scenario: No cross-join between stores
- **WHEN** any lookup implementation is reviewed
- **THEN** no SQL statement MUST join `relationship.entity_facts` with the memory-module `facts` table

### Requirement: Age — read-time staleness derivation

Staleness SHALL be a read-time computation, never stored, with per-store timestamp mapping: identity store (`relationship.entity_facts`) — `staleness_days = now() - COALESCE(observed_at, last_seen, created_at)`; narrative store (memory-module `facts` table, which has no `last_seen` column) — `staleness_days = now() - COALESCE(observed_at, last_confirmed_at, created_at)`. Bands: `fresh` (≤ 30 days), `aging` (≤ 180 days), `stale` (> 180 days). Every read surface that returns provenance MUST be able to return the derived `staleness_band`. The queue's stale bucket criterion (365 days without `last_seen`) is unchanged and derives from the same inputs. **For `relationship.entity_facts`: no stored confidence decay and no background mutation of fact rows for aging purposes.** The memory module's standing permanence/decay lifecycle (decay sweep, decay timers per the `module-memory` base spec) continues to govern the narrative store unchanged; staleness bands there are computed read-time over its currently-active rows. Staleness (time-since-observed) and confidence (assertion-time certainty) are separate axes and MUST be rendered as separate signals wherever both appear.

#### Scenario: Staleness falls back through the timestamp chain
- **WHEN** an identity fact has `observed_at = NULL` and `last_seen = 2026-01-01`
- **THEN** its staleness MUST be computed from `last_seen`
- **WHEN** an identity fact has both `observed_at` and `last_seen`
- **THEN** its staleness MUST be computed from `observed_at`
- **AND** a narrative fact's staleness MUST be computed from `COALESCE(observed_at, last_confirmed_at, created_at)`

#### Scenario: Aging never mutates identity-store rows
- **WHEN** the system runs for any duration with no new observations of a `relationship.entity_facts` row
- **THEN** that row's stored `conf`, `validity`, and timestamps MUST be unchanged
- **AND** only its derived `staleness_band` reflects the passage of time
- **AND** the memory module's decay sweep over the narrative store remains permitted per its base spec

### Requirement: Canonical fact-store layering is binding project-wide

The two-store layering declared in this spec (registry identity/relational triples in `relationship.entity_facts`; narrative facts, episodes, and non-registry edge-facts in the memory-module `facts` table) SHALL be the single project-wide answer to "where does a fact live." Provenance display, merge review, compare, butler lookup, and any future entity-knowledge consumer MUST follow it. Identity-contact predicate data MUST NOT be written to the memory-module `facts` table (see `module-memory` delta); narrative facts MUST NOT be written to `relationship.entity_facts`.

#### Scenario: Identity predicate attempted against the narrative store is rejected at the writer
- **WHEN** `memory_store_fact` is called with content shaped as a registry identity predicate (e.g. a `has-email` channel identifier)
- **THEN** the writer-side boundary check MUST reject or route the assertion to `relationship_assert_fact()`
- **AND** no identity-predicate row MUST land in the memory-module `facts` table
