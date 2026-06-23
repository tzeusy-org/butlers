## ADDED Requirements

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
