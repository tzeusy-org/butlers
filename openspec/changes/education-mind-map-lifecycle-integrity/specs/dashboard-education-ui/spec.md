## ADDED Requirements

### Requirement: Age-aware empty-curriculum copy

When the Curriculum tab renders a mind map that has zero nodes, the copy it
shows SHALL be derived from the map's `status` and the age of its `created_at`,
so that the page tells the owner what is actually true at that moment.

The evergreen string "This curriculum has no concepts yet — the butler is
still building it" SHALL NOT be rendered in any state. It asserts progress it
cannot observe, and it reads identically at ten seconds and at thirty-four
days.

The tiers, evaluated against `now - created_at`:

| Status | Age | Copy |
| --- | --- | --- |
| `draft` | under 15 minutes | "Setting up this curriculum — the butler is mapping out the concepts." |
| `draft` | 15 minutes to under 24 hours | "Still setting up — requested {relative age}. This usually finishes within a few minutes." |
| `draft` | 24 hours or more | "Setup stalled — requested {relative age} and no concepts have been added yet." |
| `abandoned` | any | "This curriculum was abandoned before any concepts were mapped." |
| `completed` | any | "This curriculum was marked complete without any concepts." |
| `active` | any | "This curriculum is marked active but has no concepts. That should not be possible — please report it." |

`{relative age}` SHALL be a human-readable elapsed duration derived from
`created_at` (for example "3 hours ago", "34 days ago"). The copy SHALL be
computed at render time, not cached from a server-side status field, so it
stays accurate without a refetch and does not depend on the weekly staleness
sweep having run.

In the stalled tier the graph area SHALL additionally present the map's
"Abandon" action inline, so the owner can clear a stalled curriculum from the
place where they discover it.

The `active` row exists as a fault indicator, not as a supported state: the
mind map content invariant forbids an `active` zero-node map. If the client
receives one, it SHALL surface the fault rather than dressing it as progress.

#### Scenario: Freshly requested curriculum shows setup copy

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 2 minutes ago
- **THEN** the graph area SHALL display "Setting up this curriculum — the butler is mapping out the concepts."
- **AND** it SHALL NOT display any "still building" copy

#### Scenario: Slow setup shows elapsed time

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 3 hours ago
- **THEN** the graph area SHALL display the "Still setting up" copy including a relative age of "3 hours ago"

#### Scenario: Stalled setup is named as stalled

- **WHEN** the Curriculum tab renders a `draft` mind map with 0 nodes created 34 days ago
- **THEN** the graph area SHALL display "Setup stalled — requested 34 days ago and no concepts have been added yet."
- **AND** the "Abandon" action SHALL be presented inline in the graph area

#### Scenario: Boundary at 24 hours moves from slow to stalled

- **WHEN** a `draft` mind map with 0 nodes has `created_at` exactly 24 hours in the past
- **THEN** the stalled copy SHALL be shown, not the "Still setting up" copy

#### Scenario: Abandoned empty curriculum reads as abandoned

- **WHEN** the Curriculum tab renders an `abandoned` mind map with 0 nodes
- **THEN** the graph area SHALL display "This curriculum was abandoned before any concepts were mapped."

#### Scenario: Active zero-node map is surfaced as a fault

- **WHEN** the Curriculum tab receives a mind map with `status = 'active'` and 0 nodes
- **THEN** the graph area SHALL display the integrity-fault copy naming the state as impossible
- **AND** it SHALL NOT display setup, "still building", or any other progress copy

#### Scenario: Copy updates without a refetch as the map ages

- **WHEN** a `draft` mind map with 0 nodes crosses the 15-minute boundary while the page is open
- **THEN** the rendered copy SHALL move to the next tier on the next render, because it is computed from `created_at` at render time

---

### Requirement: Per-map review fetch failure surfacing

The Reviews surfaces fan out one pending-reviews request per mind map. A
failure of any one of those requests SHALL be surfaced to the owner, named,
and SHALL NOT be folded into the calm empty state.

Specifically:

- The result of each per-map query SHALL be tracked individually. A query that
  errored SHALL NOT be coerced to an empty list of reviews.
