## MODIFIED Requirements

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

### Requirement: Navigation Integration
The QA page SHALL be accessible from the dashboard's main navigation. The nav-config entry already exists; the redesign does not change its position.

#### Scenario: Sidebar navigation entry
- **WHEN** the dashboard sidebar is rendered
- **THEN** a "QA" navigation item links to `/qa`
- **AND** it is grouped with other infrastructure items as configured in `frontend/src/components/layout/nav-config.ts`
- **AND** it shows a badge with the count of active investigations when > 0 (red variant; existing `qa-known-issues` badge contract preserved)

#### Scenario: Router registration
- **WHEN** the frontend router is configured
- **THEN** routes `/qa`, `/qa/patrols/:patrolId`, `/qa/investigations`, and `/qa/investigations/:attemptId` are registered
- **AND** all routes render within the `RootLayout`

## ADDED Requirements

### Requirement: Case Dossier Layout
The QA dashboard SHALL render any single case (either as the right-pane on `/qa?case=` or as the full body of `/qa/investigations/:attemptId`) using a shared Case Dossier component composed of a header, a two-column diagnosis/PR body, and a full-width patrol journal.

#### Scenario: Dossier header
- **WHEN** the Case Dossier renders
- **THEN** the header row shows: severity glyph, mono `#<short_id>`, mono `· <butler>`, mono `· detected <HH:MM>`, and a right-aligned state track ("detect — diagnose — pr — landed" with an `escalated` variant)
- **AND** below the row, an H2 sans-500 22 px headline renders the case's `headline` (or `event_summary` as a fallback when `investigation_notes.headline` is null)

#### Scenario: Active dismissal display
- **WHEN** the Case Dossier renders a case whose fingerprint has an active dismissal record (`qa_dismissals` row with `expires_at > now()`)
- **THEN** the header renders a mono caption "dismissed until <expires_at>" beneath the sev/id/butler row
- **AND** a "remove dismissal" pill action is rendered alongside the existing `Retry` / `Dismiss` pills
- **AND** clicking "remove dismissal" calls `DELETE /api/qa/dismissals/:fingerprint` and triggers a re-fetch of the case so the caption and pill disappear

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

## REMOVED Requirements

### Requirement: Known Issues Tracker
**Reason**: Folded into the Case Dossier. Known/open issues are now visible as cases in the `/qa` rail and in `/qa/investigations`. The dossier already exposes fingerprint, severity, occurrence, PR link, and dismiss action per case.
**Migration**: Operators looking for the previous "known issues" panel use the `/qa` case rail filtered by state (`detect`, `diagnose`, or `pr` — i.e. non-terminal) or `/qa/investigations?state=pr_open`.

### Requirement: State Persistence for Deduplication
**Reason**: Replaced by per-case visibility in the Case Dossier and the existing `/api/qa/dismissals` management endpoint. Active dismissals are surfaced inside the dossier header (when a dismissal exists, the dossier shows a "dismissed until <ts>" mono caption and a "remove dismissal" pill action). The dedicated "dismissals panel" requirement is no longer needed.
**Migration**: Operators use the per-case dismissal action in the Case Dossier or the existing `GET /api/qa/dismissals` + `DELETE /api/qa/dismissals/:fingerprint` endpoints (unchanged).
