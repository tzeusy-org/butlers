# Calendar Overlay Aggregation

## Purpose

Cross-domain overlay aggregation for the calendar workspace. Specialist butlers
(finance, travel, relationship, health) contribute structured domain events into
a shared read-only SQL view that the calendar workspace projects without LLM cost
or on-demand cross-schema queries. The meeting-prep rail builds on the same
cached-view discipline for per-event attendee context.

## Requirements

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
- **BECAUSE** RFC-0020 rejected the per-open / LLM-synthesis design under RFC 0010 reuse criteria #2 (deterministic) and #3 (batch); any narrative summary is batch pre-rendered and deferred (`bu-jdrkbj`)

### Requirement: Overlay View Migration

An Alembic migration SHALL create the `calendar.v_overlay_contributions` view and grant SELECT on each contributing specialist schema's `state` table to the database role used by the calendar butler. The migration MUST be reversible: downgrade drops the view and revokes the grants. The migration SHALL reuse the `core_063` optional-schema guard contract (`to_regclass`-based `_state_table_exists`, best-effort `_ensure_role_exists`, and a NULL-returning stub UNION term for any absent specialist `state` table) so it is safe on fresh/core-only databases.

#### Scenario: Migration upgrade (Guardrail 5 — grants are migration-tracked)
- **WHEN** the Alembic migration is applied
- **THEN** the view `calendar.v_overlay_contributions` exists and is queryable from the calendar schema
- **AND** SELECT grants on the contributing specialist `state` tables are active for the calendar reader role
- **BECAUSE** cross-schema access is created via migration (tracked in version control), not ad-hoc SQL or runtime code (RFC 0010 Guardrail #5)

#### Scenario: Migration downgrade is reversible
- **WHEN** the Alembic migration is reverted
- **THEN** the view `calendar.v_overlay_contributions` is dropped
- **AND** the cross-schema SELECT grants are revoked

#### Scenario: Absent specialist state table is guarded
- **WHEN** a contributing specialist's `state` table does not exist at migration time (specialist butler not yet deployed)
- **THEN** the migration emits a NULL-returning stub UNION term (`SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE`) for that specialist and the SELECT grant for it silently no-ops
- **AND** the overall view is still created and queryable

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
- **BECAUSE** RFC-0020 adopted the no-LLM structured variant; the batched pre-rendered narrative layer is deferred to `bu-jdrkbj`

### Requirement: Per-Butler Overlay Contribution Job

Each contributing specialist butler — and exactly the set `finance`, `travel`, `relationship`, `health` — SHALL implement a `calendar_overlay_contribution` deterministic job that queries its own domain tables and writes per-date overlay envelopes. The job MUST be a pure deterministic Python/SQL function with zero LLM cost, and SHALL be registered in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` under the specialist's butler name (reusing the briefing contribution registry, not a parallel system). Lifestyle, Home, and Education are excluded because their domain data is not date-keyed calendar events.

#### Scenario: Finance overlay entries
- **WHEN** the Finance butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kind `bill_due` (bills due on that date) and `subscription_renewal` (subscriptions renewing on that date)
- **AND** a date with no bills and no renewals produces an envelope with `has_entries=false`

#### Scenario: Travel overlay entries
- **WHEN** the Travel butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `departure`, `arrival`, `check_in`, and `check_out` for dates in the lookahead window
- **AND** a date with no travel events produces an envelope with `has_entries=false`

#### Scenario: Relationship overlay entries
- **WHEN** the Relationship butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `birthday`, `important_date`, and `follow_up` for dates in the lookahead window
- **AND** a date with no relationship events produces an envelope with `has_entries=false`

#### Scenario: Health overlay entries
- **WHEN** the Health butler runs `calendar_overlay_contribution`
- **THEN** it writes entries of kinds `appointment` and `medication_reminder` for dates in the lookahead window
- **AND** a date with no health events produces an envelope with `has_entries=false`

#### Scenario: Contribution job is deterministic and zero-LLM
- **WHEN** any `calendar_overlay_contribution` job executes
- **THEN** it runs as a pure Python/SQL function on the daemon with no LLM session spawned
- **BECAUSE** the whole RFC 0010 / RFC-0020 justification is avoiding LLM sessions for deterministic cross-schema work

#### Scenario: Non-contributing butlers are excluded
- **WHEN** a butler NOT in the contributing set (`education`, `home`, `lifestyle`, `general`, `calendar`, or any staffer) starts
- **THEN** it has no `calendar_overlay_contribution` handler in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
- **AND** it has no `calendar_overlay_contribution` schedule entry

### Requirement: Contribution Job Scheduling

Each contributing specialist butler SHALL have a `calendar_overlay_contribution` entry in its `butler.toml` with `dispatch_mode="job"`, `job_name="calendar_overlay_contribution"`, and a fixed daily cron, registered through the same TOML-schedule sync path the briefing contribution jobs use.

#### Scenario: Schedule entry present
- **WHEN** a contributing specialist butler daemon starts and syncs TOML schedules
- **THEN** a `calendar_overlay_contribution` scheduled task exists with `dispatch_mode="job"` and the configured daily cron

#### Scenario: Job registered in daemon
- **WHEN** the scheduler dispatches the `calendar_overlay_contribution` job on a contributing specialist
- **THEN** the job handler is found in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` under that butler's name
- **AND** it executes deterministically with no LLM session

### Requirement: Meeting-Prep Contribution Schema and State Key Convention

Each contributing specialist butler SHALL write a structured per-event meeting-prep envelope into its own `state` store under the key `calendar/prep/<event_id>`. The envelope MUST be deterministic and contain no generated prose. It MUST carry a hardcoded `butler` source field, the `event_id`, the event title and start time, a `has_context` boolean, and an `attendees` array. Each attendee entry MUST carry `entity_id`, `name`, an optional `dunbar_tier` (the relationship letter-mark source), a `notes` list, `last_met` / `last_met_event` (from the most recent prior co-attended event), and a `message_context` list reserved for email/message-owning butlers.

#### Scenario: Relationship writes per-event prep envelopes
- **WHEN** the relationship `calendar_prep_contribution` job runs for an entity-linked event in its lookahead window
- **THEN** it writes one envelope under `calendar/prep/<event_id>` with `butler="relationship"`, the event's attendees resolved to `entity_id` + `name`, each attendee's durable relationship notes, their Dunbar-tier override (when set) and their last-met from the most recent prior co-attended event
- **AND** `has_context` is `true` when at least one attendee resolved, `false` otherwise (honest empty-state)
- **AND** no LLM session is spawned

#### Scenario: Stale per-event envelopes are pruned
- **WHEN** the prep contribution job runs and a previously-written `calendar/prep/<event_id>` key references an event no longer in the lookahead window
- **THEN** that stale key is deleted, while keys for events still in the window are upserted (idempotent re-runs)

### Requirement: Cross-Schema Prep View and Migration

A migration-tracked read-only SQL view `calendar.v_prep_contributions` SHALL UNION the `butler`, `key`, and `value` columns from each contributing specialist's `state` table, filtered to `key LIKE 'calendar/prep/%'`, with a hardcoded `butler` source literal per UNION term — mirroring `calendar.v_overlay_contributions` (core_140). The migration MUST grant SELECT on each contributing specialist's `state` table to the calendar reader role, emit a NULL-returning stub UNION term for any absent specialist schema, and reverse both the view and the grants on downgrade.

#### Scenario: Prep view created and queryable
- **WHEN** the `calendar.v_prep_contributions` migration is applied
- **THEN** the view exists and is queryable from the calendar schema, UNIONs the contributing specialists' `state` rows filtered to `key LIKE 'calendar/prep/%'` with a hardcoded `butler` literal per term, and returns zero rows before any prep job runs

#### Scenario: Prep view is read-only and absent-schema-safe
- **WHEN** an INSERT/UPDATE/DELETE is attempted on the view, OR a contributing specialist's `state` table is absent at migration time
- **THEN** the write fails (UNION view is not updatable) and the missing schema is represented by a NULL-returning stub term so the view still creates

#### Scenario: Migration is reversible
- **WHEN** the migration is reverted
- **THEN** the view is dropped AND the cross-schema SELECT grants are revoked

### Requirement: Relationship Prep Contribution Job Registration

The relationship butler SHALL register a `calendar_prep_contribution` deterministic (`dispatch_mode="job"`, zero-LLM) job in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` and schedule it via its `butler.toml`. No parallel scheduler or dispatch mechanism may be introduced.

#### Scenario: Prep job registered and scheduled deterministically
- **WHEN** the daemon loads the scheduled-job registry
- **THEN** `calendar_prep_contribution` is registered under `relationship` in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` and is scheduled from `roster/relationship/butler.toml` with `dispatch_mode="job"`
- **AND** the job handler takes only `(pool, job_args)` and spawns no LLM session
