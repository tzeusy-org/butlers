## Context

The Butlers dashboard home page (`/`) is the first thing the owner sees. It is the
"is the system healthy right now?" glance. The current layout treats all four stats
as equal and gives the dominant position to the topology graph, which answers a
different question ("what is connected?") rather than the sentinel question
("is the system working?").

This change aligns the spec with the visual contract being implemented in Vertical D
(bu-2okpr epic). It does not introduce new backend endpoints, new database tables, or
new API contracts. It documents a front-end-only redesign that was already settled in
the design-language doctrine.

## Goals / Non-Goals

**Goals:**

- Define the home page layout contract in terms of information hierarchy: what gets
  visual primacy, what is demoted to supporting context, and what leaves the page.
- Establish the `dashboard-overview` capability spec so future implementers know what
  the page is supposed to render.
- Document the frontend data sources and component boundaries so the implementation
  beads have a normative contract to verify against.

**Non-Goals:**

- Implementing any frontend component. That is done by bu-2okpr.2 through bu-2okpr.6.
- Changing any API endpoint. The existing `/api/sessions` surface is used as-is.
- Specifying visual design tokens, color palettes, or pixel-level layout. That is the
  domain of `about/heart-and-soul/design-language.md` and the craft-and-care bar, not
  an openspec capability.
- Specifying the `<Page>` archetype wrapper itself. That is Vertical A scope
  (bu-vj0h3 epic); this spec assumes the `<Page archetype='overview'>` shell exists
  and uses it.

## Decisions

### D1: New capability spec, not a delta to an existing spec.

The dashboard home page at `/` is not currently specced as a capability. `dashboard-shell`
registers the route and names it "Overview dashboard" but says nothing about what the
page renders inside. `dashboard-api` owns the data layer. Neither spec owns the page
composition contract.

A new spec (`dashboard-overview`) is therefore the right vehicle. It does not conflict
with `dashboard-shell` (which owns routing and navigation) or `dashboard-api` (which
owns endpoints and data hooks).

**Alternative considered:** add a delta to `dashboard-shell` describing the home page
content. **Rejected**: the shell spec is already large and covers stable infrastructure
(sidebar, header, error boundary, theme system). Adding a mutable content contract for
a single page to that spec would couple two different change rates. Separate specs for
separate concerns.

### D2: Sessions over time as the primary visualization, not session count as a number.

The doctrine says "sessions is the hero metric." There are two ways to render a hero
metric: as a big number (count) or as a time series (trend). A big number tells the owner
where they are right now; a time series tells them whether the system is doing its job over
time.

The time series is more useful. "12 sessions today" is less informative than seeing a
stacked bar chart showing sessions by butler across the past 24 hours, with a visible
gap where the chronicler was not running. The chart makes anomalies visible; the number
hides them.

**Alternative considered:** show session count as the primary big-number stat, elevated
visually. **Rejected**: this was the pre-change status quo (Sessions Today in the stats
bar). The problem is not that it was invisible -- it was there. The problem is it was equal
in weight to "Healthy" and "Est. Cost." A number can only hold the hero position if nothing
else is competing on the same tier.

### D3: Topology graph leaves the home page.

The topology graph currently occupies the full-width primary region. It answers a
structural question ("what is wired to what?") that is better answered on a dedicated page
such as `/system` or `/butlers`. It does not answer the sentinel question. It stays for now
in the DashboardPage only as a demoted secondary card (Vertical D scope bu-2okpr.5 decides
whether to relocate it entirely to `/system`).

The spec does not mandate topology's destination. It mandates that topology is not the
primary region.

### D4: Client-side bucketing for the session stripe chart.

The sessions API returns individual session records with `started_at`, `completed_at`,
and `butler_name`. Client-side bucketing (grouping sessions into time buckets, e.g. hourly
for the past 24 hours) is sufficient for the chart. A new backend aggregation endpoint is
not required in v1.

**Alternative considered:** a new `GET /api/sessions/aggregate` endpoint returning
pre-bucketed counts grouped by butler and time window. **Deferred:** this would reduce
client-side complexity and enable efficient queries for large session counts, but the
existing endpoint (with a generous `limit` parameter) is sufficient for v1 where the
owner typically has tens to low hundreds of sessions per day. This decision is
revisited if the client-side aggregation becomes a performance problem.

### D5: Stat strip kept, not removed.

Cost, health status, and pending approvals remain on the home page as a demoted supporting
strip. Removing them entirely would destroy useful at-a-glance context. Demoting them
(smaller type, no card wrapper) achieves the visual hierarchy goal without losing the
information.

## Open Questions

- **[Deferred] Topology graph destination.** Should the topology graph move to `/system`,
  to `/butlers`, or be removed? Vertical D scope bead bu-2okpr.5 makes this decision;
  this spec does not. When that bead lands, the `dashboard-overview` spec should be
  updated to reflect whether topology appears at all on the home page.
- **[Deferred] Backend aggregation endpoint.** If session counts grow to thousands per
  day (e.g. if the owner runs many short sessions via automated triggers), client-side
  bucketing will degrade. A `GET /api/sessions/aggregate` endpoint is the natural follow-up.
  File a bead when the threshold is hit.
- **[Future] Time window selector.** The stripe chart defaults to the past 24 hours.
  A window picker (7d, 30d) is a natural extension; it is not in scope for Vertical D.
