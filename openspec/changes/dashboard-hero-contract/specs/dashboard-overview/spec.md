# Dashboard Overview Page

## Purpose

The dashboard home page (`/`) is the owner's "is the system doing its job?" surface.
Its single job is to answer that question at a glance, every time the owner opens the
tab. The primary visualization is butler sessions over time -- the clearest signal that
butlers are actively working on the owner's behalf. Supporting context (health status,
cost, pending approvals) remains visible but is visually demoted below the session chart.

This spec defines what the home page renders and how the information hierarchy is ordered.
The shell, routing, and navigation contracts are owned by `dashboard-shell`. The data
endpoint contracts are owned by `dashboard-api`. This spec owns the page composition.

## ADDED Requirements

### Requirement: Home Page Information Hierarchy

The home page at `/` SHALL render five regions in order, top to bottom:

1. **Primary region**: sessions over time visualization (butler-colored stripe chart).
2. **Secondary region**: recent moments feed (latest meaningful butler actions).
3. **Secondary card grid**: operational alerts — failed notifications and active issues
   in a two-column responsive grid (`lg:grid-cols-2`).
4. **QA widget**: approval metrics and active QA investigations, rendered as a standalone
   `<QaWidget />` card below the secondary grid.
5. **Supporting strip**: demoted stat context (health, cost, pending approvals).

No region SHALL visually outrank the primary region. The supporting strip SHALL NOT
use the same visual weight (card wrapper, large type) as the current four-stat grid.

#### Scenario: Primary region gets above-the-fold position

- **WHEN** the home page renders at a standard desktop viewport (1280px wide, 800px tall)
- **THEN** the session stripe chart SHALL be visible without scrolling
- **AND** the supporting stat strip SHALL NOT occupy equal vertical real estate as the
  chart
- **AND** no other full-width card or graph SHALL precede the session stripe chart in
  document order

#### Scenario: Topology graph is not the dominant element

- **WHEN** the home page renders
- **THEN** the topology graph SHALL NOT occupy the primary region
- **AND** if the topology graph is present on the page, it SHALL render at reduced size
  or as a secondary card below the session chart and recent moments feed

### Requirement: Session Stripe Chart

The home page SHALL render a sessions-over-time chart as the primary visualization.
The chart shows how many butler sessions occurred in each time bucket over the past
24 hours, broken down by butler, using butler-colored stripes.

#### Scenario: Chart renders sessions grouped by butler and time bucket

- **WHEN** the session stripe chart renders with session data
- **THEN** it SHALL display time on the x-axis (past 24 hours, divided into equal
  buckets -- default 1-hour buckets)
- **AND** session count on the y-axis
- **AND** each butler's contribution SHALL be a distinct visual stripe (stacked bar
  or stacked area), with a color derived deterministically from the butler's name
  using the design token system (`--category-1` through `--category-8` mod 8, or
  the equivalent chart token palette)
- **AND** a legend SHALL identify each butler stripe by name

#### Scenario: Chart handles empty state

- **WHEN** no sessions exist in the past 24 hours
- **THEN** the chart area SHALL render an explicit empty state: "No sessions in the
  past 24 hours"
- **AND** the empty state SHALL NOT display a chart with a zero-height bar

#### Scenario: Chart handles loading state

- **WHEN** session data is being fetched
- **THEN** a skeleton placeholder matching the chart's height SHALL render in place
  of the chart
- **AND** the skeleton SHALL use the standard `ChartSkeleton` component from the
  skeleton library

#### Scenario: Chart data source is the existing sessions API

- **WHEN** the chart fetches its data
- **THEN** it SHALL query `GET /api/sessions` with `since` set to 24 hours ago and
  a `limit` sufficient to cover the expected daily session volume (minimum 500)
- **AND** time bucketing SHALL be performed client-side on the returned session records
  using each record's `started_at` timestamp
- **AND** no new backend endpoint SHALL be required for this requirement

#### Scenario: Chart auto-refreshes for the current day

- **WHEN** the chart is rendered on any day
- **THEN** it SHALL auto-refresh at a 60-second interval so new sessions appear
  without a manual page reload
- **AND** the refresh SHALL use the existing `useAutoRefresh` hook pattern

### Requirement: Recent Moments Feed

The home page SHALL render a compact feed of recent meaningful butler actions below
the session stripe chart. The feed answers: "What did my system actually do?"

#### Scenario: Feed renders the most recent sessions as action lines

- **WHEN** the recent moments feed renders with session data
- **THEN** it SHALL display the 5 to 10 most recent completed sessions, each as a
  single line item
- **AND** each line item SHALL include: relative time (e.g., "3 minutes ago"), the
  butler name or a butler glyph, and a one-line summary derived from the session's
  stored trigger source or prompt
- **AND** each line item MAY include a link to the session detail page

#### Scenario: Feed handles empty state

- **WHEN** no completed sessions exist
- **THEN** the feed SHALL render an explicit empty state message
- **AND** it SHALL NOT render an empty list container

