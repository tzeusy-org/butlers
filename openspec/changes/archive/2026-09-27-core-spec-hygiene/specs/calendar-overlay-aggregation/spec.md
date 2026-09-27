## MODIFIED Requirements

### Requirement: Cross-Schema Overlay View

A SQL view `calendar.v_overlay_contributions` SHALL provide read-only access to overlay contribution state entries across the four contributing specialist schemas. The view SHALL union `butler`, `key`, and `value` columns from the `state` table of each contributing schema (`finance`, `travel`, `relationship`, `health`) filtered to keys matching `calendar/overlay/%`. Each UNION term SHALL include an explicit `butler` column as a string literal identifying the source schema (e.g. `SELECT 'finance' AS butler, key, value FROM finance.state WHERE key LIKE 'calendar/overlay/%'`), mirroring `general.v_briefing_contributions` (migration `core_063`). The view SHALL be empty (zero rows) when no specialist has written a contribution.

This view is a sanctioned exception to schema isolation (RFC 0006), reusing the RFC 0010 Cross-Butler Briefing Exception under RFC-0020's accepted criteria. The five guardrails are inherited verbatim and encoded as the scenarios below: read-only/DB-enforced, hardcoded source column, key-filtered, migration-tracked reversible grants, and zero-LLM in the read path.

#### Scenario: View returns contributions from available specialists
- **WHEN** multiple contributing specialist butlers have written overlay contributions for a given date
- **THEN** querying `calendar.v_overlay_contributions WHERE key = 'calendar/overlay/<date>'` returns all those contributions with their source schema identifiable via the `butler` column
- **AND** the `butler` column value is a string literal set per UNION term, not derived from the JSON payload

#### Scenario: View returns empty when no contributions exist
- **WHEN** no specialist butler has written any overlay contribution
- **THEN** querying `calendar.v_overlay_contributions` returns zero rows
- **AND** no error is raised (empty-when-none, not failure)

