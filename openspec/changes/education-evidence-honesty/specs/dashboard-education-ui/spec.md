## MODIFIED Requirements

### Requirement: Curriculum request outcome receipt

After a curriculum request is accepted, the Education page SHALL render a receipt panel for the tracked request, fed by `GET /curriculum-requests/{request_id}` (falling back to `GET /curriculum-requests/latest` when no request has been submitted in this session).

The panel SHALL render four distinct states and SHALL NOT collapse any of them into another:
- **accepted / running** — the request was accepted and work is in flight. The panel SHALL say so and SHALL NOT claim the curriculum exists or that the owner has been contacted.
- **completed** — the receipt carries terminal evidence. The panel SHALL render a Ready badge only when the API reports `verification: "verified"`. A completed receipt with `verification: "legacy_unverified"`, a missing `verification`, or a value the frontend does not recognise SHALL be rendered as completed but unverified, with no Ready badge, and SHALL NOT be described as a ready curriculum. The frontend SHALL NOT compute readiness itself from `mind_map_id` or from the existence of a map. The panel SHALL name the curriculum topic and SHALL distinguish `calibration_ready_at` being set (the teaching flow has started calibrating) from it being unset, rather than implying calibration in both cases. The panel SHALL state what became of the calibration notice as a line separate from calibration, so that a reader cannot take calibration having started as evidence of having been messaged.
- **failed** — the panel SHALL render the terminal `failure_reason` in owner-readable language and SHALL offer a retry. The reason `curriculum_unverified` SHALL be rendered as the butler having produced a curriculum it could not verify, never as a ready curriculum.
- **unavailable** — when `receipts_available` is `false`, the panel SHALL say the status could not be read, and SHALL NOT render an all-clear or an empty "no request" state.

The panel SHALL claim that a message went out only from `calibration_notice_outcome`, and only for the value `delivered`. That claim SHALL be worded as the delivery channel having accepted the message, because that is the strongest thing the notification path attests; the panel SHALL NOT state or imply that the owner received or read anything.

For every other outcome — a recorded `failed`, `suppressed`, `deferred` or `coalesced` dispatch, a `no_record` absence, an `unproven` read, a null outcome, or an outcome the frontend does not recognise — the panel SHALL say that contact is not confirmed, and SHALL direct the owner to this panel rather than to their messages. An unrecognised outcome SHALL degrade to "not confirmed" and never to an implied yes, so a new backend outcome cannot silently become a delivery claim in the UI.

The panel SHALL provide doors to the evidence it names: a link to the session (`/sessions/{session_id}`) whenever `session_id` is present, including on the failure path, and a control that opens the correlated curriculum whenever `mind_map_id` is present.

While the receipt is non-terminal the query SHALL poll; once terminal it SHALL stop polling.

In fallback mode (no request submitted in this session, so the panel reads the latest request), a receipt that settled longer than the recency window ago SHALL NOT be rendered — otherwise a curriculum created weeks ago would keep a permanent card on the page. A request tracked by `request_id` from this session's 202 SHALL always be rendered, however long ago it settled.

The panel SHALL be announced to assistive technology as a live status region, and every door SHALL be a keyboard-reachable control with an accessible name.

ID: REQ-dashboard-education-ui-002
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Verified completion shows Ready

- **WHEN** the tracked receipt has status `completed` and `verification: "verified"`
- **THEN** the panel SHALL render the Ready badge

#### Scenario: Legacy completed receipt is legible but unverified

- **WHEN** the tracked receipt has status `completed` and `verification: "legacy_unverified"`
- **THEN** the panel SHALL render the topic and its doors
- **AND** SHALL state that the curriculum is unverified
- **AND** SHALL NOT render a Ready badge

#### Scenario: Unverified curriculum failure is not a Ready state

- **WHEN** the tracked receipt has status `failed` with `failure_reason: "curriculum_unverified"`
- **THEN** the panel SHALL explain that the curriculum could not be verified
- **AND** SHALL NOT render a Ready badge

#### Scenario: Accepted request does not claim completion

- **WHEN** the tracked receipt has status `accepted`
- **THEN** the panel SHALL describe the request as accepted and in progress
- **AND** SHALL NOT state that a curriculum was created or that the butler has messaged the owner

#### Scenario: Completed request opens its doors

- **WHEN** the tracked receipt has status `completed` with a `mind_map_id` and a `session_id`
- **THEN** the panel SHALL offer a control that selects that curriculum
- **AND** SHALL offer a link to `/sessions/{session_id}`

#### Scenario: Calibration started does not become "the butler messaged you"

- **WHEN** the tracked receipt has status `completed` with `calibration_ready_at` set
- **AND** `calibration_notice_outcome` is `failed`, `suppressed`, `no_record`, `unproven`, null, or a value the frontend does not recognise
- **THEN** the panel SHALL state that contact is not confirmed and point the owner at this panel
- **AND** SHALL NOT state that a message reached the owner
- **AND** SHALL still state that calibration has started

#### Scenario: Channel acceptance is stated as channel acceptance

- **WHEN** the tracked receipt carries `calibration_notice_outcome: "delivered"`
- **THEN** the panel SHALL say the owner's messaging channel accepted the butler's starting message
- **AND** SHALL NOT describe it as read, seen, or received by the owner

#### Scenario: Failed request is legible and retryable

- **WHEN** the tracked receipt has status `failed` with `failure_reason: "trigger_unreachable"`
- **THEN** the panel SHALL explain that the butler could not be reached
- **AND** SHALL offer a retry control
- **AND** SHALL still link the session when `session_id` is present

#### Scenario: Unavailable status is not an all-clear

