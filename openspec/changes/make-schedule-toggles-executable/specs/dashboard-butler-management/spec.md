## MODIFIED Requirements

### Requirement: Schedules Tab (CRUD)
The schedules tab SHALL provide full CRUD management of a butler's scheduled tasks, including complexity tier configuration.

The enabled badge SHALL send an explicit requested `enabled` state through the dashboard schedules API and the canonical `schedule_toggle` MCP action. The tab SHALL wait for the server response rather than claim an optimistic state. A successful response SHALL provide the observed state and safe `schedule.toggle` audit evidence. Missing or managed refusals SHALL appear as errors, never as pause/resume successes. Each row SHALL remain pending until its own toggle request settles, even when another row's request settles first.

The dashboard SHALL attribute each schedule-toggle audit row to the authenticated owner, not the addressed butler. It SHALL retain the butler and schedule as target context and record the observed outcome for success or the bounded refusal code for failure.

#### Scenario: Schedule table columns
- **WHEN** schedules are loaded
- **THEN** a table displays: Name, Cron expression (monospace badge), Mode (prompt/job badge), Prompt/Job details (truncated to 80 chars), Complexity (tier badge), Enabled toggle (On/Off badge, clickable), Source, Next Run (relative time with absolute tooltip), Last Run (relative time with absolute tooltip), and Actions (Edit, Delete)

#### Scenario: Create schedule
- **WHEN** the operator clicks "Add Schedule"
- **THEN** a dialog opens with a form containing: Name (text input), Cron Expression (text input with standard 5-field hint), Mode selector (prompt or job), Complexity (dropdown: trivial, medium, high, extra_high; default medium), and mode-dependent fields
- **AND** in prompt mode: a Prompt textarea is shown
- **AND** in job mode: Job Name input and Job Args JSON textarea are shown
- **AND** the form validates that name and cron are non-empty, prompt is non-empty in prompt mode, and job name is non-empty with valid JSON args in job mode

#### Scenario: Edit schedule
- **WHEN** the operator clicks "Edit" on a schedule row
- **THEN** the same form dialog opens pre-filled with the schedule's existing values including complexity
- **AND** submission triggers an update mutation instead of create

#### Scenario: Delete schedule with confirmation
- **WHEN** the operator clicks "Delete" on a schedule row
- **THEN** a confirmation dialog appears with the schedule name and a warning that the action cannot be undone
- **AND** confirming the deletion triggers the delete mutation and shows a success toast

#### Scenario: Toggle schedule enabled state
- **WHEN** the operator clicks the enabled badge on an active schedule row
- **THEN** the schedule's enabled state is toggled via mutation and a toast confirms the action
- **AND** the mutation sends `enabled: false` to the canonical toggle action
- **AND** only that row's toggle control remains disabled until its request settles
- **AND** a pause success toast appears only after the response reports `observed_enabled=false`
- **WHEN** the operator clicks the disabled badge on a paused schedule row
- **THEN** the mutation sends `enabled: true`
- **AND** a resume success toast appears only after the response reports `observed_enabled=true`

#### Scenario: Server-observed toggle receipt is visible
- **WHEN** a toggle response reports an observed state and `audit.action="schedule.toggle"`
- **THEN** the schedules tab renders the observed enabled/disabled state
- **AND** it exposes the safe audit action/result as a local receipt

#### Scenario: Schedule toggle audit distinguishes actor from target
- **WHEN** the owner requests a schedule toggle that succeeds or receives a managed refusal
- **THEN** the audit row records the authenticated owner as actor
- **AND** it records the addressed butler and schedule separately as target context
- **AND** it records the observed outcome on success or the bounded refusal code on failure

#### Scenario: Managed or missing toggle refusal remains an error
- **WHEN** the canonical action returns `SCHEDULE_NOT_FOUND`, `SCHEDULE_TOML_MANAGED`, or `SCHEDULE_MANAGED`
- **THEN** the schedules tab shows the typed failure
- **AND** it does not show a success toast or optimistic state change for the refused row
- **AND** that row's toggle control becomes available again after the refusal

#### Scenario: Overlapping row toggles settle independently
- **WHEN** toggles for two different schedule rows are pending at the same time
- **AND** either request succeeds or is refused before the other settles
- **THEN** the settled row's toggle control becomes available
- **AND** the other row's toggle control remains disabled until its own request settles

#### Scenario: Repeated requested state does not double-flip
- **WHEN** the operator retries a toggle after the server already observes the requested state
- **THEN** the schedules tab accepts the `already_requested` receipt as the observed truth
- **AND** it does not imply that a second state transition occurred

#### Scenario: Auto-refresh
- **WHEN** the schedules tab is mounted
- **THEN** schedule data is polled every 30 seconds
