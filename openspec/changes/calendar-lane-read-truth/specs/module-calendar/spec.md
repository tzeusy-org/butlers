## ADDED Requirements

### Requirement: Failed Lane Reads Are Unavailable, Not Empty

The calendar workspace SHALL NOT present a failed read as an empty one. For the proposals lane the
read response SHALL carry `proposals_available` and `proposals_dropped_rows`; for the overlays
lane and the day briefing it SHALL carry `overlays_available`. `proposals_available` and
`overlays_available` SHALL be `false` when any targeted schema's read failed, the fan-out raised,
or (for overlays) no calendar-enabled reader exists, and SHALL be `true` for a confirmed-empty
read. The dashboard SHALL render an unavailable state for a `false` flag, distinct from the empty
state ("Nothing awaiting your review", "No domain context for this day"), and SHALL still list
whatever entries could be read, saying "Some proposals couldn't be loaded; this list may be
incomplete." when some are shown and "Proposals unavailable" only when none loaded, in a
`role="status"` banner. A row that cannot be converted SHALL be counted in
`proposals_dropped_rows` without discarding the rows already read, and each schema read SHALL have a
5 second deadline so a hung schema is marked unavailable instead of hanging the response. The read
SHALL still return HTTP 200, never a 500.

ID: REQ-module-calendar-047
Source: bu-5ii4gj.4 (run 26 move 4), slice C
Scope: v1-mandatory

#### Scenario: A failed proposals read renders as unavailable

- **WHEN** one butler schema's proposals read fails and another returns a pending proposal
- **THEN** the response lists the readable proposal and `proposals_available` is `false`
- **AND** with no readable proposal the panel shows "Proposals unavailable", never "Nothing awaiting your review"

#### Scenario: A failed overlay read is not "no domain context"

- **WHEN** the overlay view cannot be read
- **THEN** `overlays_available` is `false` and the overlays toolbar and day briefing show an unavailable state
- **AND** a read that succeeds with no contribution keeps `overlays_available` `true` and shows "No domain context"
