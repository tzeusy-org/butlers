## MODIFIED Requirements

### Requirement: Runtime config card displays runtime config from DB

The butler detail Management tab SHALL display the effective runtime config read from the `runtime-config` API endpoint (via the `RuntimeConfigCard` component), not from the raw toml.

ID: REQ-runtime-config-dashboard-ui-001
Source: RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine
Scope: v1-mandatory

#### Scenario: Card shows DB values
- **WHEN** the user opens the Management tab for a butler
- **THEN** the `RuntimeConfigCard` SHALL show current runtime config values (`core_groups`, `max_concurrent`, `max_queued`) from the DB with editable fields
- **AND** the resolved model and `session_timeout_s` are shown read-only with an "edit in models" link to the Models tab
- **AND** `tool_exposure_policy` SHALL be shown as an editable `Eager filtered` or `Automatic verified discovery` choice with concise fallback guidance

#### Scenario: Cold fields show restart badge
- **WHEN** a cold field (core_groups, max_concurrent, max_queued) is displayed
- **THEN** the field SHALL show a visual indicator that changes require a daemon restart

#### Scenario: Hot exposure policy shows next-session behavior

- **WHEN** `tool_exposure_policy` is displayed or changed
- **THEN** the card SHALL state that the setting applies to newly planned sessions without a daemon restart
- **AND** `auto` SHALL be described as verified-native-when-available with eager fallback, not as a guarantee that native discovery will run

#### Scenario: Unavailable policy is not presented as active

- **WHEN** the runtime-config GET request fails or returns degraded/unavailable evidence
- **THEN** the card SHALL show the policy as unavailable rather than substituting `eager_filtered` or `auto`
- **AND** it SHALL not present an unconfirmed value as the effective runtime policy

### Requirement: Config tab supports inline editing

The config tab SHALL allow the user to edit runtime config fields and save via
the PATCH endpoint. For this requirement, the historical “config tab” name
refers to the editable
`RuntimeConfigCard` that the preceding requirement places in the butler-detail
Management tab; this change does not move the card to the raw Config tab.

ID: REQ-runtime-config-dashboard-ui-002
Source: RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine
Scope: v1-mandatory

#### Scenario: Edit and save a field
- **WHEN** the user edits a field value and clicks save
- **THEN** the PATCH endpoint SHALL be called and the UI SHALL reflect the updated value

#### Scenario: Restart-required feedback after saving cold field
- **WHEN** the user saves a change that includes cold fields
- **THEN** the UI SHALL display a notification listing which fields require a daemon restart to take effect

#### Scenario: Hot policy save does not claim restart

- **WHEN** the user saves only `tool_exposure_policy`
- **THEN** the UI SHALL confirm that the policy applies to subsequent sessions
- **AND** it SHALL not show a restart-required notification

#### Scenario: Failed policy save preserves confirmed state

- **WHEN** the policy PATCH fails validation, transport, or persistence
- **THEN** the UI SHALL show an actionable error and retain or restore the last server-confirmed policy
- **AND** it SHALL not claim that the edited value is active

## Source References

- Non-Negotiable Rule 5 (operational tuning is DB-backed)
- RFC 0007 (dashboard and API surface)
- RFC 0027 (runtime tool surface discovery and exposure)
