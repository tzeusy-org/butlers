## MODIFIED Requirements

### Requirement: Schedules Tab (CRUD)
The schedules tab provides full CRUD management of a butler's scheduled tasks, including complexity tier configuration.

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
- **WHEN** the operator clicks the enabled/disabled badge on a schedule row
- **THEN** the schedule's enabled state is toggled via mutation and a toast confirms the action

#### Scenario: Auto-refresh
- **WHEN** the schedules tab is mounted
- **THEN** schedule data is polled every 30 seconds

### Requirement: Trigger Tab (Manual Session Invocation)
The trigger tab allows operators to manually spawn a session for a butler with complexity-aware model selection.

#### Scenario: Prompt input and submission
- **WHEN** the trigger tab is active
- **THEN** a card with a textarea, a complexity selector (dropdown: Trivial, Medium, High, Extra High; default Medium), and "Trigger Session" button is shown
- **AND** the button is disabled when the textarea is empty or a trigger is in flight

#### Scenario: Resolved model preview
- **WHEN** the operator selects a complexity level
- **THEN** below the dropdown, a muted text line shows the resolved model (e.g. "Will use: claude-sonnet via claude-code")
- **AND** the preview updates reactively when complexity selection changes

#### Scenario: Skill pre-fill from query parameter
- **WHEN** the URL contains a `skill` query parameter
- **THEN** the prompt textarea is pre-filled with "Use the {skill} skill to "

#### Scenario: Result display
- **WHEN** a trigger completes
- **THEN** a result card shows a Success (emerald) or Failed (destructive) badge
- **AND** successful results show the output in a monospace block with a link to the session
- **AND** failed results show the error message

#### Scenario: Ephemeral trigger history
- **WHEN** triggers have been issued during the current page session
- **THEN** a "Trigger History" card lists all previous triggers with their status badge, prompt text (truncated), complexity tier badge, timestamp, and session link
- **AND** this history is not persisted and resets on page reload

### Requirement: Sessions Tab
The sessions tab shows paginated session history for the butler with drill-down capability, including model resolution metadata.

#### Scenario: Paginated session table
- **WHEN** the sessions tab is active
- **THEN** sessions are loaded with offset-based pagination (page size 20) and displayed in a session table
- **AND** the butler column is hidden since the context is already butler-scoped
- **AND** each session row shows the model used and complexity tier as a badge

#### Scenario: Session detail drawer
- **WHEN** the operator clicks a session row
- **THEN** a drawer opens showing full session details for the selected session
- **AND** the drawer includes model resolution metadata: model alias, runtime type, complexity tier, and resolution source (catalog or toml_fallback)

#### Scenario: Pagination controls
- **WHEN** the total session count exceeds one page
- **THEN** "Previous" and "Next" buttons are shown with the current page number and total pages
- **AND** "Previous" is disabled on the first page and "Next" is disabled when `has_more` is false
