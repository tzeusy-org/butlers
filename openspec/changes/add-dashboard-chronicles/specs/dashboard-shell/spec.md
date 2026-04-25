# Dashboard Shell — Delta

This delta adds the `/chronicles` route and its sidebar entry to the dashboard shell so the existing route map and navigation contracts stay synchronized with the new Chronicles page.

## MODIFIED Requirements

### Requirement: Sidebar Navigation

The sidebar SHALL provide the primary navigation for the entire dashboard. It SHALL consist of a brand header, a scrollable navigation list, a footer with spend summary, and a collapse toggle.

#### Scenario: Brand header

- **WHEN** the sidebar is expanded
- **THEN** the brand text "Butlers" renders as a semibold `text-lg` element in the 56px-tall header area
- **WHEN** the sidebar is collapsed
- **THEN** the brand text fades out (`opacity-0 w-0 overflow-hidden`) and the single letter "B" renders instead

#### Scenario: Navigation sections and items configuration

- **WHEN** the sidebar renders
- **THEN** navigation items are organized into three labelled sections displayed in order:
  1. **Main** — Overview (`/`, exact match), Butlers (`/butlers`), Sessions (`/sessions`), Ingestion (`/ingestion`), Approvals (`/approvals`), Memory (`/memory`), Secrets (`/secrets`), Settings (`/settings`)
  2. **Dedicated Butlers** — Relationships group (Contacts `/contacts`, Groups `/groups`; butler-aware on `relationship`), Education (`/education`; butler-aware on `education`), Health (`/health/measurements`), Calendar (`/calendar`), Chronicles (`/chronicles`; butler-aware on `chronicler`)
  3. **Telemetry** — Timeline (`/timeline`), Notifications (`/notifications`), Issues (`/issues`), Audit Log (`/audit-log`)
- **AND** each section header is a clickable button containing an uppercase `text-[11px]` semibold label with `tracking-wider` and `text-muted-foreground/60` styling, plus a small chevron icon that rotates 90 degrees when expanded
- **AND** clicking a section header toggles its expanded/collapsed state with `max-h` and `opacity` transitions over 200ms
- **AND** Main and Dedicated Butlers sections default to expanded; Telemetry defaults to collapsed (`defaultExpanded: false`)
- **AND** a section auto-expands when any of its items (including group children) matches the current active route
- **AND** when the sidebar is collapsed (icon-only mode), section headers are hidden and sections are visually separated by a thin horizontal `border-border` divider
- **AND** sections with no visible items (all butler-filtered) are excluded from rendering
- **AND** each item renders a first-letter icon placeholder (the first character of the label in a 24x24 rounded `bg-muted` container) and the label text
- **AND** the Chronicles entry's tooltip SHALL read "Retrospective lived-time reconstruction" so it is unambiguously distinct from the operational Timeline entry under Telemetry

### Requirement: Full Route Map

The router SHALL define all application routes as children of the root layout. All routes SHALL share the shell, header, error boundary, and sidebar.

#### Scenario: Top-level routes

- **WHEN** the router is initialized
- **THEN** the following routes are registered:
  - `/` -- Overview dashboard
  - `/butlers` -- Butler list
  - `/butlers/:name` -- Butler detail (parameterized)
  - `/sessions` -- Session list
  - `/sessions/:id` -- Session detail (parameterized)
  - `/timeline` -- Unified timeline (operational cross-butler stream; sessions, notifications, errors)
  - `/chronicles` -- Chronicles page (retrospective lived-time reconstruction over Chronicler episodes; distinct from `/timeline`)
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
