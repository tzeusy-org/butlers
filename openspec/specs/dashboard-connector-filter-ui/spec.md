# Dashboard — Connector Filter UI

## Purpose
Defines the frontend UI for managing source filter assignments at `/ingestion?tab=connectors`. Each ConnectorCard and the ConnectorDetailPage gain a Filters button that opens an assignment dialog. A separate Manage Filters panel provides CRUD for named filter objects without leaving the connectors page.

## Requirements

### Requirement: Filters Button on ConnectorCard
Each ConnectorCard in the connectors grid MUST display a Filters button that opens the filter assignment dialog without navigating to the connector detail page.

#### Scenario: Filters button placement and behavior
- **WHEN** a ConnectorCard is rendered
- **THEN** a "Filters" button (or icon button with filter icon) is visible in the card's header action area, alongside the LivenessBadge
- **AND** clicking the Filters button opens the ConnectorFiltersDialog for that connector
- **AND** the button's click handler MUST call `event.preventDefault()` and `event.stopPropagation()` to prevent the card's Link navigation from firing

#### Scenario: Active filter count badge
- **WHEN** a connector has one or more enabled source filters
- **THEN** the Filters button displays a numeric badge showing the count of enabled filters
- **AND** when the count is zero or no filters are assigned, no badge is shown

#### Scenario: Filters button on ConnectorDetailPage
- **WHEN** the ConnectorDetailPage is rendered
- **THEN** a "Manage Filters" button appears in the page header actions area (alongside any existing action buttons)
- **AND** clicking it opens the ConnectorFiltersDialog for that connector

### Requirement: ConnectorFiltersDialog
A Sheet or Dialog component listing all named source filters with per-connector enable/disable checkboxes.

#### Scenario: Dialog content — filter table
- **WHEN** the ConnectorFiltersDialog is open
- **THEN** it shows a table with columns: Enabled (checkbox) | Name | Mode | Key Type | Patterns
- **AND** rows are ordered by priority ASC, then name ASC
- **AND** enabled checkboxes reflect the current assignment state from `GET /connectors/{type}/{identity}/filters`
- **AND** filters that are incompatible with the connector's channel are shown with a warning indicator and their checkbox is disabled

#### Scenario: Enable/disable a filter
- **WHEN** the user toggles a filter's enabled checkbox
- **THEN** the local state is updated immediately (optimistic UI)
- **AND** a Save button becomes active

#### Scenario: Save assignments
- **WHEN** the user clicks Save
- **THEN** the dialog calls `PUT /connectors/{type}/{identity}/filters` with the full list of filter assignments (enabled state + priority for each filter)
- **AND** on success the dialog closes and the ConnectorCard's active filter badge refreshes
- **AND** on error a toast notification is shown and the dialog remains open with the previous state

#### Scenario: Empty state
- **WHEN** no named filters exist
- **THEN** the dialog shows an empty state message: "No filters configured. Create filters in Manage Filters."
- **AND** a "Manage Filters" link is visible that opens the ManageSourceFiltersPanel

#### Scenario: Manage Filters link
- **WHEN** the user clicks "Manage Filters" inside the ConnectorFiltersDialog
- **THEN** the ConnectorFiltersDialog closes (or transitions) and the ManageSourceFiltersPanel opens
- **AND** after closing the ManageSourceFiltersPanel, the ConnectorFiltersDialog can be re-opened with refreshed filter data

### Requirement: ManageSourceFiltersPanel
A Sheet panel for CRUD on named source filter objects, accessible from the ConnectorFiltersDialog.

#### Scenario: Panel content — filter list
- **WHEN** the ManageSourceFiltersPanel is open
- **THEN** it shows all named filters in a table with columns: Name | Mode | Key Type | Patterns | Actions (Edit / Delete)
- **AND** a "Create filter" button is visible at the top of the panel

#### Scenario: Create filter form
- **WHEN** the user clicks "Create filter"
- **THEN** an inline form (or expandable row) appears with fields: Name (text, required), Description (text, optional), Mode (Blacklist / Whitelist, radio), Key Type (dropdown of valid values), Patterns (tag/chip input — one per line or comma-separated, shown as pills)
- **AND** submitting calls `POST /source-filters`
- **AND** on success the new filter appears in the list without a full page reload
- **AND** on duplicate name an inline error message is shown beneath the Name field

#### Scenario: Edit filter
- **WHEN** the user clicks Edit on a filter row
- **THEN** the row expands to show an edit form pre-filled with the filter's current `name`, `description`, and `patterns`
- **AND** `filter_mode` and `source_key_type` are displayed as read-only labels (they are immutable after creation)
- **AND** saving calls `PATCH /source-filters/{id}` and refreshes the list row in place

#### Scenario: Delete filter
- **WHEN** the user clicks Delete on a filter row
- **THEN** a confirmation dialog is shown: "Delete filter '{name}'? This will also remove it from all connector assignments."
- **AND** confirming calls `DELETE /source-filters/{id}`
- **AND** the filter is removed from the list; all connectors that had this filter assigned will reflect the removal on their next filter dialog open

#### Scenario: Pattern input validation
- **WHEN** the user submits a create or edit form with an empty patterns list
- **THEN** the form shows a validation error: "At least one pattern is required"
- **AND** the API call is not made

### Requirement: API Hooks
New React Query hooks to support the filter UI.

#### Scenario: useConnectorFilters hook
- **WHEN** `useConnectorFilters(connectorType, endpointIdentity)` is called
- **THEN** it fetches from `GET /connectors/{type}/{identity}/filters` and returns `ConnectorFilterAssignment[]`
- **AND** it is invalidated after `useUpdateConnectorFilters` mutates successfully

#### Scenario: useUpdateConnectorFilters hook
- **WHEN** `useUpdateConnectorFilters().mutate({connectorType, endpointIdentity, assignments})` is called
- **THEN** it calls `PUT /connectors/{type}/{identity}/filters` and on success invalidates the `useConnectorFilters` cache for that connector

#### Scenario: useSourceFilters hook
- **WHEN** `useSourceFilters()` is called
- **THEN** it fetches from `GET /source-filters` and returns `SourceFilter[]`
- **AND** it is invalidated after any create, update, or delete mutation

#### Scenario: useCreateSourceFilter, useUpdateSourceFilter, useDeleteSourceFilter hooks
- **WHEN** any of these mutation hooks completes successfully
- **THEN** the `useSourceFilters` query cache is invalidated
- **AND** the `useConnectorFilters` cache is also invalidated (a deleted filter affects connector assignments)
