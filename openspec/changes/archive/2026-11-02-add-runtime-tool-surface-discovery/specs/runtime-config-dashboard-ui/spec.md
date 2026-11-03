## MODIFIED Requirements

### Requirement: Runtime config card displays runtime config from DB

The butler detail Management tab SHALL display the effective runtime config read from the `runtime-config` API endpoint (via the `RuntimeConfigCard` component), not from the raw toml.

ID: REQ-runtime-config-dashboard-ui-001
Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Card shows DB values
- **WHEN** the user opens the Management tab for a butler
- **THEN** the `RuntimeConfigCard` SHALL show current runtime config values (`core_groups`, `max_concurrent`, `max_queued`) from the DB with editable fields
- **AND** the resolved model and `session_timeout_s` are shown read-only with an "edit in models" link to the Models tab

#### Scenario: Card distinguishes declaration, decision, and registration
- **WHEN** the user opens the Management tab for a butler
- **THEN** the card SHALL show Git-declared, runtime-effective, and actually registered counts
- **AND** any non-empty diff SHALL name the missing or widened tools/groups and the effective source
- **AND** a bounded module registration error SHALL name each declared-but-not-registered tool, its module, and the exception type
- **AND** an unavailable declaration snapshot SHALL be named explicitly instead of displaying dashes as an unknown all-clear

#### Scenario: Cold fields show restart badge
- **WHEN** a cold field (core_groups, max_concurrent, max_queued) is displayed
- **THEN** the field SHALL show a visual indicator that changes require a daemon restart

#### Scenario: Stored exposure policy can be edited

- **WHEN** the Management-tab runtime config card has loaded
- **THEN** it SHALL offer `Eager filtered` and `Automatic verified discovery` choices for `tool_exposure_policy`
- **AND** selecting a choice SHALL stage an edit for the existing Save action
- **AND** the control SHALL NOT constitute evidence of runtime-native discovery support

### Requirement: Config tab supports inline editing

The config tab SHALL allow the user to edit runtime config fields and save via the PATCH endpoint.

ID: REQ-runtime-config-dashboard-ui-002
Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Edit and save a field
- **WHEN** the user edits a field value and clicks save
- **THEN** the PATCH endpoint SHALL be called and the UI SHALL reflect the updated value

#### Scenario: Restart-required feedback after saving cold field
- **WHEN** the user saves a change that includes cold fields
- **THEN** the UI SHALL display a notification listing which fields require a daemon restart to take effect

#### Scenario: Policy save uses the existing PATCH result

- **WHEN** the user saves a policy-only edit successfully
- **THEN** the card SHALL clear the pending edit and use the returned `restart_required` list
- **AND** an empty list SHALL not produce a restart-required notification

#### Scenario: Failed policy save discards the unconfirmed policy edit

- **WHEN** the policy PATCH fails
- **THEN** the card SHALL remove the pending exposure-policy edit and retain the server-confirmed value
- **AND** other pending field edits SHALL remain available for retry

## Source References

- Non-Negotiable Rule 3 (MCP inter-butler communication)
- Non-Negotiable Rule 5 (DB-persisted operational tuning)
- RFC 0027 (catalog and exposure contract; unlanded runtime work succeeds to RFC 0038)
