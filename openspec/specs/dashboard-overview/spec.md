# dashboard-overview Specification

## Purpose

Defines the information hierarchy and content contract for the Butlers dashboard home
page at `/`. The home page is the owner's primary health-at-a-glance view: it answers
"is the system working right now?" rather than structural questions ("what is connected?").
It is the editorial triage cockpit — the system speaking, naming what needs attention,
and a quiet operational index. This spec establishes what surfaces the page renders, their
visual priority order, and the data sources and component boundaries each surface must
satisfy. It does not specify visual design tokens, pixel-level layout, or API
implementation details; those are governed by `about/heart-and-soul/design-language.md`,
`dashboard-api`, and the `<Page>` archetype contract respectively.

## Requirements

### Requirement: Home Page Information Hierarchy

The home page at `/` SHALL render the editorial triage cockpit for the owner.
It SHALL use the editorial archetype defined by
`about/heart-and-soul/design-language.md` and
`about/lay-and-land/frontend.md`: a two-column page where the left column is the
system speaking and naming attention, and the right column is a quiet operational
index.

The page SHALL render these surfaces:

1. **Briefing**: the Voice surface rendered from `GET /api/dashboard/briefing`.
2. **Needs attention**: a rule-separated attention list derived from
   `GET /api/issues`.
3. **Runtime KPI strip**: four promoted operational KPIs derived from
   `GET /api/butlers` and `GET /api/approvals/metrics`.
4. **Operations**: right-column butler scan list derived from `GET /api/butlers`
   and `GET /api/spend/summary?period=today`.
5. **Now**: right-column immediate operational items derived from existing
   approval, QA, notification, and activity endpoints.

The session stripe chart SHALL NOT be the primary region of the Overview page.
No chart-first or card-grid requirement SHALL outrank the briefing, attention
list, or KPI strip on `/`.

#### Scenario: Editorial cockpit renders instead of chart-first hierarchy

- **WHEN** a user navigates to `/`
- **THEN** `DashboardPage` renders inside `<Page archetype="editorial" title="Overview">`
- **AND** the page presents the briefing, `Needs attention`, runtime KPI strip,
  `Operations`, and `Now` surfaces
- **AND** no session stripe chart is required as the first or dominant region
- **AND** the Overview does not require the old five-region chart-first order
  (session chart, recent moments, secondary cards, QA widget, supporting strip)

#### Scenario: Existing endpoint data sources are sufficient

- **WHEN** the Overview composes its cockpit surfaces
- **THEN** it uses the existing endpoint families named in this requirement
- **AND** it SHALL NOT introduce a new Overview aggregation endpoint unless a
  separate OpenSpec change justifies why the existing dashboard sources cannot
  supply the required state

### Requirement: Briefing Voice Surface

The home page SHALL render the existing dashboard briefing as the first
left-column surface. The briefing wire contract is owned by
`dashboard-briefing`; `dashboard-overview` owns only the page composition that
consumes it.

#### Scenario: Briefing uses the six-field briefing response

- **WHEN** the briefing query succeeds
- **THEN** the page renders `greet`, `headline`, `elaboration`, `source`,
  and `generated_at` from `GET /api/dashboard/briefing` (the `state_class`
  field is a server-side classifier input and is not rendered by the page)
- **AND** the page does not require or render additional machine provenance
  fields from that endpoint

#### Scenario: Briefing handles loading and fallback states

- **WHEN** the briefing query is fetching
- **THEN** the status pill names the in-flight state
- **AND** the Voice paragraph area remains stable

- **WHEN** the endpoint returns `source = "fallback"`
- **THEN** the page renders the fallback paragraph without treating fallback as
  an error state

### Requirement: Needs Attention List

The home page SHALL render a `Needs attention` list composed from several
sources: `GET /api/issues`, the canonical butler liveness verdict, pending
approvals, notification delivery pressure, and QA staffer state. The list is
a rule-separated attention surface, not a card grid or table.

#### Scenario: Attention rows are derived from active issues

- **WHEN** `GET /api/issues` returns one or more `Issue` objects
- **THEN** each row shows severity mark, issue description, butler/source detail,
  optional error context, and a link when `link` is present
- **AND** within a severity tier, older unresolved issues sort before newer
  issues when `first_seen_at` exists

#### Scenario: Attention rows are severity-first and stable across kinds

- **WHEN** the attention list composes rows from more than one source kind
  (issue, runtime/liveness, approval, notification, qa)
- **THEN** the full list is ordered by severity first — critical, then
  high/error, then medium/warning/warn, then low, then all other
  severities — across ALL kinds, not grouped by kind first
- **AND** a higher-severity row from one kind (e.g. a tripped QA circuit
  breaker) SHALL rank above a lower-severity row from another kind (e.g. a
  medium-severity issue), even if that other kind is normally rendered
  earlier
- **AND** rows tied on severity keep a stable, deterministic relative order
  (their kind's own internal ordering, e.g. issues by recency, approvals by
  soonest-expiry) so the list does not reshuffle between otherwise-identical
  renders
