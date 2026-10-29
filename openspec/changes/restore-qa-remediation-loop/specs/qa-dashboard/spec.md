## MODIFIED Requirements

### Requirement: QA Cases API
The dashboard API SHALL expose case-shaped resources under `/api/qa/cases` for the dossier renderer. The resources join `qa_findings`, `healing_attempts`, and `qa_investigation_events` into a per-case shape; they do not replace `/api/qa/investigations` (which retains its existing semantics).

ID: REQ-qa-dashboard-001
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: GET /api/qa/cases
- **WHEN** `GET /api/qa/cases` is called with optional `limit` (default 25), `sev` (`high|medium|low|all`), and `since` (`24h`, `7d`, `30d`, `all`; default `7d`)
- **THEN** it returns a paginated list of cases ordered by most recent first
- **AND** each case includes: `id` (UUID, the canonical attempt id), `short_id` (`#NNN` derived from id), `sev` (high/medium/low mapped from severity int), `butler`, `headline` (from `investigation_notes.headline` or fallback to the linked finding's `event_summary`), `detected` (earliest `qa_findings.first_seen`), `age_seconds`, `state` (one of: `detect`, `diagnose`, `pr`, `landed`, `escalated`, `failed`), `pr_state` (drafted/open/merged/closed or null), `pr_url` (or null), `healing_session_id` (or null), and `session_ids[]` (empty when no failing sessions were captured)
- **AND** `?state=failed` filters to terminal-crash attempts (`status IN ('timeout', 'anonymization_failed')`, or `status = 'failed'` without a human-action marker) — the same precedence `state_of_case()` uses, so the filter and the rendered badge never disagree

#### Scenario: GET /api/qa/cases/:id
- **WHEN** `GET /api/qa/cases/:id` is called with an attempt UUID
- **THEN** it returns the full dossier payload: the case summary, `state_track_stage`, derived `proposal_state` (`none`, `unpublished`, `published`, `ambiguous`, or `unavailable`), `proposal_diff_snapshot` (validated independently so a diff-only fallback remains visible), `investigation_notes` (or null when no notes have been emitted yet), a `pr` summary block (or null), the most recent 50 journal events, the attempt's `healing_session_id` (or null), its `session_ids[]` (empty when none), and `error_detail` (the sanitized bounded `healing_attempts.error_detail` text, or null — populated regardless of state, but only rendered by the UI's failure banner in the `failed` state)
- **AND** when the attempt id does not exist, it returns the standard 404 envelope from RFC 0007

#### Scenario: GET /api/qa/cases/:id/journal
- **WHEN** `GET /api/qa/cases/:id/journal` is called with optional `cursor` and `limit` (default 50, max 500)
- **THEN** it returns a paginated chronological stream of `qa_investigation_events` for that case
- **AND** the response envelope is `PaginatedResponse[QaJournalEvent]`

#### Scenario: Cases API uses the standard envelopes
- **WHEN** any `/api/qa/cases*` endpoint responds
- **THEN** it uses the response envelopes defined by RFC 0007 (`ApiResponse<T>` for single, `PaginatedResponse<T>` for lists)

#### Scenario: Truthful incident and attempt projection
- **WHEN** case list or detail is read
- **THEN** it exposes incident/generation linkage, execution owner, diagnostic outcome, evidence provenance, publication stage, deployment evidence and recovery verdict as distinct typed facts
- **AND** missing or contaminated notes fall back to the source finding with a visible limitation rather than the stale note headline
- **AND** unknown provider outcome remains explicitly ambiguous and never derives unpublished solely from a null PR URL
- **AND** pagination, filters, badges and detail use the same authoritative projection

### Requirement: QA Summary KPI Extension
The QA summary SHALL expose an evidence-qualified, time-windowed incident funnel that distinguishes observations, admitted investigations, proposals, published PRs, merges, deployment and verified recovery; it SHALL not label merge time as repair time.

