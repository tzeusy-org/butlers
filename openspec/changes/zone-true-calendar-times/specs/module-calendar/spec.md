## ADDED Requirements

### Requirement: Workspace entries keep the event's authoring zone

ID: REQ-module-calendar-053
Source: bu-5ii4gj.5 (run 26 move 5), slice P3
Scope: v1-mandatory

Each calendar workspace entry SHALL carry `source_timezone`, the IANA zone the event was authored in
(the instance zone, else the event zone), alongside `timezone`, the zone `start_at` and `end_at`
are converted to for display. When the stored zone is missing or is not a valid IANA zone the field
SHALL be `null`; a zone SHALL NOT be inferred from the display zone or any default.

#### Scenario: A London event under a Sydney display zone

- **WHEN** an event authored in `Europe/London` is read with a Sydney display zone
- **THEN** `source_timezone` is `Europe/London`, `timezone` is `Australia/Sydney`, and the start is the Sydney wall time of the same instant

#### Scenario: An invalid zone is unknown

- **WHEN** the stored zone is blank or not a zone name
- **THEN** `source_timezone` is `null`
