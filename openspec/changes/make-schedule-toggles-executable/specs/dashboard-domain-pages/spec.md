## MODIFIED Requirements

### Requirement: Calendar butler view with lane-based display

In butler view, the calendar MUST display events grouped by butler lane. Each lane MUST show:
- Lane header with butler name (titleized), event count, and an "Add event" button.
- A table with columns: Time (formatted window), Title, Type ("Schedule" or "Reminder"), Status (badge), Actions (Edit, Toggle pause/resume, Delete buttons).

Recurring events from the same parent MUST be capped at 10 instances per day per lane. When instances are capped, an overflow row MUST display: "... and N more instances of 'Event Title'".

The butler schedule toggle action SHALL send an explicit requested `enabled`
state through the canonical `schedule_toggle` MCP action. It MUST wait for the
server response rather than claim an optimistic state. A successful response
MUST provide the observed state and safe `schedule.toggle` audit evidence; a
typed missing or managed refusal MUST be rendered as an error and MUST NOT be
rendered as a pause/resume success.

#### Scenario: Butler event toggle pause/resume

- **WHEN** the user clicks the toggle button on an active butler event
- **THEN** the mutation MUST send `action: "toggle"` with `enabled: false`
- **AND** the dashboard MUST wait for the canonical action's response
- **AND** a success toast "Event paused" MUST appear
- **AND** the toast MUST appear only after the response reports
  `observed_enabled=false`
- **WHEN** the user clicks toggle on a paused event
- **THEN** the mutation MUST send `enabled: true`
- **AND** a success toast "Event resumed" MUST appear
- **AND** the toast MUST appear only after the response reports
  `observed_enabled=true`

#### Scenario: Server-observed toggle receipt is visible

- **WHEN** a toggle response reports an observed state and
  `audit.action="schedule.toggle"`
- **THEN** the schedules tab MUST render the observed enabled/disabled state
- **AND** it MUST expose the safe audit action/result as a local receipt

#### Scenario: Managed toggle refusal remains an error

- **WHEN** the canonical action returns `SCHEDULE_TOML_MANAGED` or
  `SCHEDULE_MANAGED`
- **THEN** the dashboard MUST show the typed failure
- **AND** it MUST NOT show a success toast or optimistic state change

#### Scenario: Repeated requested state does not double-flip

- **WHEN** the owner retries a toggle after the server already observes the
  requested state
- **THEN** the dashboard MUST accept the `already_requested` receipt as the
  observed truth
- **AND** it MUST not imply that a second state transition occurred