ID: REQ-qa-dashboard-002
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Summary KPI block
- **WHEN** the operator opens QA for a named time window
- **THEN** each counter declares whether it counts observations, distinct incident generations, attempts or PRs and supplies its denominator and observation boundary
- **AND** initial and follow-up session duration/token totals join through trusted QA lineage with model and cache counters retained separately
- **AND** absent billing or incomplete session linkage is unknown with coverage, never zero cost or a fabricated dollar estimate
- **AND** existing unrelated summary health/configuration fields remain available through the same endpoint

#### Scenario: Complete summary producer and consumer contract
- **WHEN** the summary is generated for a selected window
- **THEN** funnel stages are observations, admitted_incidents, proposals, published_prs, merged_prs, deployed_incidents, verified_recoveries and failed_executions; each has unit, count-or-null, window and complete/partial/unavailable coverage
- **AND** failed_executions counts distinct launched sessions with actual failed/timeout execution and ended_at in the window, excluding prelaunch rejection, publication-only failure and completed-but-inconclusive diagnosis
- **AND** active_cases counts distinct incident generations with nonterminal attempts or actionable pending work at observation time; active_breakdown.awaiting_ci counts those with a published PR and pending required checks, and action_needed counts those with a recorded unresolved operator disposition
- **AND** recovery_rate is recovered deployed generations divided by eligible deployed generations in the same deployment-time cohort; an empty denominator is null, never zero percent
- **AND** the five cells consume merged_prs, verified_recoveries, recovery_rate, active_cases and failed_executions, with current/prior equally sized cohorts for comparisons and no reads of retired mttr/self_resolved fields
- **AND** event-time stages name their boundary: observation captured_at, incident admitted_at, immutable proposal sealed_at, PR created_at, merged_at, deployment observed_at, recovery verified_at and session ended_at
- **AND** usage joins trusted initial/follow-up session lineage, reports covered/expected session counts and separate input/output/cache counters by reported model, and leaves absent billing/model/linkage as unknown

#### Scenario: Recovery requires deployment and observation evidence
- **WHEN** a PR is merged but deployment is unknown, a source is partial, or no successful relevant observation exists
- **THEN** QA displays merged or recovery-unverified rather than repaired
- **AND** verified recovery requires the matching deployed revision, a successful safe regression check against that deployment or complete source coverage containing a qualifying recurrence opportunity of the failed operation, and the recorded check/workload, source generation and bounded interval; an idle empty patrol is insufficient
- **AND** renewed failure creates a linked regression generation and removes any claim of current health without rewriting the prior recovery receipt

#### Scenario: Operator can inspect uncertainty and act deliberately
- **WHEN** a case is inconclusive, design-required, publication-blocked or legacy-contaminated
- **THEN** the existing dossier gives a concise reason and keyboard-accessible links to source evidence, attempts, proposal and the applicable owner action
- **AND** unsupported retry controls remain absent or honestly disabled and no action implies permission to merge or publish
- **AND** badges and limitations use accessible text in the Dispatch design language, not color alone

#### Scenario: Circuit breaker threshold and runtime-credential-alert on the wire
- **WHEN** summary reads breaker and credential-attention projections
- **THEN** it retains the configured breaker threshold and existing credential attention signal with observation freshness and unavailable state
- **AND** failed reads cannot appear as a healthy null credential signal

### Requirement: QA Overview Page
The dashboard SHALL have a top-level QA page at route `/qa` that presents the QA staffer's work as a per-case dossier in the Dispatch design language. The page narrates what the staff caught and what it fixed; it is not a pipeline dashboard.

