## MODIFIED Requirements

### Requirement: Health data hooks with auto-refresh

Deterministic health reads MUST auto-refresh on a 30-second cadence: observed measurement
vocabulary, measurement, medication, dose, adherence, condition, symptom, meal, research, and
nutrition-summary lists, plus KPI/latest and trend reads. Slower-changing sleep, measurement-source,
and expected-signal reads MAY refresh on a 60-second cadence. Dose and adherence reads MUST fetch
only once a medication is selected.

**Auto-refresh carve-out:** the LLM Voice briefing (`GET /api/health/briefing`) and the insight
feed (`GET /api/switchboard/insights?butler=health`) MUST be excluded from auto-refresh. They rely
on the briefing's per-owner 5-minute TTL cache and a manual refresh from the BriefingStatus pill.
This is a permanent cost guard: an auto-refreshing LLM endpoint would multiply spawn cost.

#### Scenario: Deterministic hooks auto-refresh every 30s

- **WHEN** a deterministic health list, KPI, or trend read is mounted
- **THEN** it MUST refresh every 30 seconds without owner action

#### Scenario: LLM briefing and insight feed are excluded from auto-refresh

- **WHEN** the Health Overview Voice briefing or insight feed is mounted
- **THEN** it MUST NOT poll on any interval
- **AND** a fresh briefing MUST be obtained only on manual refresh or after the 5-minute TTL elapses

### Requirement: Entity browser for general butler data

The dashboard SHALL render an Entity Browser that displays structured JSONB entities from the
General butler in a searchable, filterable table with an expandable JSON viewer.

The table MUST display Collection, Tags, Data, and Created columns. The Data cell MUST show a
truncated one-line JSON preview; activating a row MUST toggle it between that preview and the full
recursive JSON viewer. Filters MUST include text search, a collection selector with an "All
collections" option, a tag selector with an "All tags" option, and a "Clear filters" action.

#### Scenario: Entity row expansion shows full JSON

- **WHEN** the user clicks an entity row with data `{"blood_type": "A+", "height_cm": 175, "notes": "..."}`
- **THEN** the Data cell MUST expand to show the full recursive JSON viewer with type-distinguished
  values
- **AND** clicking the same row again MUST collapse back to the truncated preview

### Requirement: Recursive JSON viewer component

The dashboard SHALL provide a reusable JSON viewer that renders any JSON value as a collapsible tree
and supports copy-to-clipboard.

The viewer MUST:
- Render objects and arrays recursively with collapsible nodes and depth indentation.
- Visually distinguish keys, strings, numbers, booleans, and null, using colors from
  `dashboard-design-language`.
- Offer a root-level "Copy JSON" action that copies the pretty-printed JSON (2-space indent).
- Support starting nested nodes collapsed.

#### Scenario: Copy to clipboard

- **WHEN** the user clicks "Copy JSON" on a viewer displaying `{"name": "Alice"}`
- **THEN** the clipboard MUST contain `{\n  "name": "Alice"\n}`
- **AND** the action MUST briefly confirm the copy before returning to its resting label

### Requirement: Memory hooks

Memory reads MUST refresh without owner action: stats, episode, fact, and rule lists every 30
seconds, and the recent-activity and recent-writes rails every 15 seconds. Slower-changing
aggregate reads MAY refresh less often. Single-record reads (fact, rule, episode, entity) MUST NOT
fetch until their identifier is known and MUST NOT poll.

#### Scenario: Fact detail hook waits for an ID

- **GIVEN** no fact ID is available
- **WHEN** `useFact` renders
- **THEN** its query MUST remain disabled

### Requirement: Cross-butler global search

The dashboard MUST provide a debounced global search that queries across all butlers, following
the debounce and minimum-length rules in `dashboard-api` Requirement: Dashboard Query Defaults and
Refresh. Results MUST be grouped by category (sessions, state, and dynamic butler-specific
categories); each result carries `id`, `butler`, `type`, `title`, and `snippet`.

#### Scenario: Short query suppressed

- **WHEN** the user types a single character "a"
- **THEN** the search query MUST NOT fire (enabled = false)
- **WHEN** the user types "ab"
- **THEN** the search query MUST fire after a 300ms debounce

#### Scenario: Search spans multiple butlers

- **WHEN** the user searches "headache"
- **THEN** the results MAY include: health butler symptom records, relationship butler interaction notes mentioning headache, and memory facts containing "headache"
- **AND** each result MUST include the originating `butler` name

### Requirement: Consistent loading and empty states

All domain pages MUST use the shared shell patterns in `dashboard-shell` (Requirement: Skeleton
Loading Components; Requirement: Empty State Pattern; Requirement: Error Boundary). Empty-state copy
MUST explain where the data comes from (e.g., "Health conditions will appear here as they are
tracked by the Health butler."). Loading and empty states MUST be mutually exclusive: skeletons
while loading, and the empty state only after loading completes with zero results.

#### Scenario: Loading does not show the empty state

- **GIVEN** a domain page is loading an empty result set
- **WHEN** it renders its loading state
- **THEN** it MUST render skeletons and MUST NOT render `EmptyState`

## REMOVED Requirements

### Requirement: Groups page

**Reason**: No Groups page exists; `/groups` redirects to the entity circles view.

**Migration**: See `dashboard-shell` Requirement: Route Registry and Compatibility Redirects and `dashboard-relationship` for the circles view.

### Requirement: Contact hooks with conditional fetching

**Reason**: A hook inventory is implementation detail, and the backend-dead contact readers are tracked as a defect rather than as a spec contract.

**Migration**: None; conditional enablement is specified in `dashboard-api` Requirement: Dashboard Query Defaults and Refresh.

### Requirement: Consistent pagination pattern

**Reason**: Pagination has one home.

**Migration**: See `dashboard-visibility` Requirement: Pagination Consistency, which now covers domain pages and the filter-change reset.

### Requirement: Auto-refresh intervals by domain

**Reason**: The per-domain interval table duplicated the health, memory, and shell refresh contracts and had drifted for calendar and spend.

**Migration**: See Requirement: Health data hooks with auto-refresh, Requirement: Memory hooks, and `dashboard-shell` Requirement: Bus-Aware Poll Architecture.

### Requirement: Contacts index compatibility route

**Reason**: Legacy redirects are owned by the shell route map.

**Migration**: See `dashboard-shell` Requirement: Route Registry and Compatibility Redirects.

### Requirement: Contact detail compatibility route

**Reason**: Legacy redirects are owned by the shell route map.

**Migration**: See `dashboard-shell` Requirement: Route Registry and Compatibility Redirects.

### Requirement: Costs compatibility route

**Reason**: Legacy redirects are owned by the shell route map.

**Migration**: See `dashboard-shell` Requirement: Route Registry and Compatibility Redirects.

### Requirement: Spend hooks with bus-aware refresh

**Reason**: Spend refresh is owned by the Spend capability.

**Migration**: See `dashboard-spend-dashboard` Requirement: Spend Live Stream.

### Requirement: Spend widget for dashboard overview

**Reason**: The widget belongs to the Overview page.

**Migration**: See `dashboard-overview` Requirement: Spend widget for dashboard overview.

### Requirement: Top sessions table

**Reason**: The table belongs to the Overview page.

**Migration**: See `dashboard-overview` Requirement: Top sessions table.
