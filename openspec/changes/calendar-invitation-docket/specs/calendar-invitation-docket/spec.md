## ADDED Requirements

### Requirement: Owner-Written RSVP Policies

ID: REQ-calendar-invitation-docket-001
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The system SHALL keep the owner's invitation judgment in `public.calendar_rsvp_policies`, one row per
policy, each with an ordered position, a list of closed typed predicates, a verdict (`accepted`,
`declined` or `tentative`), a `send_updates` choice, a monotonically increasing `version` and a
nullable `revoked_at`. The predicate vocabulary SHALL be closed: organizer verdict, organizer
entity role, overlaps a focus or out-of-office block, conflicts with an accepted opaque event,
outside declared working hours, recurring, attendee count and lead time. An event title or
description SHALL NOT be a predicate input, and a predicate outside the vocabulary SHALL be refused
on write. A policy SHALL be created, edited, reordered and revoked only by the owner through a
dashboard route; no MCP tool SHALL write a policy and the agent role SHALL hold no write grant on
the table. An edit SHALL increment the policy's `version`; a revoked policy SHALL match nothing.

#### Scenario: The agent role cannot write a policy
- **WHEN** a session running as a butler role attempts to insert, update or delete a policy row
- **THEN** the database refuses it, and no registered MCP tool exposes a policy write

#### Scenario: A free-text predicate is refused
- **WHEN** the owner submits a policy with a predicate on event title or description
- **THEN** the route refuses it and stores nothing

### Requirement: Deterministic Policy Evaluation

ID: REQ-calendar-invitation-docket-002
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

For a pending invitation the system SHALL select the first non-revoked policy, in position order,
whose predicates all hold, using only verified facts already on the event projection and the radar
(organizer verdict and entity role, focus and out-of-office overlap, accepted-event overlap, declared
hours, recurrence, attendee count, lead time). Evaluation SHALL be a pure function with no model and
no provider call. A quarantined invitation (unanswered, untrusted organizer) SHALL NOT be evaluated,
a series-root answer SHALL NOT be prepared, and an event with no matching policy SHALL be left for
the owner as it is today. The result SHALL name the policy id, its version and each matched fact.

#### Scenario: First matching policy wins
- **WHEN** two policies match an invitation
- **THEN** the lower position is chosen and the other is not cited

### Requirement: Prepared Answers Are Parked, Never Sent

ID: REQ-calendar-invitation-docket-003
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

A deterministic `calendar_rsvp_docket` job SHALL run in the butler that holds the calendar module
and its response fence, and SHALL park one per-event approvals card for each matched invitation
through the existing prepare-and-park path of the response coordinator. The card SHALL carry the
exact `calendar_respond` call (event, verdict, `send_updates`), a server-built dossier citing the
policy id, version and matched facts, and a request id derived from the policy version and the event
etag, so a rerun parks nothing new and an edited policy or a changed event parks a fresh card. The job
SHALL NOT call `calendar_respond` itself. No standing rule, auto-approval or promotion SHALL ever
apply `calendar_respond` on a policy's authority: a policy only prepares an answer, and a
person-visible answer is sent only after the owner approves that exact call.

#### Scenario: A rerun parks nothing new
- **WHEN** the job runs twice with no policy or event change
- **THEN** one card exists per invitation

#### Scenario: A policy edit supersedes its card
- **WHEN** a policy's version changes while its card is pending
- **THEN** the old card is no longer approvable and a new card carries the new version

### Requirement: The Docket Batch Submit

ID: REQ-calendar-invitation-docket-004
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The dashboard SHALL show every parked docket answer with its exact call (event, verdict,
`send_updates`), the policy id and version and the matched facts. A single owner submit SHALL
approve each selected card individually through the existing approve path, so each card keeps its
own audit trail and no bypass is added. A card whose event etag or policy version changed since it
was displayed SHALL be refused and named in the result, and the rest SHALL still be applied. The
docket SHALL NOT be a goal-runtime decision set.

#### Scenario: A changed card is refused and named
- **WHEN** the owner submits a batch and one event changed after display
- **THEN** that card is refused with its event named, and the others are approved

### Requirement: Per-Policy Agreement Ledger

ID: REQ-calendar-invitation-docket-005
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The system SHALL record, per policy and version, how many prepared answers the owner approved as
prepared, edited, or rejected, and SHALL show the counts beside the policy. The ledger SHALL be
informational: nothing SHALL read it to widen autonomy, auto-approve, promote or create a policy.

#### Scenario: The ledger never changes behaviour
- **WHEN** a policy reaches a perfect agreement record
- **THEN** its cards still park for approval and no rule is created