ID: REQ-qa-dashboard-003
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Overview page layout
- **WHEN** a user navigates to `/qa`
- **THEN** the page renders, in vertical order:
  - **Sticky top bar**: severity filter (all / high / medium / low) and theme toggle, shared with `/overview` and `/butlers`
  - **Page header**: title "What the staff caught and fixed", clock (mono, `HH:MM` 24h, tabular nums), and a mono eyebrow "QA Staffer · dossier" plus "port :<N> · model <M> · patrol every <P>m" caption, where `P` is `[modules.qa].patrol_interval_minutes`
  - **KPI strip**: five hairline-divided cells in this exact order:
    - `prs landed · 24h`
    - `verified recoveries · 24h`
    - `recovery verified · 7d`
    - `active cases · now`
    - `failed · 24h`
  - **Two-pane body** (320 px case-list rail on the left + dossier body on the right):
    - **Case list rail**: rule-separated rows; each row shows severity glyph (square, high/medium/low), short case id (e.g. `#218`), butler name, headline (one line), `detected` + `age` mono sub-line, a PR-state dot on the right, and a compact session-trace door when linked session evidence exists
    - **Dossier body** for the selected case (see Case Dossier Layout requirement)
- **AND** the page uses Inter Tight (sans), JetBrains Mono (mono), Source Serif 4 (serif), and the OKLCH palette tokens already shipped in `frontend/src/index.css` — no new tokens are introduced
- **AND** the page contains no card chrome, no drop shadows, no gradients, and no recharts components
- **AND** when no case is selected via `?case=`, the dossier body defaults to the most recent case in the rail (the rail's first row); when the rail is empty, the dossier body is hidden per the Empty case list scenario

#### Scenario: Empty case list
- **WHEN** the QA staffer has not produced any cases in the last 7 days
- **THEN** the case list rail renders a single serif-italic line "Nothing in the dossier." and the dossier body is hidden
- **AND** the KPI strip renders supported zero counts and explicit unknown values for missing evidence or empty denominators

#### Scenario: Case-list session trace door
- **WHEN** a case-list row has a non-null `healing_session_id` and/or a non-empty `session_ids[]`
- **THEN** it renders a compact, labeled navigation door to `/sessions/:id`, targeting the investigation session when present and otherwise the first failing session
- **AND** the door visibly states the number of distinct linked session traces and remains a sibling control to the row-selection button, so both controls have independent keyboard and screen-reader semantics
- **AND** when `healing_session_id` is null and `session_ids` is empty, no session-trace door is rendered

#### Scenario: Patrol pulse strip degraded source
- **WHEN** the overview page's recent-patrols pulse strip queries `GET /api/qa/patrols` and that query errors (network failure or non-2xx)
- **THEN** the strip renders a single-line `role="alert"` degraded note naming the patrols source inline with an em-dash qualifier (e.g. "Recent patrols: patrol source unreachable — recent patrols unavailable"), never vanishing into the same nothing as a genuinely clear stream
- **AND** a reachable-but-empty patrols source (no error, zero patrols) legitimately hides the strip, as does the still-loading state (no fabricated strip before the first response)

#### Scenario: KPI strip values
- **WHEN** the KPI strip renders
- **THEN** each cell shows:
  - A mono uppercase eyebrow label (`prs landed · 24h`, `verified recoveries · 24h`, `recovery verified · 7d`, `active cases · now`, `failed · 24h`)
  - A large sans-500 tabular-nums numeric value
  - A mono sub-label describing context (`+2 vs prior 24h`, `2 verified · 3 unverified`, `+4pp vs prior week`, `N awaiting CI · M escalated`, where N = `active_breakdown.awaiting_ci` and M = `active_breakdown.action_needed`)
- **AND** when recovery evidence or its denominator is unavailable, the cell shows `—` with the missing evidence named; a measured zero verified-recovery count remains zero
- **AND** the `failed · 24h` cell renders `funnel.failed_executions.count` with a destructive (red) tint on the numeric value only when the count is greater than zero — a zero-crash 24h stays neutral, never manufacturing alarm

#### Scenario: Force-patrol control surfaces the honest outcome
- **WHEN** a user activates the "Force patrol" control in the sticky top bar and confirms the prompt
- **THEN** the dashboard calls `POST /api/qa/force-patrol` and branches the toast on the response's `triggered` flag, not on the HTTP 202 accept alone (the endpoint accepts the request even when no patrol runs):
  - `triggered: true` → a success toast carrying the response `message`
  - `triggered: false` or absent → a warning toast carrying the response `message`, which names why no patrol ran (e.g. the QA daemon is unreachable, or a cycle is already in progress) — never a success toast
- **AND** a transport error (non-2xx / network failure) renders an error toast naming the failure

#### Scenario: Circuit-breaker control renders an honest tri-state
- **WHEN** the sticky top bar renders the QA circuit-breaker control, deriving its state from the `/api/qa/summary` query that feeds it
- **THEN** the control renders exactly one of three states, never defaulting a dead or in-flight feed to the calm "closed":
  - **closed** — the summary loaded and `circuit_breaker.tripped` is `false`: a muted, disabled "Circuit breaker closed" control (aria-label `QA circuit breaker closed`)
  - **tripped** — the summary loaded and `circuit_breaker.tripped` is `true`: an enabled destructive "Reset breaker" control (aria-label `Reset QA circuit breaker`)
  - **unknown** — the summary query is loading or errored: an amber, disabled "Circuit breaker unknown" control (aria-label `QA circuit breaker state unknown`); reset is withheld because trippedness is not proven
- **AND** the same tri-state (closed / tripped / **unknown** on `isError`) governs the butler-detail QA tab's `CircuitBreakerChip`, which derives from `GET /api/qa/circuit-breaker`

#### Scenario: Reset confirm shows the failing attempts as evidence
- **WHEN** a user activates the "Reset breaker" control while the breaker is tripped
- **THEN** the dashboard opens a confirm dialog (not a bare browser prompt) that lists the contributing session-completion events from `GET /api/qa/circuit-breaker` (`recent_attempts` carries attempt id, session id, initial/follow-up phase, typed contribution/reason and relative session ended_at), the evidence the breaker tripped on, before any reset is issued
- **AND** confirming issues `POST /api/qa/circuit-breaker/reset` and reports the outcome via toast; cancelling (or dismissing) issues no request
- **AND** when the circuit-breaker source is unreachable, the dialog names the missing evidence inline rather than presenting an empty confirm as calm

#### Scenario: Verdict opener names overdue patrols, pre-trip failure streaks, and watcher-death
- **WHEN** the page opener (`QaVerdictOpener`, composed from `GET /api/qa/summary` via the shared `DispatchVerdict` primitive) renders
- **THEN**, in addition to the existing breaker-tripped / last-patrol-failed / no-patrol-history / credential-presence clauses, it evaluates three more, all independent of each other and of the breaker-tripped state:
  - **Pre-trip failure streak** — when `circuit_breaker.consecutive_failures > 0` AND the breaker is not yet tripped, a clause names the streak and the trip threshold (`"N consecutive failures — breaker opens at <threshold>"`), naming the problem before it becomes a full trip rather than only after
  - **Overdue patrol** — when `last_patrol_at` is older than `2 × patrol_interval_minutes` (both already present on the summary response), a clause reads `"patrol overdue — last patrol <relative time>"`; entirely client-computable, no backend change
  - **Runtime-credential alert** — when `runtime_credential_alert` is non-null, a clause reads `"runtime CLI credential may be unhealthy — <alert text>"` — the watcher-death signal: `staffer_status` can read `healthy` while the QA staffer's own model dispatch is dying on a revoked/expired credential, since `staffer_status` only ever inspects patrol-run status and the healing circuit breaker

#### Scenario: Reset is a command-palette verb while tripped
- **WHEN** the QA overview page is mounted and the breaker is tripped
- **THEN** a "Reset circuit breaker" command is registered in the command palette's Actions group (alongside the always-present "Force patrol" verb), opening the same evidence-bearing confirm dialog
- **AND** the command is NOT registered while the breaker is closed or unknown — trippedness must be proven before the reset verb is offered

#### Scenario: Case stages are evidence rather than inferred repair
- **WHEN** a case has reached merge but lacks matching deployment and recovery evidence
- **THEN** the rail and dossier retain the merge fact and explicitly show recovery as unverified
- **AND** a typed inconclusive result says inconclusive rather than claiming the investigation crashed

### Requirement: Investigation Detail Page
The dashboard SHALL have a drill-down page at `/qa/investigations/:attemptId` that renders the same case dossier as `/qa?case=:attemptId`. The two routes mount the same `CaseDossier` component with no UX divergence.

ID: REQ-qa-dashboard-004
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Investigation detail layout matches Case Dossier
- **WHEN** a user navigates to `/qa/investigations/:attemptId`
- **THEN** the page renders the Case Dossier layout (header + two-column diagnosis/PR + patrol journal) as defined by the Case Dossier Layout requirement
- **AND** the page includes a top eyebrow "QA Investigation · #<short_id>" and a back-link to `/qa`
- **AND** Dismiss and remove-dismissal remain available in their eligible states; no Retry action, client helper or mutation hook is exposed

#### Scenario: Investigation not found
- **WHEN** the attempt ID does not exist or has no `qa_patrol_id`
- **THEN** the page renders a serif-italic "Investigation not found." with the same chrome as the rest of `/qa/*`

### Requirement: Case Dossier Layout
The QA dashboard SHALL render any single case (either as the right-pane on `/qa?case=` or as the full body of `/qa/investigations/:attemptId`) using a shared Case Dossier component composed of a header, a two-column diagnosis/PR body, and a full-width patrol journal.

ID: REQ-qa-dashboard-005
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Dossier header
- **WHEN** the Case Dossier renders
- **THEN** the header row shows: severity glyph, mono `#<short_id>`, mono `· <butler>`, mono `· detected <HH:MM>`, and a right-aligned state track ("detect — diagnose — pr — landed" with an `escalated` variant and a `failed` variant)
- **AND** the `escalated` variant renders an amber `· escalated` suffix with `pr`/`landed` shown in amber (handed to the user, no fix); the `failed` variant renders a destructive (red) `· failed` suffix with `pr`/`landed` shown in the destructive color (execution failed or the result was inconclusive, identified explicitly by typed outcome) — the two terminal stops are visually distinct so a dead investigation is never mistaken for a deliberate escalation
- **AND** below the row, an H2 sans-500 22 px headline renders the validated current diagnosis headline, falling back to the source event_summary when notes are missing, rejected, contaminated or legacy-unverified
- **AND** the case-list rail marks a `failed`-state row with a destructive `· failed` suffix on its detected/age sub-line, alongside the existing severity glyph and PR-state dot

#### Scenario: Active dismissal display
- **WHEN** the Case Dossier renders a case whose fingerprint has an active dismissal record (`qa_dismissals` row with `dismissed_until > now()`)
- **THEN** the header renders a mono caption "dismissed until <dismissed_until>" beneath the sev/id/butler row
- **AND** a "remove dismissal" pill action is rendered alongside the eligible Dismiss action, without a Retry control
- **AND** clicking "remove dismissal" calls `DELETE /api/qa/dismissals/{fingerprint}` and triggers a re-fetch of the case so the caption and pill disappear

#### Scenario: Terminal history has neither Retry nor Dismiss
- **WHEN** the case's derived `stage` is terminal (`landed`, `escalated`, or `failed`)
- **THEN** neither Retry nor Dismiss is rendered; terminal history is not a false-positive dismissal candidate
- **AND** every other stage (`detect`, `diagnose`, `pr`) offers Dismiss when a fingerprint is linked and no dismissal is active
- **AND** every stage withholds Retry, while existing dismissal removal and history remain available

#### Scenario: Failure banner quotes the crash
- **WHEN** the Case Dossier renders a case whose `state_track_stage` is `failed`
- **THEN** the diagnosis column opens with a destructive (red) serif-italic banner and a bounded sanitized error or inconclusive-reason block appropriate to the typed outcome
- **AND** a failed case with `proposal_state = "unpublished"` says that a local proposal was produced but publication failed; other failed cases use outcome-specific copy, never labeling an inconclusive result as a crash
- **AND** the Diagnosis/Hypothesis/Evidence/Considered sections beneath it fall back to "No investigation notes were captured for this case." (the same fallback `pr`/`landed`/`escalated` use) rather than the in-flight "Diagnosing…" copy, since a `failed` case is terminal, not still working
- **AND** a failed case with no retained proposal renders a typed failure or inconclusive explanation in the destructive color without asserting a human handoff or a proven absence of remote effects from a missing PR URL alone

#### Scenario: Diagnosis column
- **WHEN** the Case Dossier renders the left column
- **THEN** it renders these sections in order, each preceded by a mono uppercase eyebrow:
  - **Diagnosis** — serif paragraph rendered from `investigation_notes.blurb_segments`. Plain strings render as-is; anchored segments render the inner text plus a mono superscript `[N]` matching the claim's number-in-order-of-appearance
  - **Hypothesis** — single mono line rendered from `investigation_notes.hypothesis`
  - **Evidence · log fragments** — mono grid rows rendered from `investigation_notes.evidence_lines[]`; each row shows ts, level, butler, msg, and the bracketed claim numbers `[N]` it supports
  - **Considered & ruled out** — rendered from `investigation_notes.counter_evidence[]`; one row per entry showing hypothesis + reason + verdict
- **AND** for `proposal_state = "unpublished"`, the diagnosis uses the root-cause `hypothesis` and does not render agent-authored `blurb_segments` that may describe proposed code in present tense
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
- **AND** when `pr` is null, the column renders typed publication/diagnosis state: ambiguous or unavailable remote outcome stays unknown, inconclusive stays inconclusive, and escalation appears only with recorded operator disposition
- **AND** when `proposal_state = "unpublished"` is proven by the trusted publisher receipt, the column labels the artifact `Local proposal` and states that publication did not occur, and retains `why_this_fix` plus the diff under proposal-specific labels

#### Scenario: Session trace doors
- **WHEN** the Case Dossier renders a case whose attempt carries a `healing_session_id` and/or a non-empty `session_ids[]`
- **THEN** it renders a "Session trace" section with a real navigation door (a react-router `<Link>` to `/sessions/:id`, not a dead onClick) for the investigation session (`healing_session_id`) and one door per failing session in `session_ids[]`
- **AND** when `healing_session_id` is null AND `session_ids` is empty, the section is not rendered at all — no empty header and no broken link

#### Scenario: Patrol journal section
- **WHEN** the Case Dossier renders the full-width patrol journal section
- **THEN** the section renders a mono row for each event from `/api/qa/cases/:id/journal`, in chronological order: ts (`HH:MM`), step name in step color (flagged/opened amber, sampled/cross-checked/drafted neutral, considered/wait/tick dim, concluded/merged green, escalated amber), text, and an optional dim detail line beneath
- **AND** an eyebrow above the section reads "Patrol journal · every QA decision on this case" with a right-aligned `<count> entries · patrol every <P>m` caption
- **AND** when `proposal_state = "unpublished"`, a `concluded` event's agent-authored proposal detail is replaced with the factual projection `Proposal remained unpublished.` so proposed behavior cannot read as deployed
- **AND** when the case has no journal events yet, the section is hidden entirely

### Requirement: QA API Endpoints
The dashboard API SHALL expose endpoints under `/api/qa/` to support the frontend pages and provide state persistence for deduplication tracking. All endpoints SHALL use the standard response envelopes from RFC 0007: `ApiResponse<T>` for single-item responses, `PaginatedResponse<T>` for lists, `ErrorResponse` for errors.

ID: REQ-qa-dashboard-006
Source: qa-triage; authenticated actor policy; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

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
- **THEN** it returns all active/open issues: healing attempts with nonterminal status in (`investigating`, `proposal_ready`, `pr_open`, `held`), plus separately persisted pending incident work; dispatch_pending is not an attempt status
- **AND** grouped by fingerprint with occurrence count, affected butlers, and PR info where available

#### Scenario: POST /api/qa/known-issues/{fingerprint}/dismiss
- **WHEN** `POST /api/qa/known-issues/{fingerprint}/dismiss` is called with the fingerprint in the path and an optional body `{ dismissed_until }`; legacy caller-asserted attribution is ignored
- **THEN** the fingerprint is added to the dismissal cache in `public.qa_dismissals` (`dismissed_until` defaults to a year-9999 sentinel when omitted; `dismissed_by` is derived from authenticated_principal())
- **AND** returns `ApiResponse[QaDismissal]`

#### Scenario: POST /api/qa/force-patrol
- **WHEN** `POST /api/qa/force-patrol` is called
- **THEN** the endpoint accepts the request (HTTP 202) and attempts to trigger a patrol cycle immediately, regardless of the schedule
- **AND** returns `ApiResponse` wrapping `{ triggered, message }`:
  - `triggered` is the honest signal for whether a cycle was actually kicked off (in-process or via the QA daemon MCP tool); it is `false` when no patrol could be started (no in-process callable AND no reachable daemon, or a cycle is already running)
  - `message` names the outcome (why a patrol did or did not run)

#### Scenario: GET /api/qa/circuit-breaker
- **WHEN** `GET /api/qa/circuit-breaker` is called
- **THEN** it returns `{ tripped, threshold, recent_statuses, recent_attempts }` derived from the canonical ordered QA session-completion breaker projection after the latest reset cutoff, using configured threshold (default 5); recent_attempts links the contributing initial/follow-up sessions and typed reasons, not PR-closure timestamps

#### Scenario: POST /api/qa/circuit-breaker/reset
- **WHEN** `POST /api/qa/circuit-breaker/reset` is called while the breaker is tripped
- **THEN** it records one auditable row in `public.breaker_resets` (`breaker='qa'`, `reset_by`, `reset_at`, optional `reason`) and returns `{ reset: true, message }`
- **AND** it SHALL NOT fabricate history — no synthetic `qa_patrols` row and no synthetic `healing_attempts` row are created
- **AND** the dispatch-admission gate and every dashboard breaker query fold the same canonical session-completion events after the latest reset cutoff, so the breaker admits new dispatches while the real failure history (patrols, attempts, all-time stats, and the derived `staffer_status`) remains visible and un-fabricated

#### Scenario: Breaker reset while not tripped
- **WHEN** `POST /api/qa/circuit-breaker/reset` is called while the breaker is **not** tripped
- **THEN** it is a no-op and returns `{ reset: false, message }` without writing a `breaker_resets` row

#### Scenario: GET /api/qa/trends
- **WHEN** `GET /api/qa/trends` is called with `days` parameter (default: 7)
- **THEN** it returns daily aggregated stats: `[{ date, patrols, findings, novel, dispatched, prs_opened, prs_merged, success_rate, by_source: { source_type: count } }]`

#### Scenario: GET /api/qa/dismissals
- **WHEN** `GET /api/qa/dismissals` is called
- **THEN** it returns all active dismissals (dismissed_until > now()) for management

#### Scenario: DELETE /api/qa/dismissals/{fingerprint}
- **WHEN** `DELETE /api/qa/dismissals/{fingerprint}` is called
- **THEN** the dismissal for that fingerprint is removed
- **AND** subsequent patrol cycles reconsider findings under all current admission gates; removal does not reset incident budget or unchanged-outcome suppression
