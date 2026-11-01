## MODIFIED Requirements

### Requirement: Where-You-Went Map Trail

The day view SHALL render the day's movement as a map trail, subject to the
existing map privacy contract. The trail SHALL omit point events that
Chronicler flagged as impossible jumps (`payload.trail_outlier`), the same
fixes its location episodes exclude.

ID: REQ-dashboard-chronicles-004
Source: bu-2tsgqb.6; butler-chronicler "OwnTracks Location Trail Quality"
Scope: v1-mandatory

#### Scenario: Movement rendered as a trail

- **WHEN** the day has movement evidence and the map privacy contract permits
- **THEN** a trail of the day's locations is rendered

#### Scenario: Impossible jumps are not drawn

- **WHEN** a day's point events include a fix carrying `payload.trail_outlier`
- **THEN** the trail SHALL NOT include that fix
- **AND** the remaining fixes SHALL render in time order
