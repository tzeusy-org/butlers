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
dashboard route, and only through SECURITY DEFINER functions owned by a NOLOGIN role whose EXECUTE is
granted to the dashboard's role alone (the `core_350` pattern). The dashboard SHALL read policies and ledger counts through SECURITY DEFINER read functions with the same EXECUTE grant, so the catalog test and the route agree. No `butler_*_rw` role SHALL hold
INSERT, UPDATE or DELETE on the table, and SELECT SHALL be granted only to the one butler role that
runs the docket job. No MCP tool and no prompt or session context SHALL present a policy to a model.
An edit SHALL name the `version` it was made from and SHALL be refused, changing nothing, when
the policy has moved on since; an accepted edit SHALL increment the policy's `version`; a revoked policy SHALL match nothing. A policy
preview against the owner's current invitations is later work, not part of this change.

#### Scenario: The agent role cannot write a policy
- **WHEN** a session running as a butler role attempts to insert, update or delete a policy row
- **THEN** the database refuses it, the grants catalog shows no write privilege for any
  `butler_*_rw` role, and no registered MCP tool exposes a policy write

#### Scenario: An edit from a stale version is refused
- **WHEN** the owner submits an edit naming a `version` that is no longer the policy's current one
- **THEN** the route answers 409, the policy is unchanged and its version does not rise

#### Scenario: A free-text predicate is refused
- **WHEN** the owner submits a policy with a predicate on event title or description
- **THEN** the route refuses it and stores nothing

#### Scenario: No model context carries a policy
- **WHEN** the registered MCP tools and prompt builders are inspected by a contract test
- **THEN** none reads `calendar_rsvp_policies`

### Requirement: Deterministic Policy Evaluation

ID: REQ-calendar-invitation-docket-002
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

For a pending invitation the system SHALL evaluate every non-revoked policy, in position order,
using only verified facts already on the event projection and the radar (organizer verdict and
entity role, focus and out-of-office overlap, accepted-event overlap, declared hours, recurrence,
attendee count, lead time). Evaluation SHALL be a pure function with no model and no provider call.
When one policy matches, or several match with the same verdict and `send_updates`, the first by
position SHALL be chosen. When matching policies disagree on verdict or `send_updates`, nothing SHALL
be prepared and the docket SHALL show "policies disagree" naming the policies. A quarantined
invitation (unanswered, untrusted organizer) SHALL NOT be evaluated, a series-root answer SHALL NOT
be prepared, and an event with no matching policy SHALL be left for the owner as it is today. The
result SHALL name the policy id, its version and each matched fact.

#### Scenario: Agreeing policies use the first by position
- **WHEN** two policies match an invitation with the same verdict and `send_updates`
- **THEN** the lower position is chosen and cited

#### Scenario: Disagreeing policies prepare nothing
- **WHEN** two policies match and one declines while the other accepts
- **THEN** no card is parked and the docket names both policies as disagreeing

### Requirement: Prepared Answers Are Parked, Never Sent

ID: REQ-calendar-invitation-docket-003
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

A deterministic `calendar_rsvp_docket` job SHALL park one per-event approvals card for each matched
invitation through the existing prepare-and-park path of the response coordinator. The card SHALL
carry the exact `calendar_respond` call (event, verdict, `send_updates`), a server-built dossier
citing the policy id, version and matched facts, and a request id that is a digest of the event id,
occurrence, etag, policy id, policy version, verdict and `send_updates`, so a rerun parks nothing new
and any change to one of those parks a fresh card. The job SHALL NOT call `calendar_respond` itself.
No standing rule, auto-approval or promotion SHALL ever apply `calendar_respond` on a policy's
authority: a policy only prepares an answer, and a person-visible answer is sent only after the owner
approves that exact call.

#### Scenario: A rerun parks nothing new
- **WHEN** the job runs twice with no policy or event change
- **THEN** one card exists per invitation

#### Scenario: Any input to the digest changes the request id
- **WHEN** the etag, the policy version, the verdict or `send_updates` differs
- **THEN** the request id differs and a new card is parked

### Requirement: Superseded Cards Are Rejected By The System

ID: REQ-calendar-invitation-docket-004
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

