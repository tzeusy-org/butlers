## ADDED Requirements

### Requirement: Declared Status Events Add No Day Load

A typed Google status event (`event_type` `outOfOffice` or `workingLocation`,
core_341) SHALL remain a radar candidate under the existing provenance and
RSVP/transparency filters. When opaque, it SHALL still take part in overlap and
back-to-back detection. It SHALL add zero hours to the overloaded-day sum. The
shared detector input carries `counts_toward_day_load`, which defaults to true
and is false only for these two types. Only the overloaded-day calculation
reads it. Ordinary, focus and unknown future event types SHALL count as before.
Thresholds, workspace visibility and the API response shape SHALL NOT change,
and no type SHALL be inferred from a title (bu-s11n0s.4 AC D, design E7).

ID: REQ-calendar-conflict-overcommitment-radar-001
Source: bu-s11n0s.4 AC D (declared status events), design E7; core_341 event_type
Scope: v1-mandatory

#### Scenario: Opaque out-of-office still conflicts but adds no day load

- **WHEN** an opaque out-of-office block overlaps a meeting and ends just before
  another meeting
- **THEN** the radar reports the overlap and the back-to-back chain
- **AND** the block's hours do not contribute to an overloaded-day issue

#### Scenario: Ordinary and unknown types still count toward day load

- **WHEN** a default or unknown-type event pushes the day's meeting time over
  the budget
- **THEN** the radar reports an overloaded-day issue

#### Scenario: Transparent working location is ignored

- **WHEN** a working-location event is marked free (`transparency="transparent"`)
- **THEN** it produces no radar issue