- **WHEN** the status read returns `receipts_available: false`
- **THEN** the panel SHALL state that the request status could not be read
- **AND** SHALL NOT render a success, a failure, or an empty state

#### Scenario: Polling stops at a terminal state

- **WHEN** the tracked receipt reaches `completed` or `failed`
- **THEN** the receipt query SHALL stop refetching

#### Scenario: A long-settled fallback receipt is not parked on the page

- **WHEN** no request has been submitted in this session
- **AND** the latest receipt settled longer than the recency window ago
- **THEN** the panel SHALL render nothing

#### Scenario: A request tracked in this session stays visible

- **WHEN** a request submitted in this session settled longer than the recency window ago
- **THEN** the panel SHALL still render its terminal outcome

### Requirement: Mastery analytics in Analytics tab

The Analytics tab SHALL display mastery analytics for the selected mind map, consisting of:

1. **Summary cards row**: total nodes, mastered count, average mastery score, estimated completion days. Each card SHALL display the metric value and label. The average mastery card SHALL render a percentage only when the mastery summary reports `measured_over > 0`; when `avg_mastery_score` is `null` or `measured_over` is `0` it SHALL render "No graded evidence yet" and SHALL NOT render `0%`. A genuine zero with `measured_over > 0` SHALL render as `0%`, with the number of measured nodes available to the reader.

2. **Mastery trend chart**: A Recharts `AreaChart` showing `mastery_pct` over time (fetched via the analytics endpoint with `trend_days=30`). The x-axis SHALL show dates, the y-axis SHALL show percentage (0–100%). The area fill SHALL be blue (`#3b82f6`) with 20% opacity.

3. **Cross-topic portfolio view**: When viewing analytics without a specific mind map selected (or via a "Portfolio" toggle), the tab SHALL display a comparative bar chart of `mastery_pct` across all active mind maps, plus the portfolio-level `portfolio_mastery` score. Data SHALL come from the `/analytics/cross-topic` endpoint.

4. **Struggling nodes callout**: If the analytics snapshot contains `struggling_nodes` (node IDs with 5+ reviews averaging quality < 2.5), the tab SHALL display a warning card listing these nodes by label as controls that select the node through the shared page-level handler, exposing its quiz history in the shared detail panel.

ID: REQ-dashboard-education-ui-003
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Analytics for a mind map with trend data

- **WHEN** the Analytics tab loads for a mind map with 7 days of snapshot data
- **THEN** the summary cards SHALL display current metrics
- **AND** the trend chart SHALL render 7 data points
- **AND** the y-axis SHALL range from 0% to 100%

#### Scenario: Analytics with struggling nodes

- **WHEN** the analytics snapshot contains 2 struggling node IDs
- **THEN** a warning card SHALL appear listing those 2 nodes by label
- **AND** selecting a node label SHALL keep the Analytics tab active and show that node in the shared detail panel

#### Scenario: Cross-topic portfolio view

- **WHEN** the user toggles to portfolio view
- **THEN** a bar chart SHALL display mastery_pct for each active mind map
- **AND** the portfolio_mastery score SHALL be displayed as a headline metric

#### Scenario: No analytics data yet

- **WHEN** the Analytics tab loads for a mind map with no snapshots
- **THEN** the tab SHALL display "Analytics will appear after the butler computes its first daily snapshot"

#### Scenario: No graded evidence yet is not 0%

- **WHEN** the mastery summary reports `measured_over: 0` and `avg_mastery_score: null`
- **THEN** the average mastery card SHALL display "No graded evidence yet"
- **AND** SHALL NOT display `0%`

#### Scenario: A measured zero is shown as zero

- **WHEN** the mastery summary reports `measured_over: 3` and `avg_mastery_score: 0`
- **THEN** the average mastery card SHALL display `0%`

## ADDED Requirements

### Requirement: Node mastery Why disclosure

The shared node detail panel SHALL provide a "Why" disclosure that shows the node's mastery transition history from `GET /api/education/nodes/{node_id}/mastery-transitions`: each status change in order, its evidence kind, its time, and a door to the corresponding quiz response or session where one exists.

An entry with `origin: "legacy_unknown"` SHALL be rendered as an explicit unproved legacy state ("already mastered before evidence was recorded") and SHALL NOT be rendered with an invented date or as a proved transition. When the response reports `consistent: false`, or the history cannot be read, the disclosure SHALL say the history is degraded or unavailable, and the panel SHALL NOT present the node's current status as a confident mastery claim. A node with no entries SHALL show that no mastery changes are recorded, not a fabricated chain. The disclosure SHALL be keyboard-operable with an accessible name, and the frontend SHALL render the API's states rather than deciding them.

ID: REQ-dashboard-education-ui-004
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Ordered chain with evidence doors

- **WHEN** a node with two evidence-origin transitions is opened and "Why" is expanded
- **THEN** both transitions SHALL be listed oldest first with their evidence kind and time
- **AND** each entry with a `quiz_response_id` or session SHALL offer a door to it

#### Scenario: Legacy entry is shown as unproved

- **WHEN** the node's only entry has `origin: "legacy_unknown"`
- **THEN** the disclosure SHALL state that the status predates recorded evidence
- **AND** SHALL NOT show a proof date

#### Scenario: Degraded history withholds the confident claim

- **WHEN** the transitions response reports `consistent: false`
- **THEN** the panel SHALL state the history is degraded
- **AND** SHALL NOT present the current mastery status as a confident claim

#### Scenario: Empty history is empty

- **WHEN** the node has no ledger entries
- **THEN** the disclosure SHALL state that no mastery changes are recorded

