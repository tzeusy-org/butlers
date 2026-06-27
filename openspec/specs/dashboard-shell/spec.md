# Dashboard Application Shell

## Purpose

The Butlers dashboard is the **primary administrative gateway** for operating the entire butler system. It is not a secondary monitoring view -- it IS the control plane through which human operators detect failures, diagnose runtime behavior, and take corrective action. Every butler, session, trace, notification, approval, and domain entity is accessible exclusively through this single-pane-of-glass interface.

The backend is distributed across multiple butlers, modules, and databases. Without a unified UI, operators must jump between logs, DB queries, and daemon endpoints. The dashboard eliminates this by combining cross-butler status, connector health, session/trace visibility, approval governance, domain data browsing, and admin controls into a single pane. This reduces operational latency for three critical loops:

- **Detect:** Identify what is failing, degraded, or expensive.
- **Diagnose:** Inspect sessions, traces, state, and timeline context.
- **Act:** Trigger runs, update schedules, correct state, and debug MCP tools from one UI.

### Scope Boundaries

**In scope:**
- Monitoring and diagnostics across butlers.
- Read-heavy data exploration across domain surfaces.
- Selected write/admin operations through dashboard API endpoints.
- Keyboard-first navigation and quick search for operational speed.

**Out of scope:**
- Replacing chat as the main user interaction path.
- Full CRUD for every domain entity (many domain screens are read-focused).
- End-user workflow UX (this is an operator/admin dashboard).

### Cross-Cutting UX Contracts

All data-bearing surfaces follow consistent state patterns:

- **Loading:** Skeleton placeholders matching the layout of real-data counterparts.
- **Empty:** Explicit empty-state message with contextual guidance toward the creation action.
- **Error:** Explicit error text; in select cases (e.g., butler list), stale cached data remains visible with a warning banner.

The application shell defines the outermost structural frame: the sidebar navigation, page header with breadcrumbs, command palette, keyboard shortcuts, theme system, loading/error/empty state patterns, auto-refresh architecture, and the full UI primitive library. All domain pages render inside this shell and inherit its design system, responsive behavior, and operational affordances.

The technology stack is: React 18 with TypeScript, React Router v7 (browser router), TanStack Query v5 for server state, Tailwind CSS v4 with shadcn/ui components (backed by Radix UI primitives), Lucide icons, Sonner toast notifications, class-variance-authority for variant-driven styling, and Vite as the build tool.
## Requirements
### Requirement: Application Entry Point and Provider Hierarchy

The application SHALL boot via a React 18 StrictMode render. The provider hierarchy is: `StrictMode` > `QueryClientProvider` (TanStack Query) > `RouterProvider` (React Router). ReactQueryDevtools are included in development builds but start closed.

#### Scenario: Application mounts successfully

- **WHEN** the browser loads the root HTML document
- **THEN** React renders the `App` component inside `StrictMode`
- **AND** `QueryClientProvider` wraps the entire router with a shared `QueryClient` instance
- **AND** `RouterProvider` initializes browser-based routing with `createBrowserRouter`
- **AND** the `BASE_URL` from Vite's `import.meta.env` is normalized and passed as the router `basename`

#### Scenario: TanStack Query default configuration

- **WHEN** the `QueryClient` is created
- **THEN** the default `staleTime` for all queries is 30 seconds (30,000ms)
- **AND** the default retry count is 1 (one retry after initial failure)
- **AND** these defaults can be overridden per-query by individual hooks

### Requirement: Owner Timezone Resolution (cross-cutting shell contract)

The dashboard application shell SHALL provide a dashboard-wide owner timezone context so that
every page renders timestamps in the owner's configured timezone without per-page setup. The
full behavior of the context, hook, default, and `<Time>` consumption is defined by the
`owner-timezone-context` capability spec; this section records how the provider integrates
into the shell's provider hierarchy.

#### Scenario: AppTimezoneProvider is mounted at App level

