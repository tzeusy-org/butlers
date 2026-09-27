## ADDED Requirements

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

## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: CSS Design Token System
**Reason**: Token definitions are design-language content; keeping a second, outdated token table in the shell (shadcn-era values and a system-ui font stack the code no longer uses) contradicts the design-language spec.
**Migration**: Tokens, their theme overrides, and the typography defaults are specified in `dashboard-design-language` (Requirements: Surface Palette, State Color Discipline, Type System, and Token Source and Theme Mapping); `frontend/src/index.css` is normative for values.

### Requirement: UI Primitive Component Library
**Reason**: Primitive component variants are design-language content, and the shell's per-component Tailwind class inventory restated source code.
**Migration**: The primitive library contract lives in `dashboard-design-language` (Requirement: Primitive Component Library) alongside the Dispatch primitives (List Primitive, KPI Strip, Button Forms, Kind Tags, Status Indicators).

### Requirement: Settings Console Page
**Reason**: `dashboard-settings-console` is the single home for the `/settings` console; the shell copy had drifted (a truncation footer linking to `/audit-log` and a dedicated settings socket, neither of which is live).
**Migration**: See `dashboard-settings-console`, Requirements: Settings Console Layout and Settings Console Deltas On The Unified Fleet Event Bus.

### Requirement: Canonical Route Map
**Reason**: An enumerated route list restates the route registry and drifted from it (it listed removed routes and omitted live ones).
**Migration**: Replaced by Requirement: Route Registry and Compatibility Redirects, which states the registry invariant and the compatibility-redirect table; the route list itself is `frontend/src/lib/shell-capability.ts`.
