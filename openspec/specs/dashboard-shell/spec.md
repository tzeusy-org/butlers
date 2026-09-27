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

The application shell defines the outermost structural frame: the sidebar navigation, page header with breadcrumbs, command palette, keyboard shortcuts, theme system, loading/error/empty state patterns, the dashboard refresh rule, the route registry, and the chat dock slot. All domain pages render inside this shell and inherit its responsive behavior and operational affordances; tokens, primitives, and page archetypes belong to `dashboard-design-language`.

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
- **AND** the default `refetchIntervalInBackground` is false so inactive browser tabs do not poll
- **AND** intentional hidden-tab polling overrides that default explicitly via the named `POLL_IN_BACKGROUND` policy token
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
- **AND** the `EntityFinder` dialog is mounted (initially closed) outside the shell
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

### Requirement: Chat Dock Rail (>= xl breakpoint)
The shell SHALL expose an optional `chatDock` slot that renders as a sibling column of `<main>`, never an overlay, at or above the `xl` breakpoint while the dock is open. The chat surface placed in the slot, its postures, and its turn behavior are owned by `dashboard-chat-ui` (Requirement: Global Chat Postures).

#### Scenario: Docked rail at >= xl
- **WHEN** the viewport is at or above the `xl` breakpoint and the dock is open
- **THEN** the shell renders the `chatDock` slot inside an `<aside>` landmark that is a flex sibling of `<main>`
- **AND** the rail is separated from `<main>` by a hairline rule with no shadow

#### Scenario: Popover fallback below xl or while collapsed
- **WHEN** the viewport is below the `xl` breakpoint, or the dock is collapsed
- **THEN** the shell renders no `chatDock` landmark
- **AND** the chat surface falls back to its popover posture

#### Scenario: Dock open/collapsed state persists
- **WHEN** the operator collapses or reopens the dock
- **THEN** the choice is persisted per viewer and survives a reload

#### Scenario: Dock width persists
- **WHEN** the operator resizes the dock with its splitter (pointer drag or arrow keys)
- **THEN** the dock is resizable within bounds and its width is persisted per viewer and restored on the next mount

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

### Requirement: Skeleton Loading Components
The dashboard SHALL render loading states as skeleton placeholders that approximate the layout of their real-data counterparts, and loading and empty states SHALL be mutually exclusive: skeletons while a read is loading, the empty state only after it completes with zero results.

Page-level loading is owned by the `<Page>` primitive, which renders an archetype-matched skeleton (see `dashboard-design-language`, Requirement: Page Primitive and Archetypes); block-level loading inside a rendered page uses the shared skeleton library below.

#### Scenario: Base skeleton primitive
- **WHEN** a `Skeleton` element renders
- **THEN** it is a neutral token-colored placeholder block with no decorative motion

#### Scenario: Card skeleton
- **WHEN** a `CardSkeleton` renders
- **THEN** it shows optional header placeholders and a configurable number of content lines, the last line shorter than the rest

#### Scenario: Table skeleton
- **WHEN** a `TableSkeleton` renders
- **THEN** it shows header cells and a configurable number of rows whose columns match the real table's column count and alignment
- **AND** a pre-configured `NotificationTableSkeleton` variant matches the notification feed layout

#### Scenario: Stats skeleton
- **WHEN** a `StatsSkeleton` renders
- **THEN** it shows a responsive grid of stat-cell placeholders matching the real stats grid

#### Scenario: Chart skeleton
- **WHEN** a `ChartSkeleton` renders
- **THEN** it shows title and description placeholders and a chart-area placeholder of configurable height

#### Scenario: Loading never shows the empty state
- **WHEN** a read that will return zero results is still loading
- **THEN** the surface renders skeletons and does not render its empty state

### Requirement: Error Boundary
A React error boundary SHALL wrap all route content so a rendering error is contained to the page instead of crashing the application, and a failed read SHALL render as an explicit error state, never as an empty or zero result.

A read failure inside a rendered page uses the shared `ErrorState` / `QueryBoundary` primitives (state priority: loading, then error, then empty, then content); a page-level failure uses the `<Page>` primitive's error state. An error state is announced to assistive technology (`role="alert"`), names what failed, includes the underlying message when available, and offers a retry when the read can be retried. Where cached data for the same read is still available, it may stay visible alongside a warning that it is stale.

