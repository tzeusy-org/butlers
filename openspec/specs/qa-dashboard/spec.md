# QA Dashboard

## Purpose

Dashboard pages and API endpoints that make QA staffer activity, progress, and usefulness visible to operators. Covers the QA overview page (a per-case dossier in the Dispatch design language), patrol detail views, investigation detail views with linked PR status, the case dossier layout, an investigations case-index page, a home page summary widget, and all supporting REST API endpoints. Deployed at the existing dashboard URL (https://tzeusy.parrot-hen.ts.net/butlers-dev/).

## Requirements

### Requirement: QA Overview Page
The dashboard SHALL have a top-level QA page at route `/qa` that presents the QA staffer's work as a per-case dossier in the Dispatch design language. The page narrates what the staff caught and what it fixed; it is not a pipeline dashboard.

#### Scenario: Overview page layout
- **WHEN** a user navigates to `/qa`
- **THEN** the page renders, in vertical order:
  - **Sticky top bar**: severity filter (all / high / medium / low) and theme toggle, shared with `/overview` and `/butlers`
  - **Page header**: title "What the staff caught and fixed", clock (mono, `HH:MM` 24h, tabular nums), and a mono eyebrow "QA Staffer · dossier" plus "port :<N> · model <M> · patrol every <P>m" caption, where `P` is `[modules.qa].patrol_interval_minutes`
  - **KPI strip**: four hairline-divided cells in this exact order:
    - `prs landed · 24h`
    - `mttr · 24h`
    - `self-resolved · 7d`
    - `active cases · now`
  - **Two-pane body** (320 px case-list rail on the left + dossier body on the right):
    - **Case list rail**: rule-separated rows; each row shows severity glyph (square, high/medium/low), short case id (e.g. `#218`), butler name, headline (one line), `detected` + `age` mono sub-line, and a PR-state dot on the right
    - **Dossier body** for the selected case (see Case Dossier Layout requirement)
- **AND** the page uses Inter Tight (sans), JetBrains Mono (mono), Source Serif 4 (serif), and the OKLCH palette tokens already shipped in `frontend/src/index.css` — no new tokens are introduced
- **AND** the page contains no card chrome, no drop shadows, no gradients, and no recharts components
- **AND** when no case is selected via `?case=`, the dossier body defaults to the most recent case in the rail (the rail's first row); when the rail is empty, the dossier body is hidden per the Empty case list scenario

#### Scenario: Empty case list
- **WHEN** the QA staffer has not produced any cases in the last 7 days
- **THEN** the case list rail renders a single serif-italic line "Nothing in the dossier." and the dossier body is hidden
- **AND** the KPI strip continues to render with zero values

#### Scenario: KPI strip values
- **WHEN** the KPI strip renders
- **THEN** each cell shows:
  - A mono uppercase eyebrow label (`prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `active cases · now`)
  - A large sans-500 tabular-nums numeric value
  - A mono sub-label describing context (`+2 vs prior 24h`, `−12m vs 7d`, `+4pp vs prior week`, `N awaiting CI · M escalated`, where N = `active_breakdown.awaiting_ci` and M = `active_breakdown.escalated_open_cases`)
- **AND** when MTTR is computed over an empty sample, the cell shows `—` and the sub-label reads `no terminal cases in 24h`

### Requirement: Patrol Detail Page
The dashboard SHALL have a drill-down page at `/qa/patrols/:patrolId` rendered in the Dispatch design language. It narrates a single patrol cycle as a rule-separated list with mono eyebrows, not as a tabular dashboard.

#### Scenario: Patrol detail layout
- **WHEN** a user navigates to `/qa/patrols/:patrolId`
- **THEN** the page renders:
  - **Header**: mono eyebrow "QA Patrol", H2 sans-500 22 px title containing the patrol's started-at timestamp, and a mono caption with duration, status, sources polled, and `log_lookback_minutes`
  - **Findings list**: rule-separated rows (no table), one per finding. Each row uses the grid `mark / id+butler / 1fr summary / meta`: severity glyph, mono fingerprint prefix + butler, anonymized `event_summary`, and a right-aligned mono "novel" badge or `dedup_reason`
  - **Dispatch summary**: rule-separated rows listing the findings that were dispatched as investigations, each linking to `/qa/investigations/:attemptId`
- **AND** the page reuses the same eyebrow / hairline / mono-numeral vocabulary as `/qa`

#### Scenario: Patrol not found
- **WHEN** the patrol ID does not exist
- **THEN** the page renders a single serif-italic line "Patrol not found." with the same chrome as the rest of `/qa/*`

### Requirement: Investigation Detail Page
The dashboard SHALL have a drill-down page at `/qa/investigations/:attemptId` that renders the same case dossier as `/qa?case=:attemptId`. The two routes mount the same `CaseDossier` component with no UX divergence.

#### Scenario: Investigation detail layout matches Case Dossier
- **WHEN** a user navigates to `/qa/investigations/:attemptId`
- **THEN** the page renders the Case Dossier layout (header + two-column diagnosis/PR + patrol journal) as defined by the Case Dossier Layout requirement
- **AND** the page includes a top eyebrow "QA Investigation · #<short_id>" and a back-link to `/qa`
- **AND** the existing `Retry` and `Dismiss` actions remain available as pill buttons in the dossier header

#### Scenario: Investigation not found
- **WHEN** the attempt ID does not exist or has no `qa_patrol_id`
- **THEN** the page renders a serif-italic "Investigation not found." with the same chrome as the rest of `/qa/*`

### Requirement: Home Page QA Widget
The main dashboard page (`/`) SHALL include a compact QA staffer summary surface alongside existing status cards. The widget uses the same Dispatch primitives.

#### Scenario: QA widget content
- **WHEN** the dashboard home page loads
- **THEN** the QA widget displays:
  - QA staffer status (mono caption: `running`, `tripped`, `stopped`)
  - Last patrol timestamp + outcome
  - `active cases · now` count
  - Click-through to `/qa`
- **AND** the widget contains no Kanban-style columns and no charts

#### Scenario: QA staffer not deployed
- **WHEN** the QA staffer is not running or has no patrol records
- **THEN** the widget shows a single serif-italic line "QA staffer not active"

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

#### Scenario: POST /api/qa/known-issues/{fingerprint}/dismiss
- **WHEN** `POST /api/qa/known-issues/{fingerprint}/dismiss` is called with the fingerprint in the path and an optional body `{ dismissed_until, dismissed_by }`
- **THEN** the fingerprint is added to the dismissal cache in `public.qa_dismissals` (`dismissed_until` defaults to a year-9999 sentinel when omitted; `dismissed_by` defaults to `"dashboard_user"`)
- **AND** returns `ApiResponse[QaDismissal]`

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

#### Scenario: DELETE /api/qa/dismissals/{fingerprint}
- **WHEN** `DELETE /api/qa/dismissals/{fingerprint}` is called
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
The QA page SHALL be accessible from the dashboard's main navigation. The nav-config entry already exists; the redesign does not change its position.

#### Scenario: Sidebar navigation entry
- **WHEN** the dashboard sidebar is rendered
- **THEN** a "QA" navigation item links to `/qa`
- **AND** it is grouped with other infrastructure items as configured in `frontend/src/components/layout/nav-config.ts`
- **AND** it shows a badge with the count of open QA escalations when > 0 (red variant; `badgeKey: 'qa-escalations'`, sourced from `active_breakdown.escalated_open_cases` in `GET /api/qa/summary`)

#### Scenario: Router registration
- **WHEN** the frontend router is configured
- **THEN** routes `/qa`, `/qa/patrols/:patrolId`, `/qa/investigations`, and `/qa/investigations/:attemptId` are registered
- **AND** all routes render within the `RootLayout`

### Requirement: Backward Compatibility with Healing API
The existing `/api/healing/` router SHALL continue to function unchanged. QA-originated investigations appearing in `healing_attempts` (with non-null `qa_patrol_id`) will be visible in existing healing endpoints — this is intentional and provides a unified view.

#### Scenario: Existing healing API shows QA investigations
- **WHEN** `GET /api/healing/attempts` is called
- **THEN** QA-originated investigations (qa_patrol_id IS NOT NULL) appear alongside per-butler self-healing attempts
- **AND** the `qa_patrol_id` field distinguishes them
- **AND** no changes to the existing healing API response schema are required (qa_patrol_id is an additive nullable field)

### Requirement: Case Dossier Layout
The QA dashboard SHALL render any single case (either as the right-pane on `/qa?case=` or as the full body of `/qa/investigations/:attemptId`) using a shared Case Dossier component composed of a header, a two-column diagnosis/PR body, and a full-width patrol journal.

#### Scenario: Dossier header
- **WHEN** the Case Dossier renders
- **THEN** the header row shows: severity glyph, mono `#<short_id>`, mono `· <butler>`, mono `· detected <HH:MM>`, and a right-aligned state track ("detect — diagnose — pr — landed" with an `escalated` variant)
- **AND** below the row, an H2 sans-500 22 px headline renders the case's `headline` (or `event_summary` as a fallback when `investigation_notes.headline` is null)

#### Scenario: Active dismissal display
- **WHEN** the Case Dossier renders a case whose fingerprint has an active dismissal record (`qa_dismissals` row with `dismissed_until > now()`)
- **THEN** the header renders a mono caption "dismissed until <dismissed_until>" beneath the sev/id/butler row
- **AND** a "remove dismissal" pill action is rendered alongside the existing `Retry` / `Dismiss` pills
- **AND** clicking "remove dismissal" calls `DELETE /api/qa/dismissals/{fingerprint}` and triggers a re-fetch of the case so the caption and pill disappear

#### Scenario: Diagnosis column
- **WHEN** the Case Dossier renders the left column
- **THEN** it renders these sections in order, each preceded by a mono uppercase eyebrow:
  - **Diagnosis** — serif paragraph rendered from `investigation_notes.blurb_segments`. Plain strings render as-is; anchored segments render the inner text plus a mono superscript `[N]` matching the claim's number-in-order-of-appearance
  - **Hypothesis** — single mono line rendered from `investigation_notes.hypothesis`
  - **Evidence · log fragments** — mono grid rows rendered from `investigation_notes.evidence_lines[]`; each row shows ts, level, butler, msg, and the bracketed claim numbers `[N]` it supports
  - **Considered & ruled out** — rendered from `investigation_notes.counter_evidence[]`; one row per entry showing hypothesis + reason + verdict
- **AND** when the user hovers a claim segment in the Diagnosis paragraph, every evidence row whose id appears in that claim's `evidence_ids[]` is visually highlighted; when the user hovers an evidence row, the claim segments referencing that row's id are highlighted (bidirectional linkage)

#### Scenario: Proposed fix column
- **WHEN** the Case Dossier renders the right column
- **THEN** it renders a PR panel (when `pr` is non-null) containing:
  - State chip (drafted/open/merged/closed) with appropriate state color from the Dispatch palette
  - Mono `pr #<number> · <state>`
  - Sans-500 14 px PR title
  - Mono caption with branch name, CI status, `+<additions> −<deletions>`
  - "Why this fix" eyebrow followed by a serif-italic 13 px line rendered from `investigation_notes.why_this_fix`
  - "Diff preview" eyebrow followed by a line-kind-aware diff renderer for `investigation_notes.diff_snapshot[]`
  - Mono footer caption with `opened <HH:MM>` and optional `· merged <HH:MM>`
- **AND** when `pr` is null, the column renders a single serif-italic line "No PR — escalated to user."

#### Scenario: Patrol journal section
- **WHEN** the Case Dossier renders the full-width patrol journal section
- **THEN** the section renders a mono row for each event from `/api/qa/cases/:id/journal`, in chronological order: ts (`HH:MM`), step name in step color (flagged/opened amber, sampled/cross-checked/drafted neutral, considered/wait/tick dim, concluded/merged green, escalated amber), text, and an optional dim detail line beneath
- **AND** an eyebrow above the section reads "Patrol journal · every QA decision on this case" with a right-aligned `<count> entries · patrol every <P>m` caption
- **AND** when the case has no journal events yet, the section is hidden entirely

### Requirement: QA Cases API
The dashboard API SHALL expose case-shaped resources under `/api/qa/cases` for the dossier renderer. The resources join `qa_findings`, `healing_attempts`, and `qa_investigation_events` into a per-case shape; they do not replace `/api/qa/investigations` (which retains its existing semantics).

#### Scenario: GET /api/qa/cases
- **WHEN** `GET /api/qa/cases` is called with optional `limit` (default 25), `sev` (`high|medium|low|all`), and `since` (`24h`, `7d`, `30d`, `all`; default `7d`)
- **THEN** it returns a paginated list of cases ordered by most recent first
- **AND** each case includes: `id` (UUID, the canonical attempt id), `short_id` (`#NNN` derived from id), `sev` (high/medium/low mapped from severity int), `butler`, `headline` (from `investigation_notes.headline` or fallback to the linked finding's `event_summary`), `detected` (earliest `qa_findings.first_seen`), `age_seconds`, `state` (one of: `detect`, `diagnose`, `pr`, `landed`, `escalated`), `pr_state` (drafted/open/merged/closed or null), `pr_url` (or null)

#### Scenario: GET /api/qa/cases/:id
- **WHEN** `GET /api/qa/cases/:id` is called with an attempt UUID
- **THEN** it returns the full dossier payload: the case summary, `state_track_stage`, `investigation_notes` (or null when no notes have been emitted yet), a `pr` summary block (or null), and the most recent 50 journal events
- **AND** when the attempt id does not exist, it returns the standard 404 envelope from RFC 0007

#### Scenario: GET /api/qa/cases/:id/journal
- **WHEN** `GET /api/qa/cases/:id/journal` is called with optional `cursor` and `limit` (default 50, max 500)
- **THEN** it returns a paginated chronological stream of `qa_investigation_events` for that case
- **AND** the response envelope is `PaginatedResponse[QaJournalEvent]`

#### Scenario: Cases API uses the standard envelopes
- **WHEN** any `/api/qa/cases*` endpoint responds
- **THEN** it uses the response envelopes defined by RFC 0007 (`ApiResponse<T>` for single, `PaginatedResponse<T>` for lists)

### Requirement: QA Summary KPI Extension
The `/api/qa/summary` endpoint SHALL include a `kpis` block computed from `healing_attempts` for the redesigned KPI strip. The block is additive — all pre-existing fields on the summary response remain unchanged.

#### Scenario: Summary KPI block
- **WHEN** `GET /api/qa/summary` is called
- **THEN** the response includes `kpis: { prs_landed_24h, mttr_24h_seconds, self_resolved_7d_pct, active_cases_now }`
- **AND** `prs_landed_24h` is the count of `healing_attempts` rows with `status = 'pr_merged'` AND `closed_at >= now() - 24 hours`
- **AND** `mttr_24h_seconds` is the average `closed_at - created_at` in seconds across `healing_attempts` rows with `closed_at >= now() - 24 hours` AND `status IN ('pr_merged','failed','timeout','unfixable')`; `null` when the sample is empty
- **AND** `self_resolved_7d_pct` is the float percentage `pr_merged / (pr_merged + unfixable + failed)` over `closed_at >= now() - 7 days`
- **AND** `active_cases_now` is the count of `healing_attempts` rows with `status IN ('dispatch_pending','investigating','pr_open')`
- **AND** the summary response also exposes an `active_breakdown` field: `{ awaiting_ci: int, escalated_open_cases: int }`. `awaiting_ci` is the count of `active_cases_now` rows with `status='pr_open'`. `escalated_open_cases` is the count of `healing_attempts` rows with `status IN ('unfixable','failed') AND failed_with_human_action(attempt) AND (closed_at IS NULL OR closed_at >= now() - 7 days)` — it is NOT a subset of `active_cases_now`; it counts terminal-but-unresolved cases that still need operator action
- **AND** the helper `failed_with_human_action(attempt) -> bool` is the single canonical detector of an "escalated" case (checks `attempt.status IN ('unfixable','failed')` plus a documented human-action substring on the TEXT `error_detail` column: any of `"human action"`, `"operator"`, `"escalat"` via case-insensitive match); both the KPI sub-label and the `state_of_case()` mapping use this helper

### Requirement: Investigations List Page
The dashboard SHALL render the case index at `/qa/investigations` using the Dispatch case index pattern: a rule-separated list of `QaCaseSummary` rows. The page does not render a Kanban or a tabular dashboard.

#### Scenario: Investigations list layout
- **WHEN** a user navigates to `/qa/investigations`
- **THEN** the page renders a sticky filter bar (state, severity, butler, time range) and a rule-separated list of cases
- **AND** each row uses the same grid as the `/qa` rail (severity glyph, mono short id, butler, headline, detected/age, PR-state dot)
- **AND** clicking a row navigates to `/qa/investigations/:attemptId` (which renders the same dossier as `/qa?case=`)

#### Scenario: Empty list
- **WHEN** no cases match the active filters
- **THEN** the page renders a single serif-italic line "Nothing matches." with no other chrome