- **WHEN** the application boots
- **THEN** `AppTimezoneProvider` (from `frontend/src/components/ui/timezone-context.tsx`) is
  mounted inside `QueryClientProvider` and wrapping `RouterProvider`
- **AND** every route in the application can call `useTimezone()` without any per-page setup

#### Scenario: Provider hierarchy with timezone context

- **WHEN** the `App` component renders
- **THEN** the provider order from outermost to innermost is:
  `StrictMode` > `QueryClientProvider` > `AppTimezoneProvider` > `RouterProvider`
- **AND** `AppTimezoneProvider` is placed inside `QueryClientProvider` so the App can use
  `useGeneralSettings()` (TanStack Query) to fetch the owner's timezone from
  `GET /api/settings/general` and pass the resolved value into the provider's `timezone` prop

#### Scenario: Timezone source of truth is GET /api/settings/general

- **WHEN** the App resolves the owner's timezone
- **THEN** the source is `GET /api/settings/general` → `.timezone` (IANA name), with
  `DEFAULT_TZ` (`"Asia/Singapore"`) as the fallback until that value is available
- **AND** the existing general settings endpoint is used (no new endpoint is required)
- **AND** browser locale (`Intl.DateTimeFormat().resolvedOptions().timeZone`) is never used

#### Scenario: Full cross-cutting contract reference

- **WHEN** any dashboard page or component renders a timestamp
- **THEN** it uses `<Time>` which calls `useTimezone()` internally
- **AND** pages do NOT thread timezone as a prop to child components
- **AND** the behavior is defined by the `owner-timezone-context` capability spec

### Requirement: Root Layout Composition

The root layout SHALL be a single route wrapper that composes the shell structure. All page routes render as children of this layout via React Router's `<Outlet />`.

#### Scenario: Root layout renders all shell affordances

- **WHEN** any route within the application is visited
- **THEN** the `Shell` component renders with the `PageHeader` passed as the `header` prop
- **AND** page content renders inside an `ErrorBoundary` within the shell's main content area
- **AND** the `CommandPalette` dialog is mounted (initially closed) outside the shell
- **AND** the `ShortcutHints` floating button and dialog are mounted
- **AND** the `Toaster` (Sonner) is mounted for toast notifications
- **AND** global keyboard shortcuts are registered via `useKeyboardShortcuts`

### Requirement: Shell Layout Structure

The shell SHALL implement a responsive sidebar + main content layout that fills the full viewport height. The sidebar and main area are arranged in a horizontal flex container.

#### Scenario: Desktop layout (viewport >= md breakpoint)

- **WHEN** the viewport width is at or above the `md` Tailwind breakpoint (768px)
- **THEN** the desktop sidebar renders as a persistent `<aside>` element with a right border
- **AND** the sidebar renders expanded at 240px (`md:w-60`) by default and is collapsible to a 56px (`md:w-14`) icon rail; the collapsed state is persisted to `localStorage` under `butlers.sidebar-collapsed`
- **AND** the main content area is `flex-1` (flex sibling of the aside; no margin offset needed)
- **AND** the mobile drawer is not visible

#### Scenario: Mobile layout (viewport < md breakpoint)

- **WHEN** the viewport width is below the `md` breakpoint
- **THEN** the desktop sidebar is hidden (`hidden md:flex`)
- **AND** a hamburger button appears in the header (left side, before the page header)
- **AND** tapping the hamburger opens the sidebar as a `Sheet` (Radix dialog-based drawer) sliding in from the left at 256px width
- **AND** navigating to a route automatically closes the mobile drawer via the `onNavClick` callback

#### Scenario: Main content area structure