- The "No reviews scheduled" empty state SHALL be rendered only when every
  per-map query succeeded and all of them returned zero reviews. If any query
  failed, the empty state MUST NOT be shown, because the surface cannot know
  whether reviews exist.
- A failed per-map query SHALL be reported with the `SourceDegradedNote`
  vocabulary (`frontend/src/components/ui/query-boundary.tsx`), naming the
  affected mind map by title and the reason, per
  `docs/api_and_protocols/response-conventions.md` ("Gate any verdict/all-clear
  renderer on the relevant flag(s) ... never suppress it").
- Reviews from the maps that did succeed SHALL still render. A partial failure
  degrades the surface; it does not blank it.
- The same rules apply to the `/education` page Reviews tab and to the
  education butler-detail Reviews tab, since both perform the same fan-out.

A review the owner never sees because its fetch failed silently is worse than
a banner they do see. Fabricated calm is the specific failure mode this
requirement exists to prevent.

#### Scenario: One map's review fetch fails among several

- **WHEN** the Reviews tab fans out over 3 active mind maps and the request for map "Rust" fails
- **AND** the other two maps return reviews
- **THEN** the reviews from the two succeeding maps SHALL render
- **AND** a `SourceDegradedNote` SHALL name "Rust" and the failure reason
- **AND** the "No reviews scheduled" empty state SHALL NOT be rendered

#### Scenario: All review fetches fail

- **WHEN** every per-map pending-reviews request fails
- **THEN** the tab SHALL render a degraded note naming the failing maps
- **AND** the tab SHALL NOT render "No reviews scheduled — keep learning and reviews will appear here."

#### Scenario: A failing map with genuinely zero reviews is still not calm

- **WHEN** the Reviews tab fans out over 2 maps, one returns zero reviews and the other's request fails
- **THEN** the empty state SHALL NOT be rendered, because the surface cannot distinguish "no reviews" from "not known"
- **AND** a degraded note SHALL name the failing map

#### Scenario: Empty state requires every query to have succeeded

- **WHEN** all per-map pending-reviews requests succeed and every one returns zero reviews
- **THEN** the tab SHALL render "No reviews scheduled — keep learning and reviews will appear here."
- **AND** no degraded note SHALL be rendered

#### Scenario: Butler-detail Reviews tab surfaces the same failure

- **WHEN** the education butler-detail Reviews tab fans out over active mind maps and one map's request fails
- **THEN** its pending reviews timeline SHALL render a degraded note naming the failing map
- **AND** its KPI row SHALL NOT present counts as complete while a source is degraded

#### Scenario: Recovery clears the degraded note

- **WHEN** a previously failing per-map request succeeds on a subsequent refetch
- **THEN** the degraded note for that map SHALL be removed
- **AND** that map's reviews SHALL appear in the timeline

---

## MODIFIED Requirements

### Requirement: Education page layout with tab panels

The education page SHALL display a page header with title "Education" and a description line. Below the header, the page SHALL render three tab panels: **Curriculum**, **Reviews**, and **Analytics**.

The page SHALL maintain a "selected mind map" state. When the page loads, it SHALL fetch the list of mind maps whose status is `active` or `draft` and auto-select one, preferring the first `active` map and falling back to the first `draft` map when there is no active one. A mind map selector (dropdown) SHALL be visible above the tab panels, allowing the user to switch between mind maps. Draft maps SHALL be listed with a "Setting up" badge distinguishing them from active ones.

Draft maps SHALL NOT be filtered out of the selector. A map the owner cannot see is a map the owner cannot abandon, which is how the phantom curriculum survived unnoticed; visibility is what makes a stalled draft actionable.

The page SHALL coordinate node selection through one page-level `{mindMapId, nodeId}` handler shared by the Curriculum graph, Reviews timeline, and Analytics struggling-node callout. A selection for a different mind map SHALL update the selected map and node together. One shared node-detail panel SHALL render only when the selected map and node resolve together, SHALL remain visible when the user changes tabs, and SHALL not show a placeholder or details from another map when the node is unavailable. Closing the panel SHALL clear only the node selection, preserving the selected map and active tab.

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

#### Scenario: Draft maps appear in the selector

- **WHEN** the user navigates to `/education`
- **AND** there are 2 active mind maps and 1 draft mind map
- **THEN** the selector SHALL list all 3
- **AND** the draft map SHALL carry a "Setting up" badge
- **AND** an active map SHALL be auto-selected in preference to the draft

#### Scenario: A draft map is selectable when it is the only map

- **WHEN** the user navigates to `/education`
- **AND** the only mind map is a `draft` with zero nodes
- **THEN** the draft map SHALL be auto-selected
- **AND** the tab panels SHALL be rendered
- **AND** the Curriculum tab SHALL show the age-aware empty-curriculum copy for that map

#### Scenario: Switching between tabs preserves selected mind map

- **WHEN** the user selects mind map "Python" from the dropdown
- **AND** switches from the Curriculum tab to the Analytics tab
- **THEN** the Analytics tab SHALL display data for the "Python" mind map

#### Scenario: Unavailable selected node does not render mismatched details

- **WHEN** a selected node no longer belongs to the selected mind map
- **THEN** the shared node-detail panel SHALL NOT render a placeholder or details for another node

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

Clicking a node SHALL select it through the shared page-level handler and reveal the shared detail panel showing: node label, description, mastery score, mastery status, next review date (if scheduled), effort estimate, the spaced-repetition internals `ease_factor` and `repetitions`, and a link to view quiz history for that node.

When the selected mind map has zero nodes, the graph area SHALL render the copy defined by "Age-aware empty-curriculum copy" rather than a fixed string.

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
- **THEN** the graph area SHALL display the copy selected by "Age-aware empty-curriculum copy" for that map's `status` and `created_at` age
- **AND** the graph area SHALL NOT display "This curriculum has no concepts yet — the butler is still building it"

---

### Requirement: Curriculum management actions

Below the mind map graph, the Curriculum tab SHALL display management actions for the selected mind map:
- A status badge showing the current mind map status (draft/active/completed/abandoned), rendered as "Setting up" for `draft`
- An "Abandon" button (visible when status is `active` or `draft`) that calls `PUT /mind-maps/{id}/status` with `{"status": "abandoned"}`
- A "Re-activate" button (visible when status is `abandoned`) that calls `PUT /mind-maps/{id}/status` with `{"status": "active"}`. The button SHALL be disabled for an abandoned map with zero nodes, with an explanation that there are no concepts to return to, since the server refuses that transition with 409.

There SHALL be no control that activates a mind map with zero nodes. If a 409 lifecycle refusal is nonetheless received from `PUT /mind-maps/{id}/status`, the dashboard SHALL display the reason from the response body rather than a generic error.

Above the mind map selector, a "Request curriculum" button SHALL open a dialog with fields for topic (required) and goal (optional). Submitting the dialog SHALL call `POST /curriculum-requests`. On 202 success, the dialog SHALL close and a toast notification SHALL confirm the request. On 409 conflict, the dialog SHALL display an error that a request is already pending.

After a successful status change, the mind map list query cache SHALL be invalidated to reflect the new status.

#### Scenario: Abandon an active curriculum

- **WHEN** the user views an active mind map
- **AND** clicks "Abandon"
- **THEN** a confirmation dialog SHALL appear
- **AND** confirming SHALL call `PUT /mind-maps/{id}/status` with `abandoned`
- **AND** the status badge SHALL update to "abandoned"

#### Scenario: Abandon a stalled draft curriculum

- **WHEN** the user views a `draft` mind map with zero nodes
- **THEN** the status badge SHALL read "Setting up"
- **AND** an "Abandon" button SHALL be available
- **AND** confirming it SHALL call `PUT /mind-maps/{id}/status` with `abandoned`

#### Scenario: Re-activate is unavailable for an empty abandoned map

- **WHEN** the user views an `abandoned` mind map with zero nodes
- **THEN** the "Re-activate" button SHALL be disabled
- **AND** the disabled state SHALL explain that the curriculum has no concepts to return to

#### Scenario: Request a new curriculum

- **WHEN** the user clicks "Request curriculum"
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

Each review entry SHALL be a native keyboard-accessible button that emits its owning `{mindMapId, nodeId}` selection to the page-level handler.

The Overdue and Today sections SHALL be visually distinct (e.g., Overdue has a red left border, Today has an amber left border).

Reviews SHALL be fetched by iterating all active mind maps and calling the pending reviews endpoint for each. Draft maps SHALL be excluded from this fan-out: they have no nodes and therefore no reviews. The pending reviews query SHALL refetch every 15 seconds.

When there are no pending reviews across any mind map **and every per-map query succeeded**, the Reviews tab SHALL display "No reviews scheduled — keep learning and reviews will appear here." When any per-map query failed, the tab SHALL instead follow "Per-map review fetch failure surfacing": the empty state MUST NOT stand in for an unknown result.

#### Scenario: Reviews grouped by time period

- **WHEN** the Reviews tab loads
- **AND** there are 2 overdue nodes, 1 due today, and 3 due this week
- **THEN** the Overdue section SHALL list 2 entries with red left border
- **AND** the Today section SHALL list 1 entry with amber left border
- **AND** the This Week section SHALL list 3 entries

#### Scenario: No pending reviews

- **WHEN** the Reviews tab loads
- **AND** no nodes have `next_review_at` in the past or near future
- **AND** every per-map pending-reviews query succeeded
- **THEN** the tab SHALL display the empty state message

#### Scenario: Reviews span multiple mind maps

- **WHEN** the user has 2 active mind maps each with pending reviews
- **THEN** reviews from both mind maps SHALL appear in the timeline
- **AND** each entry SHALL show its parent mind map title

#### Scenario: Cross-map review selection preserves Reviews context

- **WHEN** map "Python" is selected and the Reviews tab is active
- **AND** the user activates a review for a node in map "Rust"
- **THEN** the selected map and node SHALL change to the "Rust" review together
- **AND** the shared node-detail panel SHALL show the selected "Rust" node
- **AND** the Reviews tab SHALL remain active

#### Scenario: Draft maps are not fanned out over

- **WHEN** the owner has 1 active mind map and 1 draft mind map
- **THEN** the Reviews tab SHALL issue a pending-reviews request only for the active map

---

### Requirement: Education butler-detail Reviews tab

In addition to the standalone `/education` page, the butler detail page for the education butler SHALL provide a "Reviews" tab (`ButlerEducationReviewsTab`) that aggregates learning state across all active mind maps into a single multi-panel dashboard.

The tab SHALL display:
1. A KPI summary row: total review cards, mastered count, overdue count, and average mastery score.
2. A mind maps progress panel: per-map mastery percentage with a progress bar and mastered/total node count.
3. A pending reviews timeline grouped by Overdue / Today / This Week / Later, with colored left borders per group.
4. A frontier nodes panel: the next concepts to learn, each with a mastery badge.
5. A 7-day retention trend chart with the latest mastery percentage as a headline value and a hover tooltip.

Polling intervals SHALL scale with the number of active mind maps so that total request volume stays bounded as the map count grows (base interval 15 seconds for reviews, 30 seconds for mastery and frontier data).

Every panel above is built from a per-map fan-out and SHALL obey "Per-map review fetch failure surfacing": a failed per-map query SHALL be named rather than absorbed, and no panel SHALL present an all-clear or a complete-looking total while one of its sources is degraded.

#### Scenario: Reviews tab aggregates across maps

- **WHEN** the education butler detail page Reviews tab loads with multiple active mind maps
- **THEN** the KPI row, mind maps progress panel, pending reviews timeline, frontier panel, and retention trend chart SHALL each render with data aggregated across those maps
- **AND** the pending reviews SHALL be grouped into Overdue, Today, This Week, and Later sections

#### Scenario: Degraded source is named on the butler-detail tab

- **WHEN** the Reviews tab loads and the pending-reviews request for one mind map fails
- **THEN** a degraded note SHALL name that mind map
- **AND** the KPI row SHALL NOT present its counts as a complete total
