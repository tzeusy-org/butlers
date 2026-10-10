## ADDED Requirements

### Requirement: Explicit Unanswered Calendar Invitations

GET /api/calendar/workspace/invitations SHALL return only current projected
provider occurrences with a literal self=true attendee whose exact needsAction
status has explicit provider provenance. The endpoint SHALL be read-only and
preserve every existing workspace and RSVP/transparency radar scenario.

ID: REQ-dashboard-api-067
Source: bu-q7vx1q.17; explicit-status preserving adjudication; RFC 0006; dashboard-design-language
Scope: v1-mandatory

#### Scenario: Only explicit unanswered occurrences are admitted
- **WHEN** the projection includes unanswered, accepted, declined, tentative, no-self, missing/malformed/legacy status, self-organizer, solo and cancelled occurrences
- **THEN** only genuinely explicit unanswered occurrences with another party are returned with stable instance entry IDs
- **AND** ambiguous status candidates are omitted and admission is unavailable rather than fabricated empty
- **AND** valid response_status or raw responseStatus is supported only with literal true explicit-status provenance

#### Scenario: Deduplication precedes eligibility and pagination
- **WHEN** duplicate provider copies or a stale unanswered copy coexist with a newer accepted copy
- **THEN** the current workspace dedup strategy and keep-separate rules are applied before eligibility and keyset page slicing
- **AND** deterministic operational source ownership is retained without rewriting physical ledgers
- **AND** the start/end window is ordered and at most 90 days, limit is 1..200, and remaining eligible rows expose has_more and next_cursor
- **AND** malformed or wrong-window/timezone cursors are refused
- **AND** relevant current same-origin tombstones and moved copies participate before final window filtering, so newer cancelled or out-of-window copies cannot resurrect stale invitations

#### Scenario: Organizer and conflict evidence are honest
- **WHEN** an invitation is listed
- **THEN** its organizer is the projected event organizer, a labelled organizer-attendee fallback, or explicitly unknown
- **AND** a conflict door identifies only a real radar issue whose events include that canonical entry ID
- **AND** missing or degraded conflict evidence is unknown, never an invented conflict-free verdict

#### Scenario: Failed sources retain successful data without calm
- **WHEN** any events, operational-source or conflict read fails, or candidate status admission is unknown
- **THEN** HTTP 200 retains successful invitation rows and observed issue doors with issues_available=false and named content-blind sources_degraded
- **AND** only complete available empty reads return issues_available=true with an empty list

#### Scenario: Calendar strip preserves loading and partial evidence
- **WHEN** the Calendar page reads its visible window
- **THEN** the opener shows invitation rows and organizer labels with keyboard-operable entry and earned conflict doors
- **AND** loading and unavailable invitation sources suppress the Calendar calm verdict; retained rows survive refresh/error
- **AND** only available nonloading empty reads say no unanswered invitations, and bounded remaining pages remain reachable
- **AND** the strip adds no provider mutation or RSVP action; entry details retain existing server-derived permission boundaries
