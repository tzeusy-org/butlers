## MODIFIED Requirements

### Requirement: Sidebar Navigation
The sidebar navigation is reorganized to remove the Traces entry from the Telemetry section. Trace visibility is now accessible through the Timeline tab on the Ingestion page.

#### Scenario: Navigation sections and items configuration
- **WHEN** the sidebar renders
- **THEN** navigation items are organized into three labelled sections displayed in order:
  1. **Main** — Overview (`/`, exact match), Butlers (`/butlers`), Sessions (`/sessions`), Ingestion (`/ingestion`), Approvals (`/approvals`), Memory (`/memory`), Secrets (`/secrets`), Settings (`/settings`)
  2. **Dedicated Butlers** — Relationships group (Contacts `/contacts`, Groups `/groups`; butler-aware on `relationship`), Education (`/education`; butler-aware on `education`), Health (`/health/measurements`), Calendar (`/calendar`)
  3. **Telemetry** — Timeline (`/timeline`), Notifications (`/notifications`), Issues (`/issues`), Audit Log (`/audit-log`)
- **AND** each section header is a clickable button containing an uppercase `text-[11px]` semibold label with `tracking-wider` and `text-muted-foreground/60` styling, plus a small chevron icon that rotates 90 degrees when expanded
- **AND** clicking a section header toggles its expanded/collapsed state with `max-h` and `opacity` transitions over 200ms
- **AND** Main and Dedicated Butlers sections default to expanded; Telemetry defaults to collapsed (`defaultExpanded: false`)
- **AND** a section auto-expands when any of its items (including group children) matches the current active route
- **AND** when the sidebar is collapsed (icon-only mode), section headers are hidden and sections are visually separated by a thin horizontal `border-border` divider
- **AND** sections with no visible items (all butler-filtered) are excluded from rendering
- **AND** each item renders a first-letter icon placeholder (the first character of the label in a 24x24 rounded `bg-muted` container) and the label text

### Requirement: Full Route Map
The router defines all application routes as children of the root layout. The `/traces` and `/traces/:traceId` routes are removed; requests to those paths are redirected to the Ingestion page.

#### Scenario: Top-level routes
- **WHEN** the router is initialized
- **THEN** the following routes are registered:
  - `/` -- Overview dashboard
  - `/butlers` -- Butler list
  - `/butlers/:name` -- Butler detail (parameterized)
  - `/sessions` -- Session list
  - `/sessions/:id` -- Session detail (parameterized)
  - `/timeline` -- Unified timeline
  - `/notifications` -- Notifications center
  - `/issues` -- Issues center
  - `/audit-log` -- Audit log
  - `/approvals` -- Approvals queue
  - `/approvals/rules` -- Approval standing rules
  - `/calendar` -- Calendar workspace
  - `/contacts` -- Contacts list
  - `/contacts/:contactId` -- Contact detail (parameterized)
  - `/groups` -- Groups list
  - `/costs` -- Costs and usage (not in sidebar)
  - `/memory` -- Memory system
  - `/settings` -- Local UI settings
  - `/secrets` -- Secrets management

#### Scenario: Health sub-routes
- **WHEN** the Health section is navigated to
- **THEN** the following health sub-routes are available:
  - `/health/measurements` -- Health measurements (sidebar entry point)
  - `/health/medications` -- Medications
  - `/health/conditions` -- Conditions
  - `/health/symptoms` -- Symptoms
  - `/health/meals` -- Meals
  - `/health/research` -- Research

#### Scenario: Ingestion routes with legacy redirects
- **WHEN** the ingestion section is navigated to
- **THEN** `/ingestion` renders the ingestion overview page with tabs including Connectors and Timeline
- **AND** `/ingestion/connectors/:connectorType/:endpointIdentity` renders the connector detail page
- **WHEN** a user visits the legacy `/connectors` path
- **THEN** they are redirected to `/ingestion?tab=connectors` via `<Navigate replace />`
- **WHEN** a user visits `/connectors/:connectorType/:endpointIdentity`
- **THEN** they are redirected to `/ingestion/connectors/:connectorType/:endpointIdentity` with query params preserved
- **WHEN** a user visits the legacy `/traces` or `/traces/:traceId` path
- **THEN** they are redirected to `/ingestion?tab=timeline` via `<Navigate replace />`

### Requirement: Keyboard Shortcuts System
The application supports vim-inspired two-key navigation shortcuts and search shortcuts, registered globally via the `useKeyboardShortcuts` hook. The `g then r` shortcut for Traces is removed.

#### Scenario: Search shortcuts
- **WHEN** the user presses `Cmd+K` or `Ctrl+K` (regardless of focus context)
- **THEN** the command palette opens
- **WHEN** the user presses `/` outside of input/textarea/contentEditable elements
- **THEN** the command palette opens

#### Scenario: Two-key "g" navigation
- **WHEN** the user presses `g` followed by a second key within 1 second
- **THEN** the application navigates to the corresponding route:
  - `g` then `o` -- Overview (`/`)
  - `g` then `b` -- Butlers (`/butlers`)
  - `g` then `s` -- Sessions (`/sessions`)
  - `g` then `t` -- Timeline (`/timeline`)
  - `g` then `n` -- Notifications (`/notifications`)
  - `g` then `i` -- Issues (`/issues`)
  - `g` then `a` -- Audit Log (`/audit-log`)
  - `g` then `m` -- Memory (`/memory`)
  - `g` then `c` -- Contacts (`/contacts`)
  - `g` then `h` -- Health (`/health/measurements`)
  - `g` then `e` -- Ingestion (`/ingestion`)
- **AND** the pending "g" state expires after 1 second if no second key is pressed
- **AND** shortcuts do not fire when focus is in an input, textarea, or contentEditable element

#### Scenario: Shortcut hints dialog
- **WHEN** the user clicks the floating "?" button in the bottom-right corner of the viewport
- **THEN** a dialog opens listing all available keyboard shortcuts with their key combinations

## REMOVED Requirements

### Requirement: Traces Route
**Reason**: The `/traces` page is superseded by the Timeline tab on the Ingestion page, which provides unified request-ID-anchored lineage instead of trace-ID-based lookups.
**Migration**: Navigate to `/ingestion?tab=timeline`. All existing `/traces` and `/traces/:traceId` bookmarks redirect automatically via `<Navigate replace />`.
