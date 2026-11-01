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

Exactly once SHALL include concurrent distinct answers and a competing empty answer. Answer
admission, all commitment effects and eligible projections, and the final answered state SHALL
commit in one owning transaction after the locked debrief state is rechecked. The losing call
SHALL report `already_answered` without changing commitments, evidence, answered state, timestamp
or session. Full batch validation SHALL precede its first effect. A propagated failure or
cancellation before commit SHALL roll back the answer's effects and leave its prior state intact;
the tool SHALL acknowledge `captured` or `none_agreed` only after commit. A response lost during
commit acknowledgment SHALL NOT license a second answer: retry SHALL observe the durable state
and either return `already_answered` for a committed answer or accept a new whole answer only
after rollback. A stale job snapshot SHALL NOT move an answered debrief back to answerable state.

#### Scenario: None agreed is a recorded answer

- **WHEN** the owner answers a debrief with nothing agreed
- **THEN** the debrief state is `none_agreed` with `answered_at` set and no commitment is created

#### Scenario: Re-answering does not duplicate

- **WHEN** an answered debrief is answered again
- **THEN** the second call reports `already_answered` and creates nothing


#### Scenario: Concurrent distinct answers accept one whole set

- **WHEN** two actual calls concurrently answer the same unanswered debrief with different valid commitment sets
- **THEN** exactly one call SHALL report `captured` and the other SHALL report `already_answered`
- **AND** only the winning set's commitments, meeting evidence and eligible graph effects SHALL commit

#### Scenario: None and captured compete for the same answer

- **WHEN** an empty answer and a non-empty answer concurrently target the same unanswered debrief
- **THEN** exactly one complete `none_agreed` or `captured` result SHALL commit
- **AND** the loser and any post-commit retry SHALL change no commitment, evidence, answered timestamp or session

#### Scenario: Failure after a real commitment write rolls back the whole answer

- **WHEN** a propagated later commitment, eligible graph, final-state error or cancellation occurs after an answer has written an actual commitment but before commit
- **THEN** the outer transaction SHALL roll back every effect executed by that answer and retain the debrief's prior unanswered state
- **AND** separate-connection readback SHALL find no surviving new commitment or graph rows and no partial updates to existing evidence or conditional premise effects

#### Scenario: An invalid later item refuses the entire answer

- **WHEN** an answer includes a valid item followed by an item rejected by common commitment validation
- **THEN** the tool SHALL return `invalid` after any transaction has rolled back and SHALL create no item from that answer
- **AND** current attendee/null and declared-sphere validation SHALL remain in force

#### Scenario: Counterparty no longer active refuses the answer

- **WHEN** an answer names a counterparty whose posture is no longer `active` (`memorial`, `quiet` or `no_contact`) at answer time
- **THEN** the tool SHALL return `invalid` with a fixed reason that does not state the posture, SHALL create no commitment from that answer, and SHALL leave the debrief answerable
- **AND** posture SHALL be read on the answer transaction's connection under the debrief row lock, through the same active-posture predicate the prompt path uses
- **AND** a null counterparty and an `active` counterparty SHALL behave as before, and a later answer without that item MAY be captured

#### Scenario: Lost acknowledgment does not permit a second answer

- **WHEN** the client loses the result while a complete answer is being committed and retries
- **THEN** the tool SHALL lock and inspect durable debrief state rather than infer permission from the missing response
- **AND** a committed result SHALL yield `already_answered`; a rolled-back result MAY accept one new complete answer
- **AND** no reply SHALL acknowledge `captured` or `none_agreed` before its commit is confirmed

#### Scenario: A stale prompt snapshot cannot reopen answered state

- **WHEN** the prompt job selected a pending unaskable debrief before an answer committed and later tries to expire that stale selection
- **THEN** `captured` or `none_agreed`, answered timestamp and session SHALL remain intact and the next answer SHALL report `already_answered`
- **AND** a genuinely still-pending unaskable debrief SHALL retain ordinary expiry behavior

### Requirement: Meeting debrief back-off

After three consecutive prompts without an answer the job SHALL propose a debrief batch at most
once every seven days, and the batch that crosses the threshold SHALL tell the owner the cadence has
dropped to weekly. An answer SHALL restore the daily cadence.

#### Scenario: Weekly cadence after disengagement

- **WHEN** three consecutive prompt batches are unanswered
- **THEN** a run within seven days of the last prompt proposes nothing
