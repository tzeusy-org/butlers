## ADDED Requirements

### Requirement: Organizer Verdict

The calendar SHALL compute one verdict per organizer with a pure function `organizer_verdict`
whose result is one of `owner`, `known_entity`, `known_domain`, `first_contact`, `blocked` or
`unknown`, together with lure badges from the closed set `hidden_guest_list_large`,
`organizer_domain_mismatch_conference`, `external_link_present`, `from_gmail` and `mass_invite`.
Inputs SHALL be server-verified provider fields (organizer email, the owner's own email, attendee
count, guests-hidden flag, event type, conference domain) and two owner-owned lookups passed in as
values: the identity class of the organizer address and whether connector or global ingestion
policy blocks it. The function SHALL NOT accept or read an event title, description, location or
any other free text. Precedence SHALL be `owner`, then `blocked`, then `known_entity`, then
`known_domain`, then `first_contact`. When either lookup could not be read the verdict SHALL be
`unknown`, never a `known_*` value and never `owner` unless the organizer address equals the
owner's own address.

ID: REQ-module-calendar-039
Source: bu-5ii4gj.6 (run 26 move 6), slice S1
Scope: v1-mandatory

#### Scenario: Blocked outranks known

- **WHEN** the organizer address resolves to a known entity and a connector rule blocks that sender
- **THEN** the verdict is `blocked`

#### Scenario: An unreadable lookup never admits

- **WHEN** the identity lookup raises
- **THEN** the verdict is `unknown`, not `first_contact` and not `known_entity`

#### Scenario: Free text cannot change the verdict

- **WHEN** an event title or description claims the organizer is the owner or a known contact
- **THEN** the verdict is identical to the same event without that text, and the function signature has no text parameter

### Requirement: Verdict Stamp and Re-Stamp Sweep

The calendar projection SHALL stamp `organizer_verdict`, `lure_badges` and `verdict_version` into
every provider event's metadata when it writes the event. A bounded, idempotent sweep SHALL stamp
existing events that have no stamp or an older `verdict_version`, and SHALL re-stamp events when a
verdict input changes; re-stamping the same (event id, `verdict_version`) with unchanged inputs
SHALL write nothing. The cross-schema reads SHALL go through SECURITY DEFINER functions that
return an error on a read failure rather than an empty result, so that a failure stamps `unknown`.

ID: REQ-module-calendar-040
Source: bu-5ii4gj.6 (run 26 move 6), slice S1
Scope: v1-mandatory

#### Scenario: Existing rows are stamped

- **WHEN** an event projected before this change comes from an organizer matched by a connector block rule and the sweep runs
- **THEN** its metadata carries `organizer_verdict = blocked` and the current `verdict_version`

#### Scenario: The sweep is idempotent and bounded

- **WHEN** the sweep runs twice with unchanged inputs and more unstamped events than its batch size
- **THEN** each run stamps at most one batch, and the second run over stamped rows writes nothing

### Requirement: Invitation Quarantine

An invitation whose response status is `needsAction` and whose organizer verdict is
`first_contact`, `blocked` or `unknown`, or which carries no stamp, SHALL be quarantined: it SHALL
hold no time, SHALL NOT count toward day load or conflicts, SHALL NOT trigger prep, the meeting
card or the debrief, and its title and description SHALL NOT reach a prompt. An event the owner
has accepted, declined or marked tentative SHALL be unaffected regardless of verdict. A
`known_entity`, `known_domain` or `owner` organizer SHALL NOT be quarantined. Readers SHALL
exclude quarantined invitations before reading their text.

ID: REQ-module-calendar-041
Source: bu-5ii4gj.6 (run 26 move 6), slice S2
Scope: v1-mandatory

#### Scenario: A first-contact invite holds no time

- **WHEN** a needsAction invitation from a `first_contact` organizer overlaps an accepted event
- **THEN** no conflict is reported and the invitation appears in the quarantine section of the invitation list

#### Scenario: The owner's accept wins

- **WHEN** the owner has accepted an invitation whose organizer verdict is `blocked`
- **THEN** the event counts toward load and conflicts as an accepted event

#### Scenario: The quarantine section shows time and verdict only

- **WHEN** the invitation list is read and a quarantined invitation exists
- **THEN** its entry carries the entry id, start, end, timezone, verdict and lure badges, and no title, description, location, organizer or attendee

#### Scenario: An unreadable verdict degrades the envelope

- **WHEN** a needsAction invitation carries no verdict stamp
- **THEN** it is listed as quarantined with verdict `unknown` and the response reports `issues_available = false`

#### Scenario: No reader takes quarantined text into a prompt

- **WHEN** the meeting card, prep, debrief, household context, context producer or Chronicler calendar adapter reads a quarantined invitation, including one on the owner's own calendar
- **THEN** it contributes no time, no context and no text, and a contract test fails when a SQL statement reading `calendar_events` carries neither the quarantine predicate nor an inline `-- quarantine-exempt: <reason>` comment

#### Scenario: Provider-direct event reads return a stub for a quarantined invitation

- **WHEN** `calendar_list_events` or `calendar_get_event` returns an event whose owner attendee is unanswered and whose stored organizer verdict is `first_contact`, `blocked`, `unknown` or missing, or the verdict lookup fails
- **THEN** the event is returned as its time, `organizer_verdict` and the title `[quarantined invitation]`, with no description, attendees or location

#### Scenario: Provider free/busy is not a reader of stored invitations

- **WHEN** free slots are computed from the provider's free/busy answer
- **THEN** that answer is outside this requirement; the quarantine applies to the stored event rows only