#### Scenario: Guardrail 1 — view is write-forbidden at the database level
- **WHEN** an INSERT, UPDATE, or DELETE is attempted on `calendar.v_overlay_contributions`
- **THEN** the operation fails because UNION views are not updatable in PostgreSQL
- **BECAUSE** the read-only constraint is enforced by the database engine, not application convention (RFC 0010 Guardrail #1)

#### Scenario: Guardrail 2 — source column is hardcoded, not from payload
- **WHEN** the workspace projection reads a row from the view
- **THEN** the `butler` source column value comes from the hardcoded UNION literal and the projection validates that `value->>'butler'` matches it
- **AND** if `value->>'butler'` does not match the hardcoded source column, the contribution is treated as malformed and skipped with a warning log
- **BECAUSE** the hardcoded literal is the tamper-resistant source attribution; a mismatch indicates a tampered or misconfigured payload (RFC 0010 Guardrail #2)

#### Scenario: Guardrail 3 — view is key-filtered to overlay keys only
- **WHEN** a contributing specialist's `state` table contains keys outside the `calendar/overlay/%` prefix (e.g. `briefing/daily/%` or arbitrary domain keys)
- **THEN** those rows are NOT visible through `calendar.v_overlay_contributions`
- **BECAUSE** each UNION term filters with `key LIKE 'calendar/overlay/%'`, bounding access to overlay keys only rather than the whole `state` table (RFC 0010 Guardrail #3)

#### Scenario: Guardrail 5 — zero LLM session in the read path
- **WHEN** the overlay view is queried and projected for rendering
- **THEN** the read is a pure deterministic SQL/Python projection with no LLM session and no cross-schema fan-out at request time
- **BECAUSE** RFC-0020 rejected the per-open / LLM-synthesis design under RFC 0010 reuse criteria #2 (deterministic) and #3 (batch); any narrative summary is batch pre-rendered and deferred

### Requirement: Overlay Contribution Schema and State Key Convention

Each contributing specialist butler MUST write its daily overlay contribution as a JSON envelope with fields `butler` (string, butler name), `date` (string, ISO date YYYY-MM-DD), `has_entries` (boolean), and `entries` (array of entry objects), under a state key matching `calendar/overlay/<YYYY-MM-DD>` where the date is the target calendar date in SGT (UTC+8). Each entry object SHALL have `kind` (string), `label` (string), and `priority` (one of `"high"`, `"medium"`, `"low"`), plus an optional kind-specific `meta` object. The v1 envelope MUST NOT contain a generated-prose `summary` field (the narrative layer is deferred).

#### Scenario: Envelope with entries written under the date key
- **WHEN** a contributing specialist has domain-relevant events for a target date
- **THEN** it writes an envelope with `has_entries=true` and a non-empty `entries` array to its state store under key `calendar/overlay/<date>`
- **AND** entries are ordered by priority descending (`"high"` first, then `"medium"`, then `"low"`)

#### Scenario: Envelope with no entries is still written (honest empty-state)
- **WHEN** a contributing specialist has no domain events for a target date in its lookahead window
- **THEN** it writes an envelope with `has_entries=false` and an empty `entries` array under `calendar/overlay/<date>`
- **BECAUSE** persisting the empty envelope lets the read layer distinguish "job ran, nothing found" from "job has not run"

#### Scenario: Key upserts stale entry
- **WHEN** the contribution job runs and an envelope for a given date already exists
- **THEN** the existing entry is overwritten via `state_set` (upsert semantics)

#### Scenario: Pruning removes old entries
- **WHEN** the contribution job completes its writes
- **THEN** it deletes all `calendar/overlay/*` state entries whose date suffix is older than the retention window
- **AND** when there are no entries to prune, the prune step completes as a no-op

#### Scenario: v1 envelope carries no generated prose
- **WHEN** a v1 overlay envelope is written
- **THEN** it contains no `summary` (generated-prose) field
- **BECAUSE** RFC-0020 adopted the no-LLM structured variant; the batched pre-rendered narrative layer is deferred

### Requirement: Meeting-Prep Contribution Schema and State Key Convention

Each contributing specialist butler SHALL write a structured per-event meeting-prep envelope into its own `state` store under the key `calendar/prep/<event_id>`. The envelope MUST be deterministic and contain no generated prose. It MUST carry a hardcoded `butler` source field, the `event_id`, the event title and start time, a `has_context` boolean, and an `attendees` array. Each attendee entry MUST carry `entity_id`, `name`, an optional `dunbar_tier` (the relationship letter-mark source), a `notes` list, `last_met` / `last_met_event` (from the most recent prior co-attended event), and a `message_context` list reserved for email/message-owning butlers. Each attendee entry MUST also carry a `commitments` list containing active commitment-class `owner_conditions` rows where the attendee's `entity_id` matches `metadata->>'counterparty_entity_id'`. Each commitment entry MUST carry `kind`, `direction`, `summary` (the condition's `label`), `deadline` (from metadata, nullable), `escalation_level` as one of the established `L0`, `L1`, `L2`, or `L3` labels, and `fingerprint`. The list MUST be capped at a configurable maximum per attendee (default 10), ordered `L3` through `L0`, and MUST be empty -- not absent -- when no active commitments exist for the attendee.

ID: REQ-calendar-overlay-aggregation-005
Source: RFC 0026 §Out of Scope ("Moment Prep integration — consumes commitment query surface")

#### Scenario: Relationship writes per-event prep envelopes
- **WHEN** the relationship `calendar_prep_contribution` job runs for an entity-linked event in its lookahead window
- **THEN** it writes one envelope under `calendar/prep/<event_id>` with `butler="relationship"`, the event's attendees resolved to `entity_id` + `name`, each attendee's durable relationship notes, their Dunbar-tier override (when set) and their last-met from the most recent prior co-attended event
- **AND** `has_context` is `true` when at least one attendee resolved, `false` otherwise (honest empty-state)
- **AND** no LLM session is spawned

#### Scenario: Stale per-event envelopes are pruned
- **WHEN** the prep contribution job runs and a previously-written `calendar/prep/<event_id>` key references an event no longer in the lookahead window
- **THEN** that stale key is deleted, while keys for events still in the window are upserted (idempotent re-runs)

#### Scenario: Prep envelope includes active commitments per attendee

- **WHEN** the relationship `calendar_prep_contribution` job runs for an
  entity-linked event whose attendee has active commitment-class
  `owner_conditions` rows
- **THEN** the prep envelope's attendee entry carries a `commitments` list with
  each commitment's `kind`, `direction`, `summary`, `deadline`,
  `escalation_level`, and `fingerprint`
- **AND** commitments are ordered `L3`, `L2`, `L1`, then `L0` (highest urgency
  first), capped at `MAX_COMMITMENTS_PER_ATTENDEE`
- **AND** no LLM session is spawned

#### Scenario: Attendee with no commitments gets an empty list

- **WHEN** the prep job runs for an attendee who has no active commitment-class
  `owner_conditions` rows
- **THEN** the attendee's `commitments` field is an empty list `[]`, not absent
  from the envelope
- **BECAUSE** downstream consumers distinguish "no commitments" from "commitments
  not yet populated" by field presence

#### Scenario: Commitment query failure degrades gracefully

- **WHEN** the query against `public.owner_conditions` fails during the prep job
- **THEN** the prep envelope is still written with an empty `commitments` list
  per attendee and the failure is logged at WARNING level
- **AND** existing prep context (notes, Dunbar tier, last-met, message context)
  is unaffected
- **BECAUSE** the prep rail's honest empty-state contract requires fail-open
  behavior per RFC-0020