In the same run that parks a replacement card, or finds no match, the job SHALL re-evaluate every
open docket card and reject each one that is no longer current through the existing system reject,
with a closed reason and its receipt kept: `stale_event` (the event changed), `policy_changed` (the
policy's version changed), `policy_revoked`, or `policy_no_longer_matches`. A system rejection SHALL
never be sent as an answer and SHALL NOT count toward the agreement ledger, with one exception: when a card is rejected `stale_event` because the owner's own response status changed, the job SHALL record `answered_same` (counted as agreement) when the new status equals the card's verdict, and `answered_elsewhere` otherwise.

#### Scenario: A changed event rejects its card
- **WHEN** the event's etag changes while its card is open
- **THEN** the card is rejected `stale_event` and, if a policy still matches, a fresh card is parked

#### Scenario: The owner answers the invitation themself
- **WHEN** the owner's response status changes to the card's verdict while its card is open
- **THEN** the card is rejected `stale_event` and the ledger records `answered_same`; a different status records `answered_elsewhere`

#### Scenario: A policy edit, revocation or lost match rejects its card
- **WHEN** a policy is edited, revoked, or the invitation stops matching it
- **THEN** the card is rejected `policy_changed`, `policy_revoked` or `policy_no_longer_matches`
  respectively

### Requirement: The Docket Batch Submit

ID: REQ-calendar-invitation-docket-005
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The dashboard SHALL show every parked docket answer with its exact call (event, verdict,
`send_updates`), the policy id and version and the matched facts. A single owner submit SHALL
re-run the pure evaluator for each selected card and approve it individually through the existing
approve path, so each card keeps its own audit trail and no bypass is added. A card whose event etag
or policy version changed since it was displayed, or that no longer matches its policy, SHALL be
refused and named in the result, and the rest SHALL still be applied. The docket SHALL NOT be a
goal-runtime decision set. A card the owner does not tick SHALL stay pending for the owner, untouched.

#### Scenario: A changed card is refused and named
- **WHEN** the owner submits a batch and one event changed after display
- **THEN** that card is refused with its event named, and the others are approved

#### Scenario: A card that no longer matches is refused
- **WHEN** a policy is edited between display and submit so a card no longer matches
- **THEN** submit refuses and names that card

### Requirement: Per-Policy Agreement Ledger

ID: REQ-calendar-invitation-docket-006
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The system SHALL record, in `public.calendar_rsvp_ledger`, per policy and version, how the owner dealt with each prepared answer:
`approved_as_prepared`, `owner_rejected`, `answered_same` (the owner answered outside the card with
the card's own verdict; counted as agreement) or `answered_elsewhere` (the owner answered
differently). There SHALL be no "edited" outcome. System rejections SHALL be
excluded. The counts SHALL be shown beside the policy. The ledger SHALL be informational: nothing
SHALL read it to widen autonomy, auto-approve, promote or create a policy, and a contract test SHALL
pin that only the dashboard read model and the ledger writer reference the ledger table.

#### Scenario: The ledger never changes behaviour
- **WHEN** a policy reaches a perfect agreement record
- **THEN** its cards still park for approval and no rule is created

#### Scenario: A system rejection is not counted
- **WHEN** a card is rejected `stale_event` or `policy_changed` with no change to the owner's own answer
- **THEN** no ledger outcome is recorded for it

### Requirement: An Outage Is Never A Clean Docket

ID: REQ-calendar-invitation-docket-007
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

When the provider or the response snapshot is unavailable, the job SHALL prepare nothing for the
affected invitation and SHALL record a counted `unprepared` row with its reason; the docket SHALL say
how many invitations were left unprepared and SHALL NOT present an empty or partial docket as
complete.

#### Scenario: A provider outage is shown
- **WHEN** the snapshot for an invitation cannot be read
- **THEN** no card is parked, one `unprepared` row is counted, and the docket says so

### Requirement: The Docket Runs In Exactly One Butler

ID: REQ-calendar-invitation-docket-008
Source: bu-5ii4gj.9 (invitation docket)
Scope: v1-mandatory

The `calendar_rsvp_docket` job is calendar-module logic, not Messenger or Relationship domain logic.
Exactly one butler SHALL run it: the one whose calendar module owns the owner's primary calendar and
its response fence. The job SHALL refuse to run in any other butler.

#### Scenario: A second butler refuses
- **WHEN** the job is invoked in a butler that does not own the primary calendar's response fence
- **THEN** it refuses and parks nothing