#### Scenario: Feed data source is the existing sessions API

- **WHEN** the feed fetches data
- **THEN** it SHALL use `GET /api/sessions` with a small `limit` (10) and no
  additional time filter, returning the most recent sessions
- **AND** it MAY reuse a warm TanStack Query cache already populated by the
  stripe chart query if the query keys overlap

### Requirement: Secondary Card Grid

Below the recent moments feed, the home page SHALL render a two-column secondary
card grid (`grid gap-6 lg:grid-cols-2`) containing operational alert cards that
provide below-the-fold context for system health monitoring.

#### Scenario: Failed Notifications card renders in the left column

- **WHEN** the secondary card grid renders
- **THEN** the left column SHALL render a "Failed Notifications" card showing
  recent notification delivery failures across all butlers
- **AND** if failed notifications exist, the card header SHALL display a destructive
  badge with the failure count
- **AND** if no failures exist, the card body SHALL render a success empty state:
  "No failed notifications. All systems healthy."

#### Scenario: Issues panel renders in the right column

- **WHEN** the secondary card grid renders
- **THEN** the right column SHALL render an `<IssuesPanel>` card showing active
  butler issues

### Requirement: QA Widget

Below the secondary card grid, the home page SHALL render a standalone `<QaWidget />`
card showing approval metrics and active QA investigations. This widget was added as
part of bu-yo4bt.9 (PR #1380) and is region 4 in the page's document order.

#### Scenario: QA widget renders below secondary cards

- **WHEN** the home page renders
- **THEN** the `<QaWidget />` SHALL appear after the secondary card grid
  (`Failed Notifications` + `IssuesPanel`) and before the supporting stat strip
- **AND** the widget SHALL render as a standalone full-width card (not inside the
  `lg:grid-cols-2` grid)

### Requirement: Supporting Stat Strip

The home page SHALL retain the four cross-system context metrics (butler health,
sessions today, estimated cost today, and pending approvals count) as a demoted
supporting strip. The strip SHALL NOT dominate the layout.

#### Scenario: Stat strip uses lower visual weight than the primary region

- **WHEN** the supporting stat strip renders
- **THEN** it SHALL NOT use `Card` wrappers for each metric
- **AND** metric values SHALL use `text-sm font-medium tabular-nums` (not `text-2xl`)
- **AND** metric labels SHALL use `text-xs text-muted-foreground`
- **AND** the strip SHALL render as a single horizontal row (`flex flex-wrap`) with
  a `border-t border-border pt-3` visual separator above it
- **AND** the strip's visual weight SHALL be clearly subordinate to the session
  stripe chart above it

#### Scenario: Stat strip retains all four metrics

- **WHEN** the supporting stat strip renders
- **THEN** it SHALL display: (1) butler health ratio (healthy / total), (2) total
  sessions today, (3) estimated cost today, (4) pending approvals count
- **AND** all four metrics SHALL remain on the page even after Vertical D lands;
  they are not removed, only demoted

### Requirement: Page Archetype Compliance

The home page SHALL adopt the Overview/Dashboard archetype as defined in
`about/lay-and-land/frontend.md` (archetype A). The shared `<Page>` primitive
(`components/ui/page.tsx`) was shipped as part of Vertical A (bu-vj0h3) and
`DashboardPage` was migrated to use it in bu-2okpr.6 (PR #1363). The primitive
is no longer future-tense; it is the current implementation contract.

#### Scenario: Page renders inside the standard shell

- **WHEN** a user navigates to `/`
- **THEN** the home page SHALL render inside the standard dashboard shell
  (sidebar, header bar, error boundary) as defined by `dashboard-shell`
- **AND** the page content SHALL not reimplement chrome that belongs to the shell

#### Scenario: Page uses the shared Page primitive

- **WHEN** `DashboardPage` renders
- **THEN** it SHALL use `<Page archetype="overview" title="Overview">` as its
  outermost container
- **AND** the five content regions SHALL be direct children of `<Page>`, not
  wrapped in a raw `<div className="space-y-6">`

## Source References

- `about/heart-and-soul/design-language.md` Settled Direction #4: "Hero metric: butler
  sessions. The single number that tells the owner whether their system is doing its job
  today is sessions... session count is the one that gets visual primacy."
- `about/heart-and-soul/design-language.md` Settled Direction #2: "Chronicles is the
  reference implementation. Every page should eventually deliver Chronicles-grade feature
  richness -- a real primary visualization, scrubber/control affordances where time applies,
  secondary aggregations, drill-down drawers."
- `about/lay-and-land/frontend.md` Page Archetypes, Archetype A (Overview/Dashboard):
  "Stats bar + primary visualization + secondary cards."
- Non-Negotiable Rule 1 (from vision.md): the dashboard exists so the owner can trust that
  butlers are alive and behaving. Sessions-as-hero makes that trust signal immediate.
