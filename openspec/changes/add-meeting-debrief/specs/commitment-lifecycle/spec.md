## ADDED Requirements

### Requirement: Meeting Debrief Commitment Producer

The Relationship Butler SHALL accept commitments that the owner states in answer to a meeting
debrief. A debrief commitment SHALL be created through `create_commitment` only from an explicit
owner answer; its `evidence_opened` SHALL carry `source: "meeting_debrief"`, the `event_id` and
`occurrence_start` of the meeting, and the debrief id. Its counterparty SHALL be an attendee of
that meeting or `null`; an attendee without a resolved entity SHALL yield a `null` counterparty,
never a dropped commitment.

ID: REQ-commitment-lifecycle-009
Source: heart-and-soul/vision.md (mental labor absorption; nothing vanishes silently)
Scope: v1-mandatory

#### Scenario: Debrief answer opens a commitment linked to the meeting

- **WHEN** the owner answers a debrief with "send Sam the deck by Friday"
- **THEN** a commitment-class owner condition exists whose `evidence_opened.event_id` equals the
  meeting's event id and whose `counterparty_entity_id` is the attendee's entity

#### Scenario: Counterparty outside the meeting is refused

- **WHEN** an answer names a counterparty who is not an attendee of the debriefed meeting
- **THEN** no commitment is created and the debrief stays unanswered

#### Scenario: Unresolved attendee yields a null counterparty

- **WHEN** the only other attendee has no resolved entity and the owner records a commitment
- **THEN** the commitment is created with a `null` counterparty

### Requirement: Commitment Sphere

A commitment's metadata MAY carry `sphere` with value `work` or `personal`, declared by the owner.
Any other value SHALL be rejected before the database is touched. Absence of `sphere` SHALL mean
undeclared and SHALL NOT be inferred.

ID: REQ-commitment-lifecycle-010
Source: heart-and-soul/vision.md (mental labor absorption)
Scope: v1-mandatory

#### Scenario: Declared sphere is stored

- **WHEN** a commitment is created with `sphere="work"`
- **THEN** its metadata contains `sphere: "work"`

#### Scenario: Unknown sphere is rejected

- **WHEN** `create_commitment` is called with `sphere="hobby"`
- **THEN** it raises a validation error without touching the database
