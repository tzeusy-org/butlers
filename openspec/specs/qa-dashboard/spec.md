# QA Dashboard

## Purpose

Dashboard pages and API endpoints that make QA staffer activity, progress, and usefulness visible to operators. Covers the QA overview page, patrol detail views, investigation detail views with linked PR status, a home page summary widget, known issues tracker, and all supporting REST API endpoints. Deployed at the existing dashboard URL (https://tzeusy.parrot-hen.ts.net/butlers-dev/).

## ADDED Requirements

### Requirement: QA Overview Page
The dashboard SHALL have a top-level QA page at route `/qa` showing the QA staffer's operational state at a glance.

#### Scenario: Overview page layout
- **WHEN** a user navigates to `/qa`
- **THEN** the page displays:
  - **Status banner**: QA staffer health (running/stopped/circuit-breaker-tripped), last patrol time, next expected patrol time
  - **Summary statistics cards**: Total patrols (24h), findings discovered (24h), novel findings (24h), investigations dispatched (24h), PRs opened (24h), PRs merged (all-time)
  - **Investigation pipeline**: Kanban-style columns showing investigations by status (queued → investigating → pr_open → pr_merged → failed/timeout/unfixable) with clickable cards
  - **Known issues panel**: Active investigations and open PRs grouped by fingerprint, showing current status and PR links (see Known Issues Tracker requirement)
  - **Recent patrols table**: Last 20 patrol cycles with timestamps, sources polled, finding counts, dispatch counts, and status badges
  - **Success rate trend**: Line chart showing investigation success rate (PRs merged / total dispatched) over the last 7 days
  - **Source breakdown**: Per-discovery-source finding counts (pie chart or stacked bar) to show which sources are producing the most findings

#### Scenario: Empty state
- **WHEN** no patrol cycles have been recorded
- **THEN** the overview page shows an empty state with guidance on QA staffer setup

### Requirement: Known Issues Tracker
The dashboard SHALL display a persistent view of all known/open issues that the QA system is tracking, with PR links and status.

#### Scenario: Known issues panel content
- **WHEN** the known issues panel is rendered
- **THEN** it shows all active `healing_attempts` rows (status in: `investigating`, `pr_open`, `dispatch_pending`) grouped by fingerprint
- **AND** each entry displays: fingerprint (truncated + copyable), affected butler(s), severity badge, exception type, discovery source type, current status badge, created timestamp, duration since creation

#### Scenario: PR link in known issues
- **WHEN** a known issue has `status = "pr_open"` or `status = "pr_merged"`
- **THEN** the entry includes a clickable link to the GitHub PR (`pr_url`)
- **AND** the PR status is displayed inline (open/merged/closed) fetched from the `healing_attempts` record
- **AND** the PR number is shown as a badge (e.g., `#42`)

#### Scenario: Known issues are filterable
- **WHEN** the known issues panel is displayed
- **THEN** operators can filter by: status (investigating/pr_open/pr_merged/failed), severity, source butler, discovery source type
- **AND** operators can sort by: created date, severity, occurrence count

#### Scenario: Dismiss from known issues
- **WHEN** an operator clicks "Dismiss" on a known issue
- **THEN** a dismiss dialog appears with configurable duration (default: 24h)
- **AND** dismissing creates a `qa_dismissals` entry for the fingerprint
- **AND** the issue is visually muted but remains visible until the dismiss expires

### Requirement: Patrol Detail Page
The dashboard SHALL have a drill-down page at `/qa/patrols/:patrolId` showing the full details of a single patrol cycle.

#### Scenario: Patrol detail layout
- **WHEN** a user navigates to `/qa/patrols/:patrolId`
- **THEN** the page displays:
  - **Patrol metadata**: Start/end timestamps, duration, status, configuration used (lookback, interval), sources polled
  - **Findings table**: All findings from this patrol with: fingerprint (truncated), source type badge, source butler, severity badge, exception type, event summary, occurrence count, dedup reason (or "novel" badge)
  - **Dispatch summary**: Which findings were dispatched as investigations, with links to investigation detail pages
  - Findings are sortable by severity, source type, and filterable by dedup reason

#### Scenario: Patrol not found
- **WHEN** the patrol ID does not exist
- **THEN** the page shows a 404 message

### Requirement: Investigation Detail Page
The dashboard SHALL have a drill-down page at `/qa/investigations/:attemptId` showing the full lifecycle of a QA-originated investigation, with PR tracking.

#### Scenario: Investigation detail layout
- **WHEN** a user navigates to `/qa/investigations/:attemptId`
- **THEN** the page displays:
  - **Investigation metadata**: Attempt ID, fingerprint, source butler, discovery source type, severity, status, created/updated/closed timestamps
  - **Timeline**: Visual timeline showing state transitions (dispatched → investigating → pr_open/failed/timeout/unfixable → pr_merged) with timestamps for each transition
  - **Error context**: Exception type, event summary, call site (no raw log content — all sanitized)
  - **PR section**: If status is `pr_open` or `pr_merged`, a prominent card with: clickable link to the GitHub PR, PR number, PR title, current PR status (open/merged/closed), created date
  - **Agent session link**: If a healing session was recorded, link to `/sessions/:healing_session_id`
  - **Patrol link**: Link back to the originating patrol cycle at `/qa/patrols/:qa_patrol_id`
  - **Actions**: Retry button (for terminal statuses), Dismiss button (to prevent re-investigation)

#### Scenario: Investigation not found
- **WHEN** the attempt ID does not exist or has no `qa_patrol_id`
- **THEN** the page shows a 404 message

### Requirement: Home Page QA Widget
The main dashboard page (`/`) SHALL include a QA staffer summary widget alongside existing status cards.

#### Scenario: QA widget content
- **WHEN** the dashboard home page loads
- **THEN** the QA widget displays:
  - QA staffer status indicator (green=healthy, yellow=circuit-breaker-tripped, red=stopped/error)
  - Last patrol timestamp and outcome (clean/findings/error)
  - Active investigations count
  - Open PRs count with links (clickable to `/qa` filtered to pr_open)
  - PRs merged in last 7d
  - Click-through to `/qa` for full details

#### Scenario: QA staffer not deployed
- **WHEN** the QA staffer is not running or has no patrol records
- **THEN** the widget shows a muted state with "QA Staffer not active"

### Requirement: QA API Endpoints
The dashboard API SHALL expose endpoints under `/api/qa/` to support the frontend pages and provide state persistence for deduplication tracking. All endpoints SHALL use the standard response envelopes from RFC 0007: `ApiResponse<T>` for single-item responses, `PaginatedResponse<T>` for lists, `ErrorResponse` for errors.

#### Scenario: GET /api/qa/summary
- **WHEN** `GET /api/qa/summary` is called
- **THEN** it returns: `{ staffer_status, last_patrol_at, next_patrol_at, stats_24h: { patrols, findings, novel, dispatched, prs_opened }, stats_all_time: { prs_merged, prs_failed, success_rate }, circuit_breaker: { tripped, consecutive_failures }, active_sources: [str] }`

#### Scenario: GET /api/qa/patrols
- **WHEN** `GET /api/qa/patrols` is called with optional `limit` and `offset`
- **THEN** it returns a paginated list of patrol records ordered by `started_at` descending
- **AND** each record includes `sources_polled` array

#### Scenario: GET /api/qa/patrols/:patrolId
- **WHEN** `GET /api/qa/patrols/:patrolId` is called
- **THEN** it returns the full patrol record including nested findings

#### Scenario: GET /api/qa/patrols/:patrolId/findings
- **WHEN** `GET /api/qa/patrols/:patrolId/findings` is called
- **THEN** it returns all findings for that patrol cycle with dedup reasons, source types, and linked attempt IDs

#### Scenario: GET /api/qa/investigations
- **WHEN** `GET /api/qa/investigations` is called with optional `status` filter
- **THEN** it returns a paginated list of QA-originated healing attempts (those with non-null `qa_patrol_id`)
- **AND** each record includes `pr_url`, `pr_number`, and current status

#### Scenario: GET /api/qa/known-issues
- **WHEN** `GET /api/qa/known-issues` is called
- **THEN** it returns all active/open issues: healing attempts with status in (`dispatch_pending`, `investigating`, `pr_open`)
- **AND** grouped by fingerprint with occurrence count, affected butlers, and PR info where available

#### Scenario: POST /api/qa/dismiss
- **WHEN** `POST /api/qa/dismiss` is called with `{ fingerprint, duration_hours }`
- **THEN** the fingerprint is added to the dismissal cache in `public.qa_dismissals`
- **AND** returns `{ fingerprint, dismissed_until }`

#### Scenario: POST /api/qa/force-patrol
- **WHEN** `POST /api/qa/force-patrol` is called
- **THEN** a patrol cycle is triggered immediately regardless of the schedule
- **AND** returns `{ patrol_id, status: "triggered" }`

#### Scenario: GET /api/qa/trends
- **WHEN** `GET /api/qa/trends` is called with `days` parameter (default: 7)
- **THEN** it returns daily aggregated stats: `[{ date, patrols, findings, novel, dispatched, prs_opened, prs_merged, success_rate, by_source: { source_type: count } }]`

#### Scenario: GET /api/qa/dismissals
- **WHEN** `GET /api/qa/dismissals` is called
- **THEN** it returns all active dismissals (dismissed_until > now()) for management

#### Scenario: DELETE /api/qa/dismissals/:fingerprint
- **WHEN** `DELETE /api/qa/dismissals/:fingerprint` is called
- **THEN** the dismissal for that fingerprint is removed
- **AND** subsequent patrol cycles will treat findings with that fingerprint as novel again

### Requirement: QA Settings Surface
The dashboard settings page SHALL expose the operator-managed configuration needed for QA investigations to clone, commit, and open PRs successfully.

#### Scenario: QA Staffer settings card
- **WHEN** the user opens `/settings`
- **THEN** the "QA Staffer" card shows repository configuration, GitHub token status, git author identity status, and the allowed-repositories whitelist
- **AND** the configuration badge is only "Configured" when repository settings exist, `BUTLERS_QA_GH_TOKEN` is present, and both `BUTLERS_QA_GIT_AUTHOR_NAME` and `BUTLERS_QA_GIT_AUTHOR_EMAIL` are present

#### Scenario: Git author identity is editable
- **WHEN** the operator edits the QA Staffer card's git author identity fields
- **THEN** the dashboard stores `BUTLERS_QA_GIT_AUTHOR_NAME` and `BUTLERS_QA_GIT_AUTHOR_EMAIL` via the shared secrets/settings backend
- **AND** those values are treated as the commit identity for QA-generated commits
- **AND** they are validated and surfaced independently from the GitHub token because git commit identity and GitHub authentication are separate requirements

### Requirement: Navigation Integration
The QA page SHALL be accessible from the dashboard's main navigation.

#### Scenario: Sidebar navigation entry
- **WHEN** the dashboard sidebar is rendered
- **THEN** a "QA" navigation item links to `/qa`
- **AND** it is grouped with other infrastructure items (after "Issues", before "Audit Log")
- **AND** it shows a badge with the count of active investigations when > 0

#### Scenario: Router registration
- **WHEN** the frontend router is configured
- **THEN** routes `/qa`, `/qa/patrols/:patrolId`, and `/qa/investigations/:attemptId` are registered
- **AND** all routes render within the `RootLayout`

### Requirement: Backward Compatibility with Healing API
The existing `/api/healing/` router SHALL continue to function unchanged. QA-originated investigations appearing in `healing_attempts` (with non-null `qa_patrol_id`) will be visible in existing healing endpoints — this is intentional and provides a unified view.

#### Scenario: Existing healing API shows QA investigations
- **WHEN** `GET /api/healing/attempts` is called
- **THEN** QA-originated investigations (qa_patrol_id IS NOT NULL) appear alongside per-butler self-healing attempts
- **AND** the `qa_patrol_id` field distinguishes them
- **AND** no changes to the existing healing API response schema are required (qa_patrol_id is an additive nullable field)

### Requirement: State Persistence for Deduplication
The dashboard SHALL expose the deduplication state (dismissals, known issues, PR status) as inspectable, manageable data.

#### Scenario: Dismissals are visible and manageable
- **WHEN** an operator navigates to `/qa`
- **THEN** active dismissals are visible in a collapsible panel
- **AND** each dismissal shows: fingerprint, dismissed_until, dismissed_by
- **AND** operators can remove dismissals to re-enable investigation

#### Scenario: PR status is kept current
- **WHEN** a healing attempt has `status = "pr_open"`
- **THEN** the QA staffer checks the PR's GitHub status on each patrol cycle
- **AND** if the PR has been merged, the attempt transitions to `pr_merged`
- **AND** if the PR has been closed without merge, the attempt transitions to `failed` with `error_detail = "pr_closed_without_merge"`
- **AND** the dashboard reflects the current status without manual refresh
