## ADDED Requirements

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

#### Scenario: Email butler writes per-event message context
- **WHEN** the messenger (or travel) `calendar_prep_contribution` job runs for an entity-linked event whose attendee has recent inbound email threads
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
