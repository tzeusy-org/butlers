## MODIFIED Requirements

### Requirement: State Tab (CRUD)
The state tab SHALL provide a browser and editor for the butler's key-value state store.
The server SHALL attach a fixed generic-access classification to every returned
row. Ordinary rows remain editable. Inspect-only managed rows remain visible
but SHALL NOT offer generic Edit or Delete controls. Private omitted rows SHALL
not be returned to the tab. The frontend SHALL consume the server-derived
classification and SHALL NOT duplicate key/prefix policy in browser code.

#### Scenario: State browser table
- **WHEN** state entries are loaded
- **THEN** a table displays: Key (monospace), Value (compact JSON preview, click to expand/collapse to full pretty-printed JSON), Updated timestamp, and Actions (Edit, Delete)
- **AND** the Actions (Edit, Delete) clause applies only to ordinary rows; an inspect-only row instead shows a fixed `Managed elsewhere` badge and no mutation control
- **AND** an omitted private row and its value are absent because the server filtered it before value retrieval

#### Scenario: Key prefix filter
- **WHEN** the operator types in the filter input
- **THEN** only entries whose key starts with the filter text (case-insensitive) are shown
- **AND** when no entries match, a message distinguishes between "no entries exist" and "no entries match the filter"
- **AND** filtering cannot reveal an omitted row or infer it from a placeholder/count

#### Scenario: Set new value
- **WHEN** the operator clicks "Set Value"
- **THEN** a dialog opens with Key (text input) and Value (JSON textarea) fields
- **AND** the value must be valid JSON; parse errors are shown inline
- **AND** submitting triggers a state set mutation with a success toast
- **AND** success is shown only after an ordinary-key mutation succeeds; a fixed managed-key refusal shows fixed unavailable-via-generic-state copy without echoing submitted content

#### Scenario: Edit existing value
- **WHEN** the operator clicks "Edit" on a state row
- **THEN** a dialog opens pre-filled with the entry's key (disabled) and pretty-printed JSON value
- **AND** saving triggers a state set mutation
- **AND** Edit is rendered only for an ordinary row whose server-derived classification permits generic set

#### Scenario: Delete with confirmation
- **WHEN** the operator clicks "Delete" on a state row
- **THEN** a confirmation dialog shows the key name and warns the action is irreversible
- **AND** confirming triggers a state delete mutation with a success toast
- **AND** Delete and its confirmation are rendered only for an ordinary row whose server-derived classification permits generic delete

#### Scenario: Auto-refresh
- **WHEN** the state tab is mounted
- **THEN** state entries are polled every 30 seconds
- **AND** each refresh replaces access classifications from the server rather than retaining stale browser authority

#### Scenario: Managed controls are truthful
- **WHEN** an inspect-only row is rendered or a Set Value submission receives `MANAGED_STATE_KEY`
- **THEN** the UI never offers Edit or Delete for that managed row and never emits a success toast for the refused mutation
- **AND** the fixed refusal copy contains no submitted value, stored value, framework validation detail, or inferred row existence