- **AND** the trailing "N more/older issue groups" rollup row and any
  explicitly-included old-issue rows remain appended after the severity-sorted
  set, since they summarize/de-prioritize rather than represent a current
  signal

#### Scenario: A tripped QA circuit breaker surfaces as an attention row

- **WHEN** `GET /api/qa/summary`'s `circuit_breaker.tripped` is `true`
- **THEN** the attention list renders a critical-severity row naming the
  circuit breaker as tripped and the `consecutive_failures` count, linking to
  `/qa`
- **AND** this row takes precedence over a same-summary "last patrol failed"
  row (a tripped breaker means the QA staffer has stopped dispatching
  entirely, a more severe state than one failed patrol run)

#### Scenario: An unreachable notifications source surfaces as a degraded row

- **WHEN** `GET /api/notifications/stats` returns `source_available: false`
- **THEN** the attention list renders a high-severity, source-error row
  naming the notifications feed as unavailable, instead of silently showing
  no notification-pressure row (the underlying `failed` count is a
  fabricated zero in this case, not a genuine "no failures" result)
- **AND** this row does not also render alongside a normal "N failed
  notifications" row for the same fetch

#### Scenario: An unreachable butler board source surfaces as a degraded row

- **WHEN** `GET /api/butlers/board` fails to load (`butlersError` is `true`)
- **THEN** the attention list renders a high-severity, source-error row
  naming butler status as unavailable, linking to `/butlers`
