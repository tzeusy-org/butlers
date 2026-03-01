## ADDED Requirements

### Requirement: Education page route and sidebar entry

The dashboard SHALL register a route at `/education` rendering an `EducationPage` component. The sidebar SHALL display an "Education" navigation item guarded by `butler: 'education'` — the item SHALL only appear when the education butler is present in the roster.

#### Scenario: Education tab visible when butler is in roster

- **WHEN** the dashboard loads
- **AND** the education butler is present in the roster
- **THEN** the sidebar SHALL display an "Education" navigation item
- **AND** clicking it SHALL navigate to `/education`

#### Scenario: Education tab hidden when butler is absent

- **WHEN** the dashboard loads
- **AND** the education butler is not in the roster
- **THEN** the sidebar SHALL NOT display an "Education" navigation item

---

### Requirement: Education page layout with tab panels

The education page SHALL display a page header with title "Education" and a description line. Below the header, the page SHALL render three tab panels: **Curriculum**, **Reviews**, and **Analytics**.

The page SHALL maintain a "selected mind map" state. When the page loads, it SHALL fetch the list of active mind maps and auto-select the first one. A mind map selector (dropdown) SHALL be visible above the tab panels, allowing the user to switch between mind maps.

The Curriculum tab SHALL be the default active tab.

#### Scenario: Page loads with active mind maps

- **WHEN** the user navigates to `/education`
- **AND** there are 3 active mind maps
- **THEN** the mind map selector SHALL list all 3 mind maps by title
- **AND** the first mind map SHALL be auto-selected
- **AND** the Curriculum tab SHALL be active

#### Scenario: Page loads with no mind maps

- **WHEN** the user navigates to `/education`
- **AND** there are no mind maps
- **THEN** the page SHALL display an empty state with a prompt to request a new curriculum
- **AND** the tab panels SHALL NOT be rendered

#### Scenario: Switching between tabs preserves selected mind map

- **WHEN** the user selects mind map "Python" from the dropdown
- **AND** switches from the Curriculum tab to the Analytics tab
- **THEN** the Analytics tab SHALL display data for the "Python" mind map

---

### Requirement: Mind map graph visualization in Curriculum tab

The Curriculum tab SHALL render the selected mind map as an interactive directed acyclic graph (DAG) using XYFlow with dagre top-to-bottom layout.

Each node SHALL display the concept label and a mastery score badge. Nodes SHALL be color-coded by `mastery_status`:
- `mastered`: emerald (`#10b981`)
- `reviewing`: blue (`#3b82f6`)
- `learning`: amber (`#f59e0b`)
- `diagnosed`: slate (`#64748b`)
- `unseen`: gray (`#d1d5db`)

Edges of type `prerequisite` SHALL render as solid arrows. Edges of type `related` SHALL render as dashed lines.

Frontier nodes (from the `/frontier` endpoint) SHALL have a pulsing ring indicator to highlight them as next teachable concepts.

Clicking a node SHALL open a detail panel beside the graph showing: node label, description, mastery score, mastery status, next review date (if scheduled), effort estimate, and a link to view quiz history for that node.

#### Scenario: Render a mind map with mixed mastery statuses

- **WHEN** the Curriculum tab loads for a mind map with 10 nodes
- **AND** 3 are mastered, 2 reviewing, 2 learning, 1 diagnosed, 2 unseen
- **THEN** the graph SHALL render 10 nodes with the correct color for each status
- **AND** prerequisite edges SHALL be solid arrows
- **AND** the layout SHALL flow top-to-bottom (root concepts at top)

#### Scenario: Frontier nodes highlighted

- **WHEN** the graph renders
- **AND** the frontier endpoint returns 2 nodes
- **THEN** those 2 nodes SHALL have a pulsing ring indicator

#### Scenario: Node click opens detail panel

- **WHEN** the user clicks a node labeled "List Comprehensions"
- **THEN** a detail panel SHALL appear showing the node's label, description, mastery score, mastery status, and next review date

#### Scenario: Empty mind map (no nodes)

- **WHEN** the Curriculum tab loads for a mind map with 0 nodes
- **THEN** the graph area SHALL display "This curriculum has no concepts yet — the butler is still building it"