#### Scenario: Error is caught during render
- **WHEN** a child component throws an error during rendering
- **THEN** the error boundary catches it and logs it with component stack information
- **AND** a fallback renders a "Something went wrong" heading in the destructive state color, the error message (or "An unexpected error occurred"), and a "Try again" action
- **WHEN** the user activates "Try again"
- **THEN** the error state resets and the child content attempts to re-render

#### Scenario: A failed read never renders as empty
- **WHEN** a read fails for a list, table, panel, or page
- **THEN** the surface renders its error state rather than its empty state
- **AND** it does not render a zero, a `$0.00`, or an all-clear in place of the unknown value

### Requirement: Empty State Pattern
A reusable `EmptyState` component SHALL provide consistent empty-data messaging across all pages, rendered only after a read completes successfully with zero results.

The component has two tiers, defined by `dashboard-design-language` (Requirement: Interface Copy): a page-level tier (title plus at most one short sentence of context) and a Voice-surface-inline tier (one serif-italic sentence). Neither tier renders an illustration or icon.

#### Scenario: Empty state renders
- **WHEN** a page or section has no data to display after a successful read
- **THEN** the `EmptyState` component renders a centered title and, in the page tier, one short muted sentence that states the fact and, where applicable, names where the data comes from or the action that creates it
- **AND** an optional action renders below the text
- **AND** no icon or illustration renders, even when one is passed

### Requirement: Toast Notification System

The dashboard SHALL use Sonner for toast notifications, providing feedback for mutations, errors, and informational messages.

#### Scenario: Toast rendering

- **WHEN** a toast is triggered (via `toast()`, `toast.success()`, `toast.error()`, etc.)
- **THEN** the Sonner toaster renders the notification using the current theme
- **AND** custom icons are used: `CircleCheckIcon` for success, `InfoIcon` for info, `TriangleAlertIcon` for warning, `OctagonXIcon` for error, `Loader2Icon` (spinning) for loading
- **AND** toast styling uses CSS variables mapped to the design token system (`--popover`, `--popover-foreground`, `--border`, `--radius`)

### Requirement: Bus-Aware Poll Architecture
Dashboard reads SHALL refresh on fleet event-bus invalidation, with a bounded fallback poll, and SHALL pause polling while the browser tab is hidden. This requirement is the single home for the dashboard refresh rule; no user-facing refresh toggle exists.

A read whose cache key is bus-covered (`frontend/src/lib/event-cache-registry.ts`) is refreshed primarily by bus invalidation; its poll is a reconciliation safety net whose cadence follows the bus's connection health (`useBusAwarePollInterval`). A read with no matching bus event polls on its own named interval. Every poll interval is a named policy token (`frontend/src/lib/poll-policy.ts`), never a bare literal.

#### Scenario: Bus-aware polling cadence
- **WHEN** a bus-covered query uses `useBusAwarePollInterval`
- **THEN** it polls at the slow reconciliation cadence (`POLL_BUS_RECONCILE_MS`) while the shared `EventBusProvider` freshness health is `"healthy"`
- **AND** it polls at the fast fallback cadence (`POLL_BUS_DOWN_FALLBACK_MS`) while freshness health is `"late"` or `"down"`, so a dropped socket degrades to honest polling rather than silent staleness
- **AND** a parseable but malformed fleet frame never establishes or extends `"healthy"` freshness; only a valid event or snapshot envelope may do so
- **AND** no user-facing toggle pauses or overrides this cadence

#### Scenario: Hidden tab pauses polling
- **WHEN** the browser tab is hidden
- **THEN** interval refetches pause, per the shared query-client default
- **AND** a query that must keep polling while hidden opts in explicitly through the named `POLL_IN_BACKGROUND` token with a documented reason

### Requirement: Utility Infrastructure

Shared utilities SHALL underpin component styling and settings persistence.

#### Scenario: Class name merging

- **WHEN** the `cn()` utility function is called with class value arguments
- **THEN** it composes classes via `clsx` and merges Tailwind conflicts via `tailwind-merge`
- **AND** this ensures that component prop-based class overrides correctly take precedence over base styles

