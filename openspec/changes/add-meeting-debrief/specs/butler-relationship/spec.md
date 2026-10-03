## ADDED Requirements

### Requirement: Meeting debrief job

The Relationship butler SHALL run a deterministic, zero-LLM `meeting_debrief` job that records one
`relationship.meeting_debriefs` row per `(event_id, occurrence_start)` for each ended, non-cancelled,
non-transparent, non-all-day calendar occurrence that the owner did not decline and that has at
least one other attendee, and proposes one batched insight listing the unprompted debriefs.
Recurring series SHALL be debriefed per occurrence. The job SHALL list and count only people whose
posture is `active`, and SHALL NOT prompt about a meeting whose other attendees are all non-active.

#### Scenario: Excluded meetings

- **WHEN** the job runs over a declined meeting, a solo block, an owner-only meeting and a
  transparent block
- **THEN** none of them gets a debrief row

#### Scenario: Idempotent per occurrence

- **WHEN** the job runs twice over the same ended meeting
- **THEN** exactly one debrief row exists and the meeting is prompted at most once

#### Scenario: Non-active attendee is never asked about

- **WHEN** the only other attendee of an ended meeting has posture `memorial`
- **THEN** no debrief prompt mentions the meeting

#### Scenario: Prompt failure retries

- **WHEN** the insight broker returns an error
- **THEN** no debrief is marked prompted and the next run proposes them again

### Requirement: Meeting debrief answer tools

The Relationship butler SHALL register `meeting_debrief_pending` and `meeting_debrief_answer` in its
`tracking` tool group. `meeting_debrief_answer` SHALL record `none_agreed` or `captured` exactly
once per debrief and SHALL create commitments only through `create_commitment`.

#### Scenario: None agreed is a recorded answer

- **WHEN** the owner answers a debrief with nothing agreed
- **THEN** the debrief state is `none_agreed` with `answered_at` set and no commitment is created

#### Scenario: Re-answering does not duplicate

- **WHEN** an answered debrief is answered again
- **THEN** the second call reports `already_answered` and creates nothing

### Requirement: Meeting debrief back-off

After three consecutive prompts without an answer the job SHALL propose a debrief batch at most
once every seven days, and the batch that crosses the threshold SHALL tell the owner the cadence has
dropped to weekly. An answer SHALL restore the daily cadence.

#### Scenario: Weekly cadence after disengagement

- **WHEN** three consecutive prompt batches are unanswered
- **THEN** a run within seven days of the last prompt proposes nothing

## MODIFIED Requirements

### Requirement: Relationship Butler Registered Tool Surface

The relationship butler SHALL expose its currently approved manifesto-owned
personal CRM tool set. Its eight configured relationship-module groups
(`contacts`, `contacts_extended`, `interactions`, `relationships`, `social`,
`notes`, `tracking`, and `management`) own 60 tools. The mandatory
`relationship_assert_fact` approval-dispatch handler registers unconditionally,
for 61 relationship-module handlers in total. The mixed `entity` group SHALL
remain disabled until the adopted six-read/two-write split is implemented; it
MUST NOT be activated while it still exposes both reads and writes.

#### Scenario: Exact current registered inventory

- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** all 60 tools owned by the eight configured groups SHALL be registered
- **AND** the inventory SHALL include `contact_create`, `contact_update`,
  `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`,
  `relationship_add`, `relationship_list`, `relationship_remove`, `date_add`,
  `date_list`, `upcoming_dates`, `note_create`, `note_list`, `note_search`,
  `interaction_log`, `interaction_list`, `fact_set`, `fact_list`, `feed_get`,
  `meeting_debrief_pending`, and `meeting_debrief_answer`
- **AND** `relationship_assert_fact` SHALL be the additional mandatory
  unconditional handler, making 61 relationship-module handlers total
- **AND** `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`,
  `relationship_lookup`, `entity_update`, and `relationship_record_coverage`
  SHALL all be absent
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered
- **AND** the separately configured memory module MAY expose
  `memory_entity_create` under that canonical prefixed name
