## ADDED Requirements

### Requirement: Explicit Provider Invitation Status Provenance

Calendar provider projection SHALL distinguish a provider-supplied valid RSVP
from the AttendeeInfo default, without changing existing tool defaults, declined
or transparency radar behavior, recurrence expansion, or provider write authority.

ID: REQ-module-calendar-028
Source: bu-q7vx1q.17; explicit-status preserving adjudication; Non-Negotiable Rules 1 and 7; RFC 0006
Scope: v1-mandatory

#### Scenario: Explicit status survives the provider projection
- **WHEN** a provider attendee carries a valid explicit responseStatus
- **THEN** projection retains the exact normalized response_status and a literal true response_status_explicit marker
- **AND** self and organizer remain actual provider-copy flags
- **AND** existing projected attendees stay exactly equal to the unchanged tool payloads; a separate ordered attendee_status_provenance companion binds each marker to its exact email and normalized status

#### Scenario: Default and malformed statuses remain unknown
- **WHEN** a provider attendee omits responseStatus or supplies a malformed value
- **THEN** AttendeeInfo and tool payload defaults remain compatible
- **AND** projected response_status_explicit is false, so no unanswered invitation can be inferred from the default

#### Scenario: Legacy projection is never restamped by a read
- **WHEN** a legacy projected needsAction attendee lacks explicit-status provenance
- **THEN** the invitation read omits that ambiguous candidate and reports admission unavailable
- **AND** no provider request, backfill, RSVP write or projection rewrite is performed