#### Scenario: Entity finder event bridge

- **WHEN** `dispatchOpenEntityFinder()` is called from any location (header button, keyboard shortcut)
- **THEN** a `CustomEvent` named `open-entity-finder` is dispatched on the `window` object
- **AND** the `EntityFinder` component listens for this event and opens the dialog

#### Scenario: Local settings resilience

- **WHEN** `localStorage` read or write operations fail (e.g., in private browsing or quota exceeded)
- **THEN** all settings functions silently catch errors and return fallback values
- **AND** the application continues to function with default settings

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

### Requirement: Route Registry and Compatibility Redirects
Every page route SHALL be registered in the shell capability manifest (the route registry, `frontend/src/lib/shell-capability.ts`), and every other registered path SHALL be a compatibility redirect to a canonical route. All routes render as children of the root layout and share the shell, header, error boundary, and sidebar.

The router derives each page's lazy boundary from the registry, so a page path absent from the registry fails at startup; navigation, the command finder, shortcut help, and prefetch are projections of the same registry. Compatibility redirects are replace-navigations that preserve bookmarks and carry no page of their own:

| Legacy path | Canonical destination |
|---|---|
| `/contacts`, `/contacts/:contactId` | `/entities/index?has=contact` |
| `/costs`, `/settings/spend` | `/spend` |
| `/groups` | `/entities/circles` |
| `/entities/hop`, `/entities/columns`, `/entities/social-map` | `/entities` (the plex), carrying any focus parameters |
| `/qa/investigations` | `/qa` |
| `/ingestion?tab=connectors`, `/ingestion?tab=filters` | `/ingestion/connectors`, `/ingestion/filters` |
| `/ingestion?tab=history`, `/ingestion?tab=timeline`, `/ingestion/history` | `/ingestion` |
| `/connectors`, `/connectors/:connectorType/:endpointIdentity` | the matching `/ingestion/connectors` route |
| `/butlers/relationship/entities/:entityId` | `/entities/:entityId` |
| `/butlers/relationship/contacts/:id` | the contacts compatibility redirect above |

#### Scenario: Every page route is in the registry
- **WHEN** the router is initialized
- **THEN** every route that renders a page resolves its page loader from the route registry
- **AND** any registered path that is not in the registry is a compatibility redirect listed above

#### Scenario: Compatibility routes redirect
- **WHEN** a legacy path from the compatibility table is visited
- **THEN** the router replaces it with its canonical destination
- **AND** no legacy path renders a page of its own

#### Scenario: Settings stays system-side
- **WHEN** the router is configured
- **THEN** `/settings` and its sub-routes (`/settings/models`, `/settings/permissions`) carry only system-side configuration
- **AND** `/secrets` renders the credential passport, which is where per-user OAuth and provider credentials are set up

#### Scenario: Ingestion sub-routes are first-class
- **WHEN** the owner opens `/ingestion/connectors` or `/ingestion/filters`
- **THEN** the route renders inside the root dashboard shell with the sidebar and page header present
- **AND** the ingestion surface does not rely on page-level `?tab=` state as its route map

#### Scenario: Ingestion connector detail is route-addressable
- **WHEN** the owner opens `/ingestion/connectors/:connectorType/:endpointIdentity`
- **THEN** the router loads the connector detail route directly
- **AND** refresh or deep-link navigation preserves the selected connector

## Source References

- Route registry: `frontend/src/lib/shell-capability.ts` (router, navigation, finder, and shortcut projections).
- `about/heart-and-soul/design-language.md` — Sidebar/composition: icon rail, one elevation, no nested nav.
- `about/heart-and-soul/v1.md` — Per-user OAuth (Google, Spotify, Telegram, Steam, etc.) is explicitly out of v1 system-settings scope; OAuth setup remains on `/secrets` to keep `/settings` system-side only.
- `about/heart-and-soul/vision.md` Non-Negotiable Rule 1 (composure) and Rule 6 (governing-document-driven scope).
- Ingestion dispatch console route ownership: `dashboard-ingestion-dispatch-console` capability spec (first-class ingestion child routes; legacy `?tab=` state is compatibility only).
