# Relationship Entity Lookup

New capability. The butler-facing read contract for entity knowledge: the `relationship_lookup` MCP tool. Implements the **look up** stage of `relationship-entity-lifecycle` for programmatic consumers. Until this capability, butlers could write facts (`relationship_assert_fact`) but had no symmetric read path; this tool closes that gap so any butler can contextualize advice with the owner's relationship to a person, vendor, company, or place. Cost posture per brief Phase D: the tool itself is deterministic; the only LLM cost lives at the caller, which is why the in-session-only constraint below is load-bearing.

## ADDED Requirements

### Requirement: `relationship_lookup` MCP tool contract

The relationship butler SHALL expose an MCP tool `relationship_lookup(entity_id=None, entity_ref=None)` accepting exactly one of: `entity_id` (UUID) or `entity_ref` (a name/alias/contact-value string). `entity_ref` resolution MUST use the same deterministic ranking rules as `GET /api/relationship/entities/search` (no model call). The response MUST contain:

- `entity`: `{id, canonical_name, entity_type, aliases, roles, tier (nullable — tier exists only when a `dunbar_tier_override` fact is pinned), state}`.
- `facts`: active facts from **both stores** per the `relationship-entity-lifecycle` layering — each `{store: 'identity' | 'narrative', predicate, object, object_kind, src, conf, verified, primary, observed_at, last_seen (nullable; omitted on narrative rows, which have no such column), staleness_band}` — identity facts ordered before narrative facts.
- `recency`: `{last_seen, last_interaction_at, staleness_band}` for the entity as a whole.
- `resolution`: when `entity_ref` was used — `{matched_on, score, ambiguous: bool, candidates: [{id, canonical_name, score}]}` (top 3 candidates when ambiguous).

#### Scenario: Lookup by reference resolves deterministically
- **WHEN** a butler session calls `relationship_lookup(entity_ref="Northwind Plumbing")`
- **THEN** resolution MUST follow the deterministic search ranking defined in the `dashboard-relationship` Finder requirement (prefix 100 > contact-value 70 > substring 50 > predicate label 30, `last_seen DESC` then `tier ASC` tie-break)
- **AND** the response MUST include `resolution.matched_on` and `resolution.score`

#### Scenario: Ambiguous reference returns candidates, not a guess
- **WHEN** `entity_ref` matches multiple entities with equal top score
- **THEN** `resolution.ambiguous` MUST be `true` with up to 3 `candidates`
- **AND** `entity` MUST be `null` and `facts` MUST be empty — the caller re-invokes with an explicit `entity_id`
- **AND** no fact for any candidate MUST be returned (a butler acting on the wrong person's channels is the failure this prevents)

#### Scenario: Facts carry provenance and staleness
- **WHEN** any lookup succeeds
- **THEN** every fact row MUST include `src`, `conf`, `verified`, `observed_at`/`last_seen`, and the derived `staleness_band`
- **AND** no provenance field MUST be silently omitted

### Requirement: Lookup is read-only

`relationship_lookup` MUST NOT write, mutate, or schedule anything: no fact writes, no `last_seen` touch, no view-mark updates, no pending actions, no session spawns. Repeated identical calls MUST leave the database byte-identical.

#### Scenario: Lookup has zero side effects
- **WHEN** `relationship_lookup` is called twice for the same entity with no intervening writes
- **THEN** both responses MUST be identical
- **AND** no row in any `relationship.*` or memory-module table MUST have changed

### Requirement: In-session-only cost gate

`relationship_lookup` SHALL be callable only from already-running butler sessions. **No cron entry, scheduled task, or spawn trigger MAY exist whose primary purpose is invoking `relationship_lookup`** (brief Phase D amendment 1 — the conditional-red cost finding). The guardrail is mechanical: a test MUST scan scheduled-task seed definitions and roster cron/scheduled-prompt files for the literal string `relationship_lookup` and MUST fail on any occurrence; the allowlist starts empty, and adding an entry requires a new LLM-cost review (Phase D re-entry), not a code change.

#### Scenario: Guardrail catches a lookup-feeding schedule
- **WHEN** a roster change adds a scheduled-task prompt containing the literal string `relationship_lookup`
- **THEN** the guardrail scan MUST fail (empty allowlist)
- **AND** the change MUST be rejected or escalated to cost review

### Requirement: Docstring budget

The tool's MCP docstring MUST be ≤ 300 tokens, counted as whitespace-delimited tokens (deterministic, dependency-free measure; it enters every mounting butler's tool inventory on every session). The docstring MUST state the read-only and in-session-only constraints so callers do not design schedules around it.

#### Scenario: Docstring stays within budget
- **WHEN** the tool registry loads `relationship_lookup`
- **THEN** a test MUST assert the whitespace-delimited token count of the docstring is ≤ 300

### Requirement: Deterministic not-found behavior

When neither `entity_id` nor `entity_ref` resolves, the tool MUST return a structured miss — `{entity: null, resolution: {ambiguous: false, candidates: []}}` — not an exception, so caller sessions can branch without retry loops. Supplying both or neither argument MUST raise a validation error naming the constraint.

#### Scenario: Miss is a value, not an error
- **WHEN** `relationship_lookup(entity_ref="zzz-no-such-entity")` finds nothing
- **THEN** the tool MUST return `entity: null` with empty candidates
- **AND** no exception MUST propagate to the calling session