- **WHEN** the shell renders its main area
- **THEN** a header bar of height 56px (`h-14`) renders with horizontal padding of 24px (`px-6`) and a bottom border
- **AND** the main content area below the header fills remaining vertical space with `overflow-y-auto` and 24px padding (`p-6`)
- **AND** the header contains the `PageHeader` component alongside the mobile hamburger button (on small screens)

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
  2. **Dedicated Butlers** — Education (`/education`; butler-aware on `education`), Health (`/health/measurements`), Calendar (`/calendar`), Chronicles (`/chronicles`; butler-aware on `chronicler`)
  3. **Telemetry** — Timeline (`/timeline`), Notifications (`/notifications`), Issues (`/issues`), Sessions (`/sessions`), Audit Log (`/audit-log`), System (`/system`)
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
- **AND** no separate sidebar entries exist for `/settings/models`, `/settings/spend`, or `/settings/permissions` — these are reached via the Console panels

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

### Requirement: Full Route Map

The router SHALL define all application routes as children of the root layout. All routes SHALL share the shell, header, error boundary, and sidebar.

The route map SHALL include the Settings Console sub-routes and the ingestion dispatch console sub-routes as first-class child routes (not page-level `?tab=` state).

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
  - `/approvals` -- Approvals queue (rendered by `ApprovalsPage`)
  - `/approvals/rules` -- Approval standing rules
  - `/calendar` -- Calendar workspace
  - `/contacts` -- Contacts list
  - `/contacts/:contactId` -- Contact detail (parameterized)
  - `/groups` -- Groups list (not in sidebar; reachable via the relationship butler's CRM tab Quick Links)
  - `/costs` -- Costs and usage (not in sidebar)
  - `/memory` -- Memory system
  - `/ingestion` -- Ingestion Timeline ledger
  - `/ingestion/connectors` -- Ingestion connector roster
  - `/ingestion/connectors/:connectorType/:endpointIdentity` -- Ingestion connector detail (parameterized)
  - `/ingestion/filters` -- Ingestion Filters pipeline
  - `/settings` -- Settings Console (`SettingsConsolePage`; system-side only)
  - `/settings/models` -- Settings model catalog (`SettingsModelsPage`)
  - `/settings/spend` -- Settings spend (`SettingsSpendPage`)
  - `/settings/permissions` -- Settings permissions (`SettingsPermissionsPage`)
  - `/secrets` -- Secrets management (per-user OAuth provider setup lives here, not under `/settings`)

#### Scenario: Settings Console routes

- **WHEN** the frontend router is configured
- **THEN** the following routes are registered, each rendering within the `RootLayout`:
  - `/settings` → `SettingsConsolePage`
  - `/settings/models` → `SettingsModelsPage`
  - `/settings/spend` → `SettingsSpendPage`
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

### Requirement: Page Header with Breadcrumbs

The `PageHeader` component SHALL render inside the shell's header bar and
provide the breadcrumbs strip (populated by the active page), a command palette
trigger, and a theme toggle. `PageHeader` is shell chrome only — it does not own
page titles or generate breadcrumbs from the URL.

#### Scenario: Breadcrumbs strip

- **WHEN** the active page supplies breadcrumbs via `<Page breadcrumbs=...>`
- **THEN** `<Page>` renders the supplied breadcrumb trail using the shared
  `<Breadcrumbs>` component (`frontend/src/components/ui/breadcrumbs.tsx`)
  inside `<main>`, above the page's `<h1>`
- **AND** `<Page>` signals `BreadcrumbsControlProvider` (via `useBreadcrumbsControl`)
  so that `PageHeader` suppresses its URL-segment auto-builder
- **AND** crumbs are separated by `/` characters
- **AND** the final crumb (current page) renders as plain text without a link
- **WHEN** the active page does not supply breadcrumbs (un-migrated pages)
- **THEN** `PageHeader` renders URL-segment breadcrumbs auto-generated from
  `location.pathname` as a legacy fallback; this is not the normative path for
  pages that have adopted `<Page>`

#### Scenario: Page title ownership

- **WHEN** any page within the application renders its primary heading
- **THEN** the `<h1>` is rendered by `<Page>`, not by `PageHeader`
- **AND** the `<h1>` uses `text-3xl font-bold tracking-tight` — this is the
  canonical operator-tool H1 size for all pages as shipped in
  `frontend/src/components/ui/page.tsx`
- **AND** `PageHeader` does NOT render an `<h1>` or accept a `title` prop as
  a live contract; the `PageHeader.title` slot is removed from the normative
  interface

#### Scenario: Header action buttons

- **WHEN** the page header renders
- **THEN** a search icon button (magnifying glass) appears on the right side,
  triggering the command palette on click
- **AND** a theme toggle button appears next to the search button
- **AND** both buttons use the `ghost` variant at `sm` size with 32x32px
  dimensions

### Requirement: Command Palette (Global Search)

The command palette SHALL be a modal overlay providing cross-entity search with keyboard navigation, recent search history, and grouped results by category.

#### Scenario: Opening the command palette

- **WHEN** the user presses `Cmd+K` (macOS) or `Ctrl+K` (other platforms)
- **OR** the user presses `/` (when not in an input/textarea/contentEditable)
- **OR** the user clicks the search icon in the page header
- **THEN** the command palette dialog opens at 20% from the top of the viewport
- **AND** the search input is auto-focused
- **AND** the previous query and selection state are reset

#### Scenario: Search behavior

- **WHEN** the user types fewer than 2 characters
- **THEN** no search API call is made
- **AND** recent searches are displayed (if any exist, up to 5)
- **WHEN** the user types 2 or more characters
- **THEN** a debounced search request is sent to the backend search API
- **AND** results are grouped by category (sessions, state, contacts, etc.) with category headers
- **AND** each result shows a title, optional snippet, and a butler badge

#### Scenario: Keyboard navigation within results

- **WHEN** the command palette has results and the user presses Arrow Down
- **THEN** the selection index moves to the next result (clamped to the last result)
- **WHEN** the user presses Arrow Up
- **THEN** the selection index moves to the previous result (clamped to 0)
- **WHEN** the user presses Enter
- **THEN** the selected result's URL is navigated to, the query is saved to recent searches, and the dialog closes
- **AND** mouse hover on a result also updates the selection index

#### Scenario: Recent searches persistence

- **WHEN** a search query leads to navigation
- **THEN** the query is saved to localStorage under the `butlers:recent-searches` key
- **AND** duplicates are deduplicated (most recent first)
- **AND** the history is capped at 5 entries
- **AND** the recent search list can be cleared from the Settings page

#### Scenario: Loading, error, and empty states

- **WHEN** a search is in progress
- **THEN** skeleton placeholders render in the results area (two group headers with line items)
- **WHEN** the search API returns an error
- **THEN** the text "Search failed. Please try again." renders in destructive color
- **WHEN** the search completes with zero results
- **THEN** the text "No results found" renders in muted foreground color

#### Scenario: Footer keyboard hints

- **WHEN** results are displayed in the command palette
- **THEN** a footer bar shows keyboard hints: up/down arrows to navigate, Enter to open
- **AND** an ESC keyboard hint is shown next to the search input

### Requirement: Keyboard Shortcuts System

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
  - `g` then `c` -- Contacts (`/contacts`)
  - `g` then `h` -- Health (`/health/measurements`)
  - `g` then `e` -- Ingestion (`/ingestion`)
- **AND** the pending "g" state expires after 1 second if no second key is pressed
- **AND** shortcuts do not fire when focus is in an input, textarea, or contentEditable element

#### Scenario: Shortcut hints dialog

- **WHEN** the user clicks the floating "?" button in the bottom-right corner of the viewport
- **THEN** a dialog opens listing all available keyboard shortcuts with their key combinations
- **AND** the button has `opacity-60` by default and `opacity-100` on hover
- **AND** the button is fixed-positioned at `bottom-4 right-4` with `z-50`

### Requirement: Dark Mode and Theme System

The dashboard SHALL support three theme modes (light, dark, system) using a CSS class-based dark mode strategy with localStorage persistence.

#### Scenario: Theme initialization

- **WHEN** the application loads
- **THEN** the stored theme preference is read from `localStorage` under the key `theme`
- **AND** valid values are `light`, `dark`, and `system`; invalid or missing values default to `system`
- **AND** the `system` mode resolves to the OS preference via `prefers-color-scheme` media query

#### Scenario: Theme application

- **WHEN** the resolved theme is `dark`
- **THEN** the `dark` class is added to the `<html>` element
- **WHEN** the resolved theme is `light`
- **THEN** the `dark` class is removed from the `<html>` element
- **AND** theme changes are persisted to `localStorage` immediately

#### Scenario: System theme reactivity

- **WHEN** the theme is set to `system` and the OS preference changes
- **THEN** the resolved theme updates reactively via a `change` event listener on the `prefers-color-scheme` media query
- **AND** the UI updates without requiring a page reload

#### Scenario: Theme toggle in header

- **WHEN** the user clicks the theme toggle button in the header
- **THEN** the theme cycles: if currently `system`, toggle to the opposite of the resolved theme; if explicit `light` or `dark`, toggle to the other
- **AND** the button icon shows a sun (for switching to light) when in dark mode and a moon (for switching to dark) when in light mode

### Requirement: CSS Design Token System

The design system SHALL use CSS custom properties (design tokens) defined in `:root` and overridden in `.dark`, using the OKLCH color space for perceptual uniformity. All tokens are mapped into Tailwind's color system via a `@theme inline` block.

#### Scenario: Light mode color tokens

- **WHEN** the light theme is active
- **THEN** the following semantic tokens are defined:
  - `--background`: pure white (`oklch(1 0 0)`)
  - `--foreground`: near-black (`oklch(0.145 0 0)`)
  - `--primary` / `--primary-foreground`: dark neutral / near-white
  - `--secondary` / `--secondary-foreground`: very light neutral / dark neutral
  - `--muted` / `--muted-foreground`: light neutral background / mid-gray text
  - `--accent` / `--accent-foreground`: light neutral / dark neutral (matches secondary)
  - `--destructive`: red-orange (`oklch(0.577 0.245 27.325)`)
  - `--border` / `--input`: light gray (`oklch(0.922 0 0)`)
  - `--ring`: mid-gray for focus rings
  - Five chart colors for data visualization
  - Sidebar-specific tokens mirroring the main palette

#### Scenario: Dark mode color tokens

- **WHEN** the dark theme is active
- **THEN** background inverts to near-black (`oklch(0.145 0 0)`)
- **AND** foreground inverts to near-white (`oklch(0.985 0 0)`)
- **AND** card and popover backgrounds use a slightly lighter dark (`oklch(0.205 0 0)`)
- **AND** borders use semi-transparent white (`oklch(1 0 0 / 10%)`)
- **AND** chart colors shift to higher-chroma variants optimized for dark backgrounds
- **AND** sidebar tokens follow the dark card background

#### Scenario: Border radius tokens

- **WHEN** any component uses rounded corners
- **THEN** the base `--radius` is `0.625rem` (10px)
- **AND** derived radii (`sm`, `md`, `lg`, `xl`, `2xl`, `3xl`, `4xl`) are computed relative to the base

#### Scenario: Typography defaults

- **WHEN** the application renders text
- **THEN** the root font stack is `system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- **AND** base line height is 1.5, font weight is 400
- **AND** font smoothing is enabled (`-webkit-font-smoothing: antialiased`, `-moz-osx-font-smoothing: grayscale`)
- **AND** font synthesis is disabled for consistent rendering

### Requirement: UI Primitive Component Library

The dashboard SHALL use shadcn/ui as its component library, which generates local component files backed by Radix UI headless primitives and styled with Tailwind CSS via class-variance-authority (CVA).

#### Scenario: Button component variants

- **WHEN** a `Button` is rendered
- **THEN** the following variants are available:
  - `default`: primary background with primary foreground
  - `destructive`: red background with white text
  - `outline`: bordered with background, subtle shadow
  - `secondary`: secondary background colors
  - `ghost`: transparent background with hover accent
  - `link`: text-only with underline on hover
- **AND** the following sizes are available: `default` (h-9), `xs` (h-6), `sm` (h-8), `lg` (h-10), `icon` (size-9), `icon-xs` (size-6), `icon-sm` (size-8), `icon-lg` (size-10)
- **AND** all buttons include focus-visible ring styles and disabled opacity

#### Scenario: Badge component variants

- **WHEN** a `Badge` is rendered
- **THEN** variants include: `default`, `secondary`, `destructive`, `outline`, `ghost`, `link`
- **AND** badges render as rounded-full pill shapes with `px-2 py-0.5 text-xs font-medium`
- **AND** the `asChild` prop enables Radix Slot composition

#### Scenario: Card component structure

- **WHEN** a `Card` is rendered
- **THEN** it uses `bg-card text-card-foreground` with `rounded-xl border shadow-sm` and `py-6`
- **AND** sub-components `CardHeader`, `CardTitle`, `CardDescription`, `CardAction`, `CardContent`, and `CardFooter` compose the internal layout
- **AND** `CardHeader` uses a CSS grid layout with auto-rows and optional action slot

#### Scenario: Dialog component (modals)

- **WHEN** a `Dialog` is rendered
- **THEN** it uses Radix Dialog primitives with a backdrop overlay (`bg-black/50`)
- **AND** content centers at `top-50% left-50%` with translate transforms
- **AND** open/close animations include fade-in/fade-out and zoom-in-95/zoom-out-95
- **AND** an optional close button (X icon) renders in the top-right corner
- **AND** the `showCloseButton` prop controls its visibility (defaults to true)

#### Scenario: Sheet component (drawers)

- **WHEN** a `Sheet` is rendered
- **THEN** it uses Radix Dialog primitives configured as a slide-in panel
- **AND** the `side` prop controls slide direction: `left`, `right`, `top`, or `bottom`
- **AND** open animation duration is 500ms, close animation is 300ms
- **AND** the mobile sidebar uses the `left` side variant at `w-64`

#### Scenario: Table component

- **WHEN** a `Table` is rendered
- **THEN** it wraps in a container with `overflow-x-auto` for horizontal scrolling
- **AND** rows have hover highlight (`hover:bg-muted/50`) and border-bottom
- **AND** header cells use `font-medium` with `h-10` height

#### Scenario: Form components (Input, Select, Textarea, Checkbox, Label)

- **WHEN** form components are rendered
- **THEN** `Input` renders at `h-9` with border, focus-visible ring, and placeholder styling
- **AND** `Select` uses Radix Select primitives with animated dropdown content, check indicators, and scroll buttons
- **AND** `Textarea` uses `field-sizing-content` for auto-height with a minimum of `min-h-16`
- **AND** `Checkbox` renders as a 16x16 rounded-sm box with check indicator animation
- **AND** `Label` renders as `text-sm font-medium` with peer-disabled opacity

#### Scenario: Tabs component

- **WHEN** `Tabs` are rendered
- **THEN** two list variants are available: `default` (muted background pill) and `line` (underline indicator)
- **AND** tabs support both horizontal and vertical orientations
- **AND** active tab triggers show `bg-background` with shadow in default variant, or a bottom/side underline in line variant
- **AND** the `line` variant uses a pseudo-element (`after:`) for the active indicator

#### Scenario: Tooltip component

- **WHEN** a `Tooltip` wraps an element
- **THEN** it uses Radix Tooltip primitives with `bg-foreground text-background` (inverted colors)
- **AND** the tooltip includes a directional arrow
- **AND** the default `delayDuration` on the provider is 0ms (instant show)

#### Scenario: Dropdown menu component

- **WHEN** a `DropdownMenu` is rendered
- **THEN** it uses Radix DropdownMenu primitives with animated content (fade + zoom + slide)
- **AND** menu items support `default` and `destructive` variants
- **AND** checkbox items, radio items, sub-menus, separators, labels, and shortcut hints are all available

### Requirement: Skeleton Loading Components

The dashboard SHALL provide a library of reusable skeleton loaders that match the layout of their real-data counterparts, ensuring perceived performance during data fetching.

#### Scenario: Base skeleton primitive

- **WHEN** a `Skeleton` element renders
- **THEN** it applies `bg-accent animate-pulse rounded-md` for a pulsing placeholder effect

#### Scenario: Card skeleton

- **WHEN** a `CardSkeleton` renders
- **THEN** it shows a card with optional header placeholders (title line at `h-5 w-40`, description at `h-4 w-64`)
- **AND** a configurable number of content lines (default 3) with the last line at 75% width

#### Scenario: Table skeleton

- **WHEN** a `TableSkeleton` renders
- **THEN** it shows a table with skeleton header cells and a configurable number of rows (default 5)
- **AND** column widths and alignment are specified per-column to match the real table layout
- **AND** a pre-configured `NotificationTableSkeleton` variant matches the notification feed layout

#### Scenario: Stats skeleton

- **WHEN** a `StatsSkeleton` renders
- **THEN** it shows a responsive grid of stat cards (2 columns on mobile, 4 on desktop)
- **AND** each card has a title placeholder, a circular icon placeholder, and a value placeholder

#### Scenario: Chart skeleton

- **WHEN** a `ChartSkeleton` renders
- **THEN** it shows a card with title and description placeholders and a large rectangular area (default `h-64`)

### Requirement: Error Boundary

A React class-based error boundary SHALL wrap all route content to catch and recover from rendering errors without crashing the entire application.

#### Scenario: Error is caught during render

- **WHEN** a child component throws an error during rendering
- **THEN** the error boundary catches it via `getDerivedStateFromError`
- **AND** the error is logged to `console.error` with component stack info
- **AND** a fallback UI renders: centered layout with min-height 400px, a "Something went wrong" heading in destructive color, the error message (or "An unexpected error occurred"), and a "Try again" outline button
- **WHEN** the user clicks "Try again"
- **THEN** the error state resets and the child content attempts to re-render

### Requirement: Empty State Pattern

A reusable `EmptyState` component SHALL provide consistent empty-data messaging across all pages.

#### Scenario: Empty state renders

- **WHEN** a page or section has no data to display
- **THEN** the `EmptyState` component renders centered content with 64px vertical padding
- **AND** an optional icon renders at `text-4xl` in `text-muted-foreground`
- **AND** the title renders as `text-lg font-semibold`
- **AND** the description renders as `text-sm text-muted-foreground` with a max width of `max-w-sm`
- **AND** an optional action slot renders below the description with 16px top margin

### Requirement: Toast Notification System

The dashboard SHALL use Sonner for toast notifications, providing feedback for mutations, errors, and informational messages.

#### Scenario: Toast rendering

- **WHEN** a toast is triggered (via `toast()`, `toast.success()`, `toast.error()`, etc.)
- **THEN** the Sonner toaster renders the notification using the current theme
- **AND** custom icons are used: `CircleCheckIcon` for success, `InfoIcon` for info, `TriangleAlertIcon` for warning, `OctagonXIcon` for error, `Loader2Icon` (spinning) for loading
- **AND** toast styling uses CSS variables mapped to the design token system (`--popover`, `--popover-foreground`, `--border`, `--radius`)

### Requirement: Auto-Refresh Architecture

Pages with live data SHALL provide a user-controllable auto-refresh mechanism with configurable intervals, pause/resume, and localStorage persistence.

#### Scenario: Auto-refresh default state

- **WHEN** a page using `useAutoRefresh` mounts for the first time
- **THEN** auto-refresh is enabled by default (reading from `localStorage` key `butlers:auto-refresh:enabled`, falling back to `true`)
- **AND** the default interval is 10 seconds (reading from `localStorage` key `butlers:auto-refresh:interval`, falling back to 10,000ms)
- **AND** the hook returns a `refetchInterval` value compatible with TanStack Query (the interval number when enabled, `false` when disabled)

#### Scenario: Interval options

- **WHEN** the user changes the refresh interval
- **THEN** the available options are: 5s (5,000ms), 10s (10,000ms), 30s (30,000ms), 60s (60,000ms)
- **AND** invalid interval values are silently rejected
- **AND** the selected interval is persisted to `localStorage`

#### Scenario: Auto-refresh toggle UI

- **WHEN** the `AutoRefreshToggle` component renders
- **THEN** it shows a "Live" badge (emerald green) when enabled
- **AND** an interval selector dropdown is shown (disabled when auto-refresh is paused)
- **AND** a Pause/Resume button toggles the enabled state
- **AND** the component uses `Button` size `sm` at height `h-8` with `text-xs`

#### Scenario: Settings page control

- **WHEN** the user visits `/settings`
- **THEN** the "Live Refresh Defaults" card shows the `AutoRefreshToggle` component
- **AND** changes to enabled state and interval are persisted and apply as defaults to all pages using `useAutoRefresh`

### Requirement: Settings Page

The settings page SHALL provide local-only (browser-scoped) preferences for the dashboard operator.

#### Scenario: Appearance settings

- **WHEN** the user visits `/settings`
- **THEN** the Appearance card shows a theme selector with options: System, Light, Dark
- **AND** the currently resolved theme is displayed as text below the selector
- **AND** theme changes take immediate effect

#### Scenario: Command palette settings

- **WHEN** the user visits `/settings`
- **THEN** the Command Palette card shows the count of saved recent searches
- **AND** a "Clear recent searches" button removes all saved searches from `localStorage`
- **AND** the button is disabled when the count is 0

### Requirement: Utility Infrastructure

Shared utilities SHALL underpin component styling and settings persistence.

#### Scenario: Class name merging

- **WHEN** the `cn()` utility function is called with class value arguments
- **THEN** it composes classes via `clsx` and merges Tailwind conflicts via `tailwind-merge`
- **AND** this ensures that component prop-based class overrides correctly take precedence over base styles

#### Scenario: Command palette event bridge

- **WHEN** `dispatchOpenCommandPalette()` is called from any location (header button, keyboard shortcut)
- **THEN** a `CustomEvent` named `open-search` is dispatched on the `window` object
- **AND** the `CommandPalette` component listens for this event and opens the dialog

#### Scenario: Local settings resilience

- **WHEN** `localStorage` read or write operations fail (e.g., in private browsing or quota exceeded)
- **THEN** all settings functions silently catch errors and return fallback values
- **AND** the application continues to function with default settings

## Source References

- Routes contract (settings refactor PLAN.md §4; prototype graduated).
- `about/heart-and-soul/design-language.md` — Sidebar/composition: 56px icon rail, one elevation, no nested nav.
- `about/heart-and-soul/v1.md` — Per-user OAuth (Google, Spotify, Telegram, Steam, etc.) is explicitly out of v1 system-settings scope; OAuth setup remains on `/secrets` to keep `/settings` system-side only.
- `about/heart-and-soul/vision.md` Non-Negotiable Rule 1 (composure) and Rule 6 (governing-document-driven scope).
- Ingestion dispatch console route ownership: `dashboard-ingestion-dispatch-console` capability spec (first-class ingestion child routes; legacy `?tab=` state is compatibility only).

