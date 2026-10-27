## MODIFIED Requirements

### Requirement: Frontend-Gated Operation Safety

Several operations SHALL be intentionally restricted to the dashboard frontend as a safety measure, ensuring that credential provisioning and sensitive decisions require deliberate human interaction through a visual interface.

#### Scenario: Credential provisioning requires dashboard

- **WHEN** a new butler or connector needs API keys, tokens, or credentials
- **THEN** the operator must use the Secrets page to add the credentials
- **AND** there is no MCP tool, CLI command, or automated path to provision secrets (by design)
- **AND** this ensures the human operator maintains full awareness and control of what credentials are active

#### Scenario: OAuth bootstrap requires browser

- **WHEN** Google OAuth tokens need to be obtained or refreshed
- **THEN** the flow requires browser-based redirect to Google's consent screen
- **AND** this is architecturally impossible without the dashboard frontend
- **AND** the backend's `/api/oauth/google/start` endpoint generates CSRF-protected redirect URLs that must be followed in a browser context

#### Scenario: Approval decisions via dashboard as primary surface

- **WHEN** a pending action requires human decision
- **THEN** the dashboard provides the richest decision context: full tool arguments, agent summary, timestamps, execution history
- **AND** after a successful approval the dashboard offers an inline, separately confirmed opportunity to create a standing rule for the approved action; approval itself never creates a rule
- **AND** while MCP tools also expose approve/reject, the dashboard is the intended primary decision surface

#### Scenario: Connector monitoring is dashboard-exclusive

- **WHEN** the operator needs to assess connector fleet health
- **THEN** the only visual surface for connector liveness, volume, errors, and routing is the dashboard's ingestion pages
- **AND** there is no MCP tool equivalent for the aggregated visual monitoring provided by the ingestion pages

## REMOVED Requirements

### Requirement: Approval Queue and Decision Workflow

**Reason**: Describes the deleted dialog-based approvals page; the `/approvals` surface is specified in `dashboard-approvals`.

**Migration**: See `dashboard-approvals` for the queue, decision, and standing-rule behavior.

### Requirement: Approval Rules Management

**Reason**: The standalone `/approvals/rules` page is not routed; standing rules are managed from the `/approvals` Autonomy panel.

**Migration**: See `dashboard-approvals` for standing-rule management.

### Requirement: Ingestion and Connector Fleet Management

**Reason**: Describes the deleted tabbed ingestion page (Overview/Connectors/Filters/History tabs, fanout matrix, tier donut).

**Migration**: See `dashboard-ingestion-dispatch-console` for the `/ingestion` routes.

### Requirement: Connectors Tab Backfill Integration

**Reason**: The connector backfill UI no longer exists in the dashboard.

**Migration**: None; backfill is not a dashboard surface.

### Requirement: Settings as Operator Preferences

**Reason**: `/settings` is the system-configuration console; the browser-local preferences page it describes is not rendered.

**Migration**: Theme preference is specified in `dashboard-shell` "Dark Mode and Theme System"; the settings console in `dashboard-settings-console` "Settings Console Page".

### Requirement: Query Key Strategy and Cache Sharing

**Reason**: Implementation detail (cache-key literals) for deleted tab surfaces; not observable behavior.

**Migration**: Refresh behavior is specified in `dashboard-shell` "Bus-Aware Poll Architecture".
