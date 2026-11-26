## ADDED Requirements

### Requirement: Workspace entries keep the event's authoring zone

ID: REQ-module-calendar-053
Source: bu-5ii4gj.5 (run 26 move 5), slice P3
Scope: v1-mandatory

Each calendar workspace entry SHALL carry `source_timezone`, the IANA zone the event was authored in
(the instance zone, else the event zone when the instance zone is invalid), alongside `timezone`, the zone `start_at` and `end_at`
are converted to for display. The field SHALL be set only for a provider event whose payload carried
a `timeZone` (recorded at mapping time as `source_zone_from_payload`). Internal events, scheduler
rows (which store the `UTC` follow-the-owner sentinel) and provider events with no payload zone (which
take the module config zone) SHALL read `null`, as SHALL a missing or invalid zone; a zone SHALL NOT
be inferred from the display zone or any default.

The grid and agenda show the event's own clock at the display zone's wall-clock geometry: an event
whose own-zone start falls on another date or across a DST change is still placed by its display-zone
time, and the second time is a label, not a placement.

#### Scenario: A London event under a Sydney display zone

- **WHEN** an event authored in `Europe/London` is read with a Sydney display zone
- **THEN** `source_timezone` is `Europe/London`, `timezone` is `Australia/Sydney`, and the start is the Sydney wall time of the same instant

#### Scenario: An invalid zone is unknown

- **WHEN** the stored zone is blank or not a zone name
- **THEN** `source_timezone` is `null`

#### Scenario: A write-time default is not the event's zone

- **WHEN** a scheduler row stores `UTC`, or a Google event arrives with no `timeZone`
- **THEN** `source_timezone` is `null`