---

### Requirement: Curriculum management actions

Below the mind map graph, the Curriculum tab SHALL display management actions for the selected mind map:
- A status badge showing the current mind map status (active/completed/abandoned)
- An "Abandon" button (visible when status is `active`) that calls `PUT /mind-maps/{id}/status` with `{"status": "abandoned"}`
- A "Re-activate" button (visible when status is `abandoned`) that calls `PUT /mind-maps/{id}/status` with `{"status": "active"}`

Above the mind map selector, a "Request New Curriculum" button SHALL open a dialog with fields for topic (required) and goal (optional). Submitting the dialog SHALL call `POST /curriculum-requests`. On 202 success, the dialog SHALL close and a toast notification SHALL confirm the request. On 409 conflict, the dialog SHALL display an error that a request is already pending.

After a successful status change, the mind map list query cache SHALL be invalidated to reflect the new status.

#### Scenario: Abandon an active curriculum

- **WHEN** the user views an active mind map
- **AND** clicks "Abandon"
- **THEN** a confirmation dialog SHALL appear
- **AND** confirming SHALL call `PUT /mind-maps/{id}/status` with `abandoned`
- **AND** the status badge SHALL update to "abandoned"

#### Scenario: Request a new curriculum

- **WHEN** the user clicks "Request New Curriculum"
- **AND** enters topic "Rust" and goal "Systems programming basics"
- **AND** submits the form
- **THEN** the system SHALL call `POST /curriculum-requests`
- **AND** on 202 response, a toast SHALL display "Curriculum requested — the butler will set it up shortly"
- **AND** the dialog SHALL close

#### Scenario: Duplicate curriculum request blocked

- **WHEN** the user submits a curriculum request
- **AND** the server returns 409
- **THEN** the dialog SHALL display "A curriculum request is already pending — please wait for the butler to process it"

---

### Requirement: Spaced repetition review timeline in Reviews tab

The Reviews tab SHALL display pending and upcoming spaced repetition reviews as a grouped timeline list with sections: **Overdue**, **Today**, **This Week**, **Later**.

Each review entry SHALL display: node label, parent mind map title, mastery score badge, and the scheduled review date/time.

The Overdue and Today sections SHALL be visually distinct (e.g., Overdue has a red left border, Today has an amber left border).

Reviews SHALL be fetched by iterating all active mind maps and calling the pending reviews endpoint for each. The pending reviews query SHALL refetch every 15 seconds.

When there are no pending reviews across any mind map, the Reviews tab SHALL display "No reviews scheduled — keep learning and reviews will appear here."

#### Scenario: Reviews grouped by time period

- **WHEN** the Reviews tab loads
- **AND** there are 2 overdue nodes, 1 due today, and 3 due this week
- **THEN** the Overdue section SHALL list 2 entries with red left border
- **AND** the Today section SHALL list 1 entry with amber left border
- **AND** the This Week section SHALL list 3 entries

#### Scenario: No pending reviews

- **WHEN** the Reviews tab loads
- **AND** no nodes have `next_review_at` in the past or near future
- **THEN** the tab SHALL display the empty state message

#### Scenario: Reviews span multiple mind maps

- **WHEN** the user has 2 active mind maps each with pending reviews
- **THEN** reviews from both mind maps SHALL appear in the timeline
- **AND** each entry SHALL show its parent mind map title

---

### Requirement: Mastery analytics in Analytics tab

The Analytics tab SHALL display mastery analytics for the selected mind map, consisting of:

1. **Summary cards row**: total nodes, mastered count, average mastery score, estimated completion days. Each card SHALL display the metric value and label.

2. **Mastery trend chart**: A Recharts `AreaChart` showing `mastery_pct` over time (fetched via the analytics endpoint with `trend_days=30`). The x-axis SHALL show dates, the y-axis SHALL show percentage (0–100%). The area fill SHALL be blue (`#3b82f6`) with 20% opacity.

3. **Cross-topic portfolio view**: When viewing analytics without a specific mind map selected (or via a "Portfolio" toggle), the tab SHALL display a comparative bar chart of `mastery_pct` across all active mind maps, plus the portfolio-level `portfolio_mastery` score. Data SHALL come from the `/analytics/cross-topic` endpoint.

