## ADDED Requirements

### Requirement: Canonical Route Map

The router SHALL define all application routes as children of the root layout. All routes SHALL share the shell, header, error boundary, and sidebar.

The route map SHALL include the Settings Console sub-routes and the ingestion dispatch console sub-routes as first-class child routes (not page-level `?tab=` state).

#### Scenario: Top-level routes

- **WHEN** the router is initialized
- **THEN** the following routes are registered:
  - `/` -- Overview dashboard
  - `/chat` -- Full-page chat, the global "Talk to Butlers" surface (bu-0ynlk.11)
  - `/chat/:conversationId` -- Full-page chat deep-linked to one conversation, resolved cross-butler by id (parameterized)
  - `/butlers` -- Butler list
  - `/butlers/:name` -- Butler detail (parameterized)
  - `/sessions` -- Session list
  - `/sessions/:id` -- Session detail (parameterized)
  - `/timeline` -- Unified timeline (operational cross-butler stream; sessions, notifications, errors)
  - `/chronicles` -- Chronicles page (retrospective lived-time reconstruction over Chronicler episodes; distinct from `/timeline`)
  - `/notifications` -- Notifications center
  - `/issues` -- Issues center
  - `/audit-log` -- Audit log
  - `/approvals` -- Approvals queue (rendered by `ApprovalsPage`)
  - `/approvals/rules` -- Approval standing rules
  - `/calendar` -- Calendar workspace
  - `/contacts` -- Redirect to `/entities/index?has=contact` (legacy bookmark compatibility; `public.contacts` was dropped in core_134)
  - `/contacts/:contactId` -- Redirect to `/entities/index?has=contact` (legacy per-contact bookmark compatibility)
  - `/groups` -- Groups list (not in sidebar; reachable via the relationship butler's CRM tab Quick Links)
  - `/spend` -- Canonical Spend page (`SpendPage`)
  - `/costs` -- Redirect to `/spend` (legacy bookmark compatibility)
  - `/memory` -- Memory system
  - `/memory/facts/:factId` -- Fact detail (parameterized)
  - `/memory/rules/:ruleId` -- Rule detail (parameterized)
  - `/memory/episodes/:episodeId` -- Episode detail (parameterized)
  - `/entities` -- Entity plex, the owner ego-graph landing (`PlexPage`)
  - `/entities/index` -- Entities index (`EntitiesIndexPage`)
  - `/entities/concentration` -- Entity concentration view
  - `/entities/hop`, `/entities/columns`, `/entities/social-map` -- retired views; redirect into the plex
  - `/entities/:entityId` -- Entity detail (parameterized)
  - `/health` -- Health overview (`HealthOverviewPage`)
  - `/health/measurements` -- Health measurements
  - `/health/medications` -- Health medications
  - `/health/conditions` -- Health conditions
  - `/health/symptoms` -- Health symptoms
  - `/health/meals` -- Health meals
  - `/health/research` -- Health research
  - `/education` -- Education (`EducationPage`)
  - `/ingestion` -- Ingestion Timeline ledger
  - `/ingestion/connectors` -- Ingestion connector roster
  - `/ingestion/connectors/:connectorType/:endpointIdentity` -- Ingestion connector detail (parameterized)
  - `/ingestion/filters` -- Ingestion Filters pipeline
  - `/qa` -- QA overview (`QaOverviewPage`)
  - `/qa/patrols/:patrolId` -- QA patrol detail (parameterized)
  - `/qa/investigations` -- QA investigations list
  - `/qa/investigations/:attemptId` -- QA investigation detail (parameterized)
  - `/system` -- System overview (`SystemPage`; version, uptime, DB state, backup state, egress catalog, butler heartbeats)
  - `/settings` -- Settings Console (`SettingsConsolePage`; system-side only)
  - `/settings/models` -- Settings model catalog (`SettingsModelsPage`)
  - `/settings/spend` -- Redirect to `/spend` (legacy Settings Console compatibility)
  - `/settings/permissions` -- Settings permissions (`SettingsPermissionsPage`)
  - `/secrets` -- Secrets management (per-user OAuth provider setup lives here, not under `/settings`)

#### Scenario: Settings Console routes

- **WHEN** the frontend router is configured
- **THEN** the following routes are registered, each rendering within the `RootLayout`:
  - `/settings` → `SettingsConsolePage`
  - `/settings/models` → `SettingsModelsPage`
  - `/settings/spend` → redirect to `/spend`
  - `/settings/permissions` → `SettingsPermissionsPage`
- **AND** the legacy `/settings` → `SettingsPage` registration is REMOVED and `frontend/src/pages/SettingsPage.tsx` is DELETED in the same change
- **AND** `/settings` is system-side only (catalog, spend, permissions, audit, webhooks)

#### Scenario: Approvals route replacement

- **WHEN** the frontend router is configured
- **THEN** `/approvals` renders the new `ApprovalsPage` (rewritten in this change), not the legacy page

#### Scenario: Per-user OAuth stays at /secrets

- **WHEN** the frontend router is configured
- **THEN** provider-setup cards (`GoogleOAuthSection`, `HomeAssistantSetupCard`, `OwnTracksSetupCard`, `SpotifySetupCard`, `SteamSetupCard`, `WhatsAppSetupCard`, `GoogleHealthStatusCard`) are consumed by `SecretsPage` and NOT by any `/settings/*` route
- **AND** per-user OAuth (Google, Spotify, Telegram, Steam, etc.) lives on `/secrets` to keep `/settings` system-side only

#### Scenario: Ingestion sub-routes share the dashboard shell

- **WHEN** the owner opens `/ingestion/connectors`
- **THEN** the route renders inside the root dashboard shell
- **AND** the sidebar and page header remain present
- **AND** the content is the ingestion connector roster, not a legacy tab panel
- **AND** these ingestion routes are first-class child routes; the redesigned ingestion surface SHALL NOT rely on a single `/ingestion` component with page-level `?tab=` state as its primary route map

#### Scenario: Ingestion connector detail is route-addressable

- **WHEN** the owner opens `/ingestion/connectors/:connectorType/:endpointIdentity`
- **THEN** the router loads the connector detail route directly
- **AND** refresh or deep-link navigation preserves the selected connector

#### Scenario: Legacy tab query state is compatibility only

- **WHEN** a legacy `/ingestion?tab=filters` URL is visited
- **THEN** the app normalizes it to `/ingestion/filters`
- **AND** future route ownership remains in `dashboard-ingestion-dispatch-console` rather than the shell spec

### Requirement: Canonical Keyboard Shortcuts System

The application SHALL support vim-inspired two-key navigation shortcuts and search shortcuts, registered globally via the `useKeyboardShortcuts` hook.

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
  - `g` then `c` -- Contacts (`/entities/index?has=contact`)
  - `g` then `h` -- Health (`/health`)
  - `g` then `e` -- Ingestion (`/ingestion`)
- **AND** the pending "g" state expires after 1 second if no second key is pressed
- **AND** shortcuts do not fire when focus is in an input, textarea, or contentEditable element

#### Scenario: Shortcut hints dialog

- **WHEN** the user clicks the floating "?" button in the bottom-right corner of the viewport
- **THEN** a dialog opens listing all available keyboard shortcuts with their key combinations
- **AND** the button has `opacity-60` by default and `opacity-100` on hover
- **AND** the button is fixed-positioned at `bottom-4 right-4` with `z-50`

#### Scenario: Page-scoped shortcut suspension

Page-scoped shortcuts (registered per-page via `useRegisterShortcut`, e.g. approvals triage j/k/a/d/x/u) are suspended in the contexts below so they never collide with typing or leak underneath an overlay that owns the keyboard.

- **WHEN** focus is in an `input`, `textarea`, `<select>`, or `contentEditable` element
- **THEN** page-scoped shortcuts do not fire
- **WHEN** focus sits inside any open dialog (`[role="dialog"]`), whether modal or not
- **THEN** the keystroke belongs to that dialog and page-scoped shortcuts do not fire
- **WHEN** a modal dialog (`[role="dialog"][aria-modal="true"]`, e.g. the command menu, the `?` help sheet, or any `useModalChoreography` overlay) is open
- **THEN** page-scoped shortcuts are suspended app-wide regardless of where focus sits
- **WHEN** only a non-modal dialog (`[role="dialog"]` without `aria-modal`, e.g. the persistent floating chat widget mounted in the shell) is open and focus is on the page
- **THEN** page-scoped shortcuts continue to fire — a non-modal overlay does not claim the app's keyboard
- **AND** a binding may opt out of all of the above suspension contexts by setting `allowWhenSuspended`

## MODIFIED Requirements

### Requirement: Sidebar Navigation (56px Icon Rail)

The sidebar SHALL be a fixed 56px-wide icon rail providing primary navigation. It SHALL consist of a brand mark, icon-only navigation items with floating tooltips, butler status dots, live badge indicators, and a footer status summary.

#### Scenario: Rail geometry

- **WHEN** the desktop sidebar renders
- **THEN** the `<aside>` element renders expanded at 240px (`md:w-60`) by default and collapses to a 56px icon rail (`md:w-14`), full viewport height, with a right border
- **AND** a collapse toggle switches between the two widths, persisting the collapsed state to `localStorage` under `butlers.sidebar-collapsed`

#### Scenario: Brand mark

- **WHEN** the rail renders its brand area
- **THEN** a 56px-tall brand row renders at the top of the rail with the letter "B" (or a wordmark if it fits) centered
- **AND** no "Butlers" full text is shown on the desktop rail (icon-only)

#### Scenario: Navigation sections and items configuration

- **WHEN** the sidebar renders
- **THEN** navigation items are organized into three labelled sections displayed in order:
  1. **Main** — Overview (`/`, exact match), Butlers (`/butlers`), QA (`/qa`; butler-aware on `qa`; badge), Ingestion (`/ingestion`), Approvals (`/approvals`; badge), Memory (`/memory`), Entities (`/entities`), Secrets (`/secrets`), Settings (`/settings`; badge)
  2. **Dedicated Butlers** -- Education (`/education`; butler-aware on `education`), Health (`/health`), Calendar (`/calendar`), Chronicles (`/chronicles`; butler-aware on `chronicler`)
  3. **Telemetry** -- Timeline (`/timeline`), Notifications (`/notifications`), Issues (`/issues`), Sessions (`/sessions`), Spend (`/spend`), Audit Log (`/audit-log`), System (`/system`)
- **AND** section headers are hidden on the desktop rail (icon-only mode); sections are separated by a thin horizontal `border-border` divider
- **AND** a section auto-expands when any of its items (including group children) matches the current active route
- **AND** sections with no visible items (all butler-filtered) are excluded from rendering
- **AND** each item in the Main and Telemetry sections renders a first-letter glyph (the first character of the label in a 24x24 rounded container) as the icon
- **AND** each item in the Dedicated Butlers section with a `butler` association renders a `ButlerMark` component (`tone="neutral"`) as the icon
- **AND** the Chronicles entry's tooltip SHALL read "Retrospective lived-time reconstruction" so it is unambiguously distinct from the operational Timeline entry under Telemetry

#### Scenario: Tooltip floating on hover or focus

- **WHEN** the user hovers over or focuses a nav item in the rail
- **THEN** a tooltip appears at `left: 56px` (anchored to the right edge of the rail) showing the item's label text
- **AND** the tooltip uses the Radix `Tooltip` primitive with `delayDuration={0}` (instant show)
- **AND** the tooltip dismisses when the cursor or focus leaves the item

#### Scenario: Active-state visual rule

- **WHEN** a nav item's route matches the current location
- **THEN** the item renders a 2px left border bar (`border-l-2 border-sidebar-primary`)
- **AND** the item background applies a subtle tint: 6% white in dark mode, 5% black in light mode
- **AND** inactive items on hover apply `hover:bg-sidebar-accent/50`

#### Scenario: Status dot on butler-associated items

- **WHEN** a nav item has a `butler` association and the named butler has status `degraded`
- **THEN** an amber 6px dot (`bg-amber-500`) renders at the top-right of the icon, with a ring matching the rail background (`ring-2 ring-background`)
- **WHEN** the named butler has status `error`
- **THEN** a red 6px dot (`bg-destructive`) renders at the top-right of the icon
- **WHEN** the named butler has status `ok` or is not present
- **THEN** no dot renders
- **AND** status data is read from the `useButlers()` hook which polls every 30 seconds

#### Scenario: Live badge indicators

- **WHEN** a nav item has `badgeKey: 'qa-escalations'` and the count is greater than 0
- **THEN** a red circle badge (`bg-red-500 text-white`) renders at the top-right of the icon with the count (capped at "99+")
- **AND** the count is the number of open QA escalations (`active_breakdown.escalated_open_cases` from `GET /api/qa/summary`), not the raw known-issue fingerprint count
- **WHEN** a nav item has `badgeKey: 'approvals-pending'` and the count is greater than 0
- **THEN** an amber circle badge (`bg-amber-500 text-white`) renders at the top-right of the icon
- **AND** badge and status dot do not overlap (badge takes precedence over status dot when both would render)

#### Scenario: No collapsible nav groups in the sidebar

- **WHEN** the sidebar renders
- **THEN** no collapsible nav group (group header glyph with expand/collapse chevron) appears
- **AND** the Relationships group is not rendered (its only remaining child, Groups, was removed from the navigation; the `/groups` page stays routable but is not surfaced in the sidebar)

#### Scenario: Sidebar Settings entry (single, un-nested)

- **WHEN** the sidebar renders
- **THEN** a `Settings` nav item links to `/settings`
- **AND** no separate sidebar entries exist for `/settings/models`, `/settings/spend`, or `/settings/permissions`; Models and Permissions are reached via the Console panels, while Spend uses the canonical `/spend` Telemetry entry

#### Scenario: Sidebar Approvals badge source

- **WHEN** the sidebar renders the `Approvals` nav item linking to `/approvals`
- **THEN** the badge count reflects `header.open_approvals` from `GET /api/settings/console` (or the equivalent live count)

#### Scenario: Footer status summary

- **WHEN** the rail renders its footer
- **THEN** a small dot indicator reflects the worst butler status (red for any `error`, amber for any `degraded`, green for all ok)
- **AND** the full summary text (e.g., "1 degraded, 2 awaiting approvals") is available via the `title` attribute on the footer element
- **AND** no visible text label renders in the footer (dot only)
- **WHEN** the butlers query is loading or has failed
- **THEN** the dot renders neutral/dim (`bg-muted-foreground/40`) to avoid a misleading green state
- **AND** the `title` attribute reads "Loading butlers" (loading) or "Butlers query failed" (error)

### Requirement: Settings Console Page

The Settings Console page (`SettingsConsolePage`) SHALL serve as the system-configuration root, aggregating attention items and sub-page summaries for the dashboard operator. It is system-side only; per-user preferences are not surfaced here.

#### Scenario: Settings Console renders header KPI strip

- **WHEN** the user visits `/settings`
- **THEN** a KPI strip shows four cells: Active Butlers, Spend MTD (USD), Open Approvals, and Models OK (verified count / total enabled count)
- **AND** the Open Approvals cell renders in red when the count is greater than zero
- **AND** each cell shows a skeleton placeholder while its data loads from `GET /api/settings/console`

#### Scenario: Settings Console renders AttentionStrip

- **WHEN** the user visits `/settings`
- **THEN** an attention strip is populated from `GET /api/settings/console`
- **AND** each attention item displays a tone-coloured indicator dot (red or amber), descriptive text, and a "Review" link that navigates to the item's `action_route`
- **AND** when the attention list is empty the strip shows "Everything is in hand."
- **AND** a truncated-count footer row appears when the server omits additional items for brevity, linking to `/audit-log`

#### Scenario: Settings Console renders panel grid

- **WHEN** the user visits `/settings`
- **THEN** a panel grid renders one panel per sub-surface: Models, Spend, Approvals, Permissions, and Secrets
- **AND** clicking a panel navigates to its corresponding route: `/settings/models`, `/spend`, `/approvals`, `/settings/permissions`, and `/secrets` respectively
- **AND** panels with a live data summary fetch independently so a slow or failing panel does not block the others

#### Scenario: Settings Console live stream

- **WHEN** the user visits `/settings`
- **THEN** the page subscribes to the settings WebSocket stream for live updates to header counts and attention items
- **AND** when the WebSocket connection is closed the page falls back to polling `GET /api/settings/console` every five minutes

## REMOVED Requirements

### Requirement: Full Route Map

**Reason**: The earlier route map names retired Contacts, Costs, and Settings Spend destinations.

**Migration**: Use the Canonical Route Map requirement in this change; compatibility aliases remain registered.

### Requirement: Keyboard Shortcuts System

**Reason**: The earlier shortcut map targets legacy Contacts and pre-overview Health destinations.

**Migration**: Use the Canonical Keyboard Shortcuts System requirement in this change.
