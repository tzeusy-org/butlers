## MODIFIED Requirements

### Requirement: Switchboard Triage Filters

The filters surface (accessible from the ingestion page at `/ingestion?tab=filters`) manages unified ingestion rules, thread affinity settings, and Gmail label filters. It replaces the previous dual-model UI (triage rules table + ManageSourceFiltersPanel sheet) with a single rules table.

#### Scenario: Unified rules table with CRUD
- **WHEN** the user navigates to `/ingestion?tab=filters`
- **THEN** they see a single table of all ingestion rules with columns: Priority, Scope, Condition, Action, Enabled toggle, Actions (edit/delete)

#### Scenario: Scope display and filtering
- **WHEN** the rules table is rendered
- **THEN** each rule's scope is shown as a badge: "Global" for global rules, or the connector identity (e.g., "gmail:user:dev") for connector-scoped rules
- **AND** a scope filter dropdown above the table allows filtering by "All", "Global only", or specific connector scopes

#### Scenario: Rule editor drawer with scope selector
- **WHEN** the user creates or edits a rule
- **THEN** the rule editor drawer includes a scope selector (Global / Connector) and, when Connector is selected, a connector type and endpoint identity picker
- **AND** the action field is constrained based on scope: connector scope only allows "block"; global allows all actions

#### Scenario: Test rule dry-run
- **WHEN** the user clicks "Test" in the rule editor
- **THEN** a test envelope is sent to POST `/ingestion-rules/test` and the result is displayed inline (matched/no-match with reason)

#### Scenario: Thread affinity panel preserved
- **WHEN** the user scrolls below the rules table
- **THEN** the thread affinity panel (enable/disable toggle + TTL input) is displayed unchanged

#### Scenario: Import seed rules
- **WHEN** the user clicks "Import defaults"
- **THEN** a preview dialog shows the 9 default seed rules (now as global ingestion rules) and imports them on confirmation

#### Scenario: Connector detail page shows scoped rules
- **WHEN** the user navigates to a connector detail page (e.g., `/ingestion/connectors/gmail/gmail:user:dev`)
- **THEN** the page shows a rules section listing only rules with `scope = 'connector:gmail:gmail:user:dev'`, with an "+ Add Rule" button that pre-fills the scope

## REMOVED Requirements

### Requirement: ManageSourceFiltersPanel
**Reason**: Replaced by the unified rules table. Named source filter objects no longer exist as a separate concept — each filter pattern is now an individual ingestion rule.
**Migration**: The "Manage Filters" button, ManageSourceFiltersPanel sheet component, and all source filter CRUD hooks are removed. Users manage connector-scoped block rules directly in the unified table or from the connector detail page.

### Requirement: ConnectorFiltersDialog
**Reason**: Replaced by the connector-scoped rules section on the connector detail page. The checkbox-based filter assignment dialog is no longer needed — rules are created directly with the appropriate scope.
**Migration**: The ConnectorFiltersDialog component and its trigger button on ConnectorCard/ConnectorDetailPage are removed. The connector detail page instead shows a filtered view of ingestion rules for that connector's scope.

### Requirement: Source filter API hooks
**Reason**: `use-source-filters.ts` hooks (useSourceFilters, useCreateSourceFilter, useUpdateSourceFilter, useDeleteSourceFilter) and connector filter hooks (useConnectorFilters, useUpdateConnectorFilters) are replaced by unified ingestion rules hooks.
**Migration**: New hooks: `useIngestionRules(params)`, `useCreateIngestionRule()`, `useUpdateIngestionRule()`, `useDeleteIngestionRule()`, `useTestIngestionRule()`. These query `/api/switchboard/ingestion-rules` with optional scope filtering.

### Requirement: Triage rule hooks
**Reason**: `use-triage.ts` hooks (useTriageRules, useCreateTriageRule, useUpdateTriageRule, useDeleteTriageRule, useTestTriageRule) are replaced by unified ingestion rules hooks.
**Migration**: Same unified hooks as above. The `rule_type` and `action` fields are unchanged; only the endpoint path changes from `/triage-rules` to `/ingestion-rules`.