4. **Struggling nodes callout**: If the analytics snapshot contains `struggling_nodes` (node IDs with 5+ reviews averaging quality < 2.5), the tab SHALL display a warning card listing these nodes by label with a link to view their quiz history.

#### Scenario: Analytics for a mind map with trend data

- **WHEN** the Analytics tab loads for a mind map with 7 days of snapshot data
- **THEN** the summary cards SHALL display current metrics
- **AND** the trend chart SHALL render 7 data points
- **AND** the y-axis SHALL range from 0% to 100%

#### Scenario: Analytics with struggling nodes

- **WHEN** the analytics snapshot contains 2 struggling node IDs
- **THEN** a warning card SHALL appear listing those 2 nodes by label
- **AND** each node label SHALL be clickable to view its quiz history

#### Scenario: Cross-topic portfolio view

- **WHEN** the user toggles to portfolio view
- **THEN** a bar chart SHALL display mastery_pct for each active mind map
- **AND** the portfolio_mastery score SHALL be displayed as a headline metric

#### Scenario: No analytics data yet

- **WHEN** the Analytics tab loads for a mind map with no snapshots
- **THEN** the tab SHALL display "Analytics will appear after the butler computes its first daily snapshot"

---

### Requirement: Quiz interaction history

The Curriculum tab's node detail panel SHALL include a "Quiz History" section showing paginated quiz responses for the selected node. Additionally, a "Quiz History" panel SHALL be accessible from the mind map selector level to view all quiz responses for the entire mind map.

Each quiz response entry SHALL display: question text, user answer (or "No answer" if null), quality score (0–5) as a colored badge (0–2 red, 3 amber, 4–5 green), response type (diagnostic/teach/review) as a label, and the timestamp.

Responses SHALL be ordered by `responded_at` descending (newest first). Pagination SHALL use a "Load more" button with a page size of 20.

#### Scenario: View quiz history for a specific node

- **WHEN** the user clicks a node in the mind map graph
- **AND** the detail panel opens
- **THEN** the Quiz History section SHALL load responses filtered by that node's ID
- **AND** display up to 20 responses ordered newest first

#### Scenario: Quality score color coding

- **WHEN** a quiz response has quality 1
- **THEN** the quality badge SHALL be red
- **WHEN** a quiz response has quality 3
- **THEN** the quality badge SHALL be amber
- **WHEN** a quiz response has quality 5
- **THEN** the quality badge SHALL be green

#### Scenario: Load more pagination

- **WHEN** the quiz history shows 20 responses
- **AND** more responses exist
- **THEN** a "Load more" button SHALL be visible
- **AND** clicking it SHALL append the next 20 responses

#### Scenario: No quiz responses for a node

- **WHEN** the user views a node with no quiz responses
- **THEN** the Quiz History section SHALL display "No quiz responses recorded yet"

---

### Requirement: Frontend API client and type definitions

The frontend SHALL define TypeScript interfaces matching the education API response models in `src/api/types.ts`. The API client in `src/api/client.ts` SHALL expose typed functions for all education endpoints (both existing and new).

TanStack Query hooks in `src/hooks/use-education.ts` SHALL wrap each client function with appropriate query keys (`["education", <resource>, ...params]`), refetch intervals (30s for lists, 15s for pending reviews), and `enabled` guards for conditional queries.

Mutation hooks SHALL be provided for:
- `useUpdateMindMapStatus` — calls `PUT /mind-maps/{id}/status`, invalidates `["education", "mind-maps"]` on success
- `useRequestCurriculum` — calls `POST /curriculum-requests`, shows toast on success/conflict

#### Scenario: Hook refetch intervals

- **WHEN** the `useMindMaps` hook is active
- **THEN** it SHALL refetch every 30 seconds
- **WHEN** the `usePendingReviews` hook is active
- **THEN** it SHALL refetch every 15 seconds

#### Scenario: Mutation invalidates cache

- **WHEN** `useUpdateMindMapStatus` succeeds
- **THEN** the `["education", "mind-maps"]` query cache SHALL be invalidated
- **AND** the mind map list SHALL refetch
