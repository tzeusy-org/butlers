## Context

The existing `dashboard-overview` spec was authored for a previous Overview
direction where a session stripe chart was the first and loudest artifact. The
current doctrine is different:

- `about/heart-and-soul/design-language.md` defines the Overview as the first
  editorial archetype consumer: system-spoken Voice, status pill, attention
  list, KPI strip, and a right-column index.
- `about/lay-and-land/frontend.md` defines the editorial frame:
  `<Page archetype="editorial">`, two columns (`1.4fr / 1fr`), left-column
  narrative, and right-column scan lists.
- `openspec/changes/dashboard-overview-briefing/` defines the existing
  `GET /api/dashboard/briefing` endpoint and explicitly defers page
  restructuring to a separate change.
- `frontend/src/pages/DashboardPage.tsx` already points at the editorial shape:
  `useBriefing`, `useIssues`, `useButlers`, `useCostSummary("today")`, and
  `useApprovalMetrics()`.

This change is that missing page-level reconciliation. It does not bless every
detail of the current implementation as final; it defines the intended behavior
so implementation beads have a stable target.

## Decisions

### D1: Create a new page-level change instead of extending `dashboard-overview-briefing`

`dashboard-overview-briefing` owns a backend/API capability: the server-side
briefing object and local-runtime elaboration path. It is intentionally narrow
and endpoint-first.

The triage cockpit is page composition. Keeping it in a separate
`dashboard-overview-triage-cockpit` change prevents the briefing endpoint
contract from becoming a grab bag of frontend layout obligations.

### D2: Supersede the chart-first Overview hierarchy

The session stripe chart, recent moments feed, secondary alert card grid, QA
widget, and demoted stat strip are no longer the normative Overview hierarchy.
They are removed from `dashboard-overview` by this delta.

The replacement hierarchy is:

1. Editorial briefing surface.
2. `Needs attention` list.
3. Promoted runtime KPI strip.
4. Right-column `Operations` list.
5. Right-column `Now` list.

This follows the settled design-language rule that the Overview is a read-first
observability surface and that the system may speak in sentences only on the
editorial archetype.

### D3: Existing endpoint sources are sufficient for the first implementation

The page should not start by adding an aggregation endpoint. The intended first
implementation uses:

| Surface | Existing source |
|---|---|
| Briefing | `GET /api/dashboard/briefing` via `useBriefing()` |
| Needs attention | `GET /api/issues` via `useIssues()` |
| KPI: total butlers | `GET /api/butlers` via `useButlers()` |
| KPI: healthy butlers | `GET /api/butlers` via `useButlers()` |
| KPI: sessions · 24h | `GET /api/butlers` `sessions_24h` via `useButlers()` |
| KPI: pending approvals | `GET /api/approvals/metrics` via `useApprovalMetrics()` |
| Operations: butler sessions/status | `GET /api/butlers` via `useButlers()` |
| Operations: cost today | `GET /api/costs/summary?period=today` via `useCostSummary("today")` |
| Now: pending approvals | `GET /api/approvals/metrics` via `useApprovalMetrics()` |
| Now: QA state | `GET /api/qa/summary` via `useQaSummary()` and, if active PR/investigation detail is needed, `GET /api/qa/investigations` |
| Now: failed notification pressure | `GET /api/notifications/stats` via `useNotificationStats()` |
| Now: recent activity | `GET /api/timeline` via `useTimeline()` or `GET /api/sessions` via `useSessions()` |

If the eventual `Now` list needs scheduled-task, reminder, or calendar detail,
that follow-up may use existing calendar/scheduler surfaces or propose a new
endpoint. It is not justified by the current Overview scope.

### D4: Stale issue summarization is client-side over `Issue`

`GET /api/issues` already returns the fields needed to make the attention list
operator-readable: `severity`, `type`, `butler`, `description`, `link`,
`error_message`, `occurrences`, `first_seen_at`, `last_seen_at`, and optional
`butlers`.

The page should order and summarize issues client-side:

- severity order: high/critical/error, then medium/warning/warn, then the rest;
- within severity, older unresolved issues before newer issues when
  `first_seen_at` exists, because unresolved age is what makes an item stale;
- items older than 24 hours should expose age in the detail line, calculated
  relative to the owner's configured timezone;
- repeated old items with the same `type` and `description` may collapse into a
  single row when `occurrences` or `butlers` shows multiplicity;
- empty state is the serif Voice line `Nothing waiting.`.

This is a presentation rule over existing payloads, not a new backend grouping
contract.

### D5: Promoted KPIs are operational summary, not decoration

The KPI strip is promoted because it gives the owner the fastest answer to
"is the system alive and doing work." The four cells are:

- `Total butlers`: count of `GET /api/butlers` rows where `type === "butler"`.
- `Healthy`: count of butler rows whose `status` is `ok` or `online`.
- `Sessions · 24h`: sum of `sessions_24h` across butler rows.
- `Pending approvals`: `total_pending` from `GET /api/approvals/metrics`.

The strip remains hairline-divided and tabular. Promoted here means promoted in
information architecture, not wrapped in heavier card chrome.

### D6: `Now` starts with current operator signals, not a new endpoint

`Now` is the right-column place for immediate operational work and recent
movement. The first implementation should compose small rows from existing
sources:

- pending approval count from `GET /api/approvals/metrics`;
- QA alert state from `GET /api/qa/summary`, with active investigation or PR
  detail from `GET /api/qa/investigations` only when the row needs it;
- failed notification count from `GET /api/notifications/stats`;
- recent activity from `GET /api/timeline`, or `GET /api/sessions` when the
  implementation only needs recent completed sessions.

The page may omit a row when its source reports no actionable state. That is
different from hiding failures: source failures render as local `Now` error
rows so the owner can see which signal is unavailable.

### D7: Loading and failure states stay local to surfaces

The Overview should never blank the whole page because one supporting query
failed. Each surface owns its own state:

- briefing falls back per `dashboard-briefing`;
- attention list renders loading rows, an error row, an empty Voice line, or
  attention rows;
- KPI cells render unavailable glyphs when one source is still loading or has
  failed;
- Operations renders a loading/error/empty list state;
- Now renders pending items, `Nothing scheduled.`, or an error state.

This keeps the owner-oriented page readable during partial backend degradation.

## Risks / Trade-offs

- **R1: Client-side issue summarization can drift.** Mitigation: keep the rules
  simple, documented here, and test them in the frontend follow-up.
- **R2: No new aggregation endpoint means multiple queries on page load.**
  Mitigation: the current hooks already poll at modest intervals, and the page
  is a dashboard operator surface where explicit source contracts matter more
  than premature consolidation.
- **R3: `Now` starts sparse.** Pending approvals are enough to define the row
  anatomy and empty states. Richer schedule/calendar items need a separate
  source-of-truth decision.

## Migration Plan

1. Land this spec-only reconciliation.
2. Implement the Overview page/components to match the new delta using existing
   hooks and endpoints only.
3. Add frontend tests for hierarchy, data-source usage, stale issue
   summarization, and empty/loading/error states.
4. After the implementation lands and validates, archive this change so
   `openspec/specs/dashboard-overview/spec.md` no longer contains the
   chart-first contract.
