## MODIFIED Requirements

### Requirement: Meeting-Prep Contribution Schema and State Key Convention
Each contributing specialist butler SHALL write a structured per-event meeting-prep envelope into its own `state` store under the key `calendar/prep/<event_id>`. The envelope MUST be deterministic and contain no generated prose. It MUST carry a hardcoded `butler` source field, the `event_id`, the event title and start time, a `has_context` boolean, and an `attendees` array. Each attendee entry MUST carry `entity_id`, `name`, an optional `dunbar_tier` (the relationship letter-mark source), a `notes` list, `last_met` / `last_met_event` (from the most recent prior co-attended event), and a `message_context` list reserved for email/message-owning butlers. Each attendee entry MUST also carry a `commitments` list containing active commitment-class `owner_conditions` rows where the attendee's `entity_id` matches `metadata->>'counterparty_entity_id'`. Each commitment entry MUST carry `kind`, `direction`, `summary` (the condition's `label`), `deadline` (from metadata, nullable), `escalation_level` as one of the established `L0`, `L1`, `L2`, or `L3` labels, and `fingerprint`. The list MUST be capped at a configurable maximum per attendee (default 10), ordered `L3` through `L0`, and MUST be empty -- not absent -- when no active commitments exist for the attendee.
- An explicit OOO or working-location event SHALL not be eligible for a meeting-prep envelope. Previously cached keys for such events SHALL be pruned by the established idempotent contribution refresh. The normal eligible meeting envelope, commitment ordering/caps, attribution and failure behavior SHALL remain intact.

ID: REQ-calendar-overlay-aggregation-005
Source: RFC 0026 §Out of Scope ("Moment Prep integration — consumes commitment query surface")
Scope: v1-mandatory

#### Scenario: Relationship writes per-event prep envelopes
- **WHEN** the relationship `calendar_prep_contribution` job runs for an eligible entity-linked event in its lookahead window
- **THEN** it writes one envelope under `calendar/prep/<event_id>` with `butler="relationship"`, the event's attendees resolved to `entity_id` + `name`, each attendee's durable relationship notes, their Dunbar-tier override (when set) and their last-met from the most recent prior co-attended event
- **AND** `has_context` is `true` when at least one attendee resolved, `false` otherwise (honest empty-state)
- **AND** no LLM session is spawned

#### Scenario: Stale per-event envelopes are pruned
- **WHEN** the prep contribution job runs and a previously-written `calendar/prep/<event_id>` key references an event no longer in the lookahead window
- **THEN** that stale key is deleted, while keys for events still in the window are upserted (idempotent re-runs)

#### Scenario: Prep envelope includes active commitments per attendee

- **WHEN** the relationship `calendar_prep_contribution` job runs for an
  entity-linked event whose eligible meeting attendee has active commitment-class
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


#### Scenario: Relationship prunes a status-only prep key
- **WHEN** the Relationship contribution job sees a typed OOO or working-location event with planted linked attendees and a prior prep key plus an eligible ordinary meeting
- **THEN** the status-only key is pruned and no new prep envelope is written for it
- **AND** the ordinary meeting still gets its established complete attendee/commitment envelope



### Requirement: Email/Message-Context Prep Contribution Job
The email/message-owning butlers (messenger, travel) SHALL each run a
deterministic (`dispatch_mode="job"`, zero-LLM) `calendar_prep_contribution` job
that precomputes the meeting-prep `message_context` panel into their own `state`
store under the key `calendar/prep/<event_id>`. For each entity-linked event in
the rolling lookahead window, the job MUST collect the recent inbound
`email`-channel threads each attendee wrote — read from the persisted
inbound-message store (`switchboard.message_inbox`) during the scheduled job, NOT
a direct cross-butler Gmail read at request time and NOT via an LLM session — and
key them by the resolved sender `entity_id` so the prep read merges them into the
relationship-sourced attendee. The envelope MUST carry a hardcoded `butler` source
field and, per attendee, an `entity_id`, `name`, and a `message_context` list. To
preserve the prep rail's honest empty-state, an envelope MUST be written only for
events where at least one attendee has recent message context; events with none
MUST be skipped and any previously-written `calendar/prep/<event_id>` key pruned.
The job MUST be registered in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
under `messenger` and `travel` and scheduled via each `butler.toml`; no parallel
scheduler may be introduced.
- Messenger and Travel SHALL use the same OOO/working-location meeting-prep exclusion as Relationship against their own calendar projections. Cached status-only keys SHALL be pruned without reading another butler schema or changing the established message-context read exception.

ID: REQ-calendar-overlay-aggregation-006
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: Email butler writes per-event message context
- **WHEN** the messenger (or travel) `calendar_prep_contribution` job runs for an eligible entity-linked event whose attendee has recent inbound email threads
- **THEN** it writes one envelope under `calendar/prep/<event_id>` with `butler="messenger"` (resp. `"travel"`), and that attendee's `message_context` list carries the recent threads (channel, thread id, subject, snippet, last-message time, message count) keyed by the attendee's `entity_id`
- **AND** no LLM session is spawned and no Gmail/IMAP read occurs at request time

#### Scenario: Events without message context are skipped
- **WHEN** the job runs and an entity-linked event has no attendee with recent message context
- **THEN** no envelope is written for that event, preserving the prep rail's honest empty-state, and any stale `calendar/prep/<event_id>` key from a prior run is pruned

#### Scenario: Fail-open when the message store is unreadable
- **WHEN** the job cannot read `switchboard.message_inbox` (table absent or no grant)
- **THEN** the job surfaces no message context for that run and completes without raising (logged at WARNING), rather than crashing the scheduled job

#### Scenario: Registered deterministically for both email butlers
- **WHEN** the daemon loads the scheduled-job registry
- **THEN** `calendar_prep_contribution` is registered under both `messenger` and `travel` in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`, each scheduled from its `butler.toml` with `dispatch_mode="job"`
- **AND** each job handler takes only `(pool, job_args)` and spawns no LLM session

#### Scenario: Message owners suppress status-only meeting prep
- **WHEN** Messenger or Travel has OOO/working-location and ordinary meeting rows with linked attendees and planted recent thread context
- **THEN** no status-only prep is written and an earlier status-only key is pruned
- **AND** the ordinary meeting positive retains the same bounded message context