- **AND** this holds even when no other attention source has a signal, so
  the list cannot silently render `Nothing waiting.` while the SAME board
  fetch drives the dashboard briefing headline's `"degraded"` state_class
  (`dashboard-briefing` spec's Degraded class scenario) -- bu-gcz9e.2's
  cross-surface consistency test pins this bound from a shared fixture

#### Scenario: Stale issues are summarized

- **WHEN** an unresolved issue has `first_seen_at` older than 24 hours
- **THEN** the row detail exposes that it is old/stale using a human-readable age
  calculated relative to the owner's configured timezone
- **AND** repeated old issues with the same `type` and `description` MAY collapse
  into one summarized row when `occurrences` or `butlers` indicates multiplicity
- **AND** the summary MUST name the affected butlers with human-readable names,
  not raw machine identifiers

#### Scenario: Attention list handles empty, loading, and error states

- **WHEN** issues are loading
- **THEN** the list renders stable loading rows or an equivalent skeleton

- **WHEN** `GET /api/issues` succeeds with an empty array
- **THEN** the list renders the serif Voice empty state `Nothing waiting.`
- **AND** it does not render an empty table, blank card, or celebratory graphic

- **WHEN** `GET /api/issues` fails
- **THEN** the list renders a local error row for the attention surface
- **AND** the rest of the Overview remains visible

### Requirement: Inline Approve/Deny/Defer on Attention Rows

When individual pending approvals are available (not just the aggregate pending count), the `Needs attention` list SHALL render one actionable row per approval — up to a small cap, with any remainder collapsed into a single "N more pending approvals" row linking to `/approvals` — instead of only the aggregate count row. Each actionable row SHALL expose verb-labeled Approve/Deny/Defer buttons that call the same approve/deny/defer endpoints `/approvals` uses, letting the owner decide without leaving the Overview page. A row's own pending state (e.g. "Approving…") SHALL be scoped to that row's button only, never borrowed by a sibling row's in-flight decision.

When the individual pending-approvals detail fetch is unavailable or empty while the aggregate metrics still report a nonzero pending count, the list SHALL fall back to the existing aggregate "N pending approvals" link row rather than silently dropping the signal.

#### Scenario: Actionable rows render inline decision buttons

- **WHEN** one or more individual pending approvals are available
- **THEN** the attention list renders one row per approval (capped, with a "N more" row for the remainder) each showing Approve, Deny, and Defer buttons
- **AND** clicking a button calls the corresponding approve/deny/defer endpoint with that approval's id and optimistically removes the row on success.

#### Scenario: Falls back to the aggregate count when the detail list is unavailable

- **WHEN** the individual pending-approvals fetch errors or returns no rows while the aggregate metrics report `total_pending > 0`
- **THEN** the attention list renders the existing aggregate "N pending approvals" row linking to `/approvals`, with no inline decision buttons.

### Requirement: Runtime KPI Strip

The home page SHALL render a promoted four-cell runtime KPI strip. "Promoted"
means the KPIs are part of the primary information hierarchy; it does not mean
they use heavier card chrome. The strip SHALL remain hairline-divided,
tabular-numeric, and visually calm.

#### Scenario: KPI cells have defined meanings

- **WHEN** the runtime KPI strip renders
- **THEN** it includes exactly these four cells:
  - `Total butlers`: count of `GET /api/butlers` rows where `type` is `"butler"`
  - `Healthy`: count of butler rows whose `status` is `"ok"`, `"online"`, or `"healthy"`
  - `Sessions · 24h`: sum of `sessions_24h` across butler rows
  - `Pending approvals`: `total_pending` from `GET /api/approvals/metrics`
- **AND** every numeric value uses tabular numerals

#### Scenario: KPI strip handles loading and partial failure

- **WHEN** either KPI source is still loading
- **THEN** cells depending on unavailable data render an unavailable/loading
  value without shifting layout

- **WHEN** one KPI source fails
- **THEN** cells backed by the failed source render an unavailable/error value
- **AND** cells backed by the still-available source MAY continue rendering

### Requirement: Operations Index

The home page SHALL render a right-column `Operations` section summarizing the
active domain butlers. The section is a scan list, not a chart.

#### Scenario: Operations rows join butler and spend summaries

- **WHEN** `GET /api/butlers` returns butler rows
- **THEN** `Operations` renders only rows whose `type` is `"butler"`
- **AND** each row shows the butler identity, session count from `sessions_24h`,
  and today's spend from `GET /api/spend/summary?period=today` `by_butler`
- **AND** missing spend data renders as an explicit zero or unavailable value,
  not by hiding the row

#### Scenario: Operations handles empty, loading, and error states

- **WHEN** butlers are loading
- **THEN** `Operations` renders stable loading rows or an equivalent skeleton

- **WHEN** no domain butlers are active
- **THEN** `Operations` renders `No butlers active.`

- **WHEN** the butlers query fails
- **THEN** `Operations` renders a local error state
- **AND** the rest of the Overview remains visible

### Requirement: Now List

The home page SHALL render a right-column `Now` section for immediate
operational items. In the first implementation this section is sourced from
existing endpoints and does not require a new endpoint.

The acceptable first-source set is:

- `GET /api/approvals/metrics` for pending approval count;
- `GET /api/qa/summary` for QA patrol, finding, and dispatched-investigation
  pressure;
- `GET /api/qa/investigations` when the row needs active investigation or PR
  detail beyond the summary counts;
- `GET /api/notifications/stats` for failed notification pressure;
- `GET /api/timeline` for recent activity, or `GET /api/sessions` when the
  implementation only needs recent completed sessions.

#### Scenario: Pending approvals appear in Now

- **WHEN** `GET /api/approvals/metrics` returns `total_pending` greater than zero
- **THEN** `Now` renders one immediate item naming the pending approval count
- **AND** the item is labelled as an approval item

#### Scenario: QA pressure appears in Now

- **WHEN** `GET /api/qa/summary` reports novel findings, dispatched
  investigations, an active patrol failure, or another current QA alert
- **THEN** `Now` renders an immediate item naming the QA state in human-readable
  terms
- **AND** if active investigation or PR detail is needed, the page MAY read
  `GET /api/qa/investigations` instead of introducing a new endpoint

#### Scenario: Failed notification pressure appears in Now

- **WHEN** `GET /api/notifications/stats` returns `failed` greater than zero
- **THEN** `Now` renders an immediate item naming the failed notification count
- **AND** the item is labelled as a notification item

#### Scenario: Recent activity appears in Now

- **WHEN** `GET /api/timeline` returns recent activity, or `GET /api/sessions`
  returns recent completed sessions
- **THEN** `Now` MAY render a compact recent activity item
- **AND** the row links to the appropriate timeline or sessions surface when a
  link is available

#### Scenario: Now handles empty, loading, and error states

- **WHEN** one or more `Now` sources are loading
- **THEN** `Now` renders stable loading rows or an equivalent skeleton

- **WHEN** every loaded `Now` source reports no actionable state
- **THEN** `Now` renders `Nothing scheduled.`

- **WHEN** a `Now` source fails
- **THEN** `Now` renders a local error state for that source
- **AND** the rest of the Overview remains visible

### Requirement: Page Archetype Compliance

The home page SHALL adopt the Editorial archetype as defined in
`about/lay-and-land/frontend.md`. The shared `<Page>` primitive
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
- **THEN** it SHALL use `<Page archetype="editorial" title="Overview">` as its
  outermost container
- **AND** the cockpit surfaces SHALL be direct children of `<Page>`, not
  wrapped in a raw `<div className="space-y-6">`

## Source References

- `about/heart-and-soul/design-language.md` §Editorial archetype: the Overview
  uses the Voice surface, status pill, attention list, KPI strip, and
  right-column index.
- `about/lay-and-land/frontend.md` §Editorial archetype layout: the Overview
  frame is `<Page archetype="editorial">` with left-column narrative and
  right-column scan lists.
- `openspec/changes/dashboard-overview-briefing/specs/dashboard-briefing/spec.md`:
  the briefing response remains the six-field API contract consumed by the
  Overview page.
- Current endpoint sources: `GET /api/dashboard/briefing`, `GET /api/issues`,
  `GET /api/butlers`, `GET /api/spend/summary?period=today`,
  `GET /api/approvals/metrics`, `GET /api/qa/summary`,
  `GET /api/qa/investigations`, `GET /api/notifications/stats`,
  `GET /api/timeline`, and `GET /api/sessions`.
