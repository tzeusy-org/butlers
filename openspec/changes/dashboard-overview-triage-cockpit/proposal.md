## Why

The canonical `dashboard-overview` spec still describes the Overview page as a
chart-first surface: session stripe chart, recent moments feed, secondary cards,
QA widget, and a demoted stat strip. That contract is stale. Doctrine and the
current `DashboardPage.tsx` have moved the Overview toward an editorial triage
cockpit: a system-spoken briefing, a `Needs attention` list, promoted runtime
KPIs, and a right column for operational scan lists.

The stale chart-first spec now creates bad downstream work. It asks implementers
to optimize a layout the product no longer wants and conflicts with
`about/heart-and-soul/design-language.md`'s settled editorial archetype. This
change reconciles the spec before additional frontend implementation beads
continue.

## What Changes

- **Modified Capability**: `dashboard-overview` becomes the contract for the
  editorial triage cockpit at `/`.
- The Overview opens with the existing `dashboard-briefing` endpoint response
  (`GET /api/dashboard/briefing`), rendered as the Voice surface defined by the
  design language.
- `Needs attention` is defined as a rule-separated list derived from
  `GET /api/issues`; the spec now covers issue severity ordering, stale issue
  summarization, empty/loading/error states, and old issue grouping.
- The runtime KPI strip is promoted into the core information hierarchy and its
  four cells are defined precisely: total butlers, healthy butlers, sessions in
  the last 24 hours, and pending approvals.
- The right column is explicitly named and scoped:
  - `Operations`: butler scan list sourced from `GET /api/butlers` and
    `GET /api/costs/summary?period=today`.
  - `Now`: immediate operational items sourced from existing approval, QA,
    notification, and activity endpoints: `GET /api/approvals/metrics`,
    `GET /api/qa/summary`, `GET /api/qa/investigations`,
    `GET /api/notifications/stats`, and either `GET /api/timeline` or
    `GET /api/sessions`.
- The stale chart-first requirements are removed from the page contract. Session
  charts and recent moments may still exist elsewhere, but they no longer define
  the Overview's primary region.
- No new backend endpoint is introduced. Any future need for a richer `Now` list
  must justify itself against existing approval, QA, notification, activity,
  schedule, issue, and briefing surfaces first.

## Capabilities

### Modified Capabilities

- `dashboard-overview`: replace chart-first five-region hierarchy with the
  editorial triage cockpit contract, including binding data sources and states.

### Related Capabilities

- `dashboard-briefing`: unchanged. This change consumes its existing six-field
  response contract but does not modify the endpoint.

## Impact

**Doctrine and topology**
- Aligns the capability spec with `about/heart-and-soul/design-language.md`
  §Editorial archetype and `about/lay-and-land/frontend.md` §Editorial
  archetype layout.

**Frontend implementation follow-up**
- A downstream implementation bead should reconcile `DashboardPage.tsx` and the
  `frontend/src/components/overview/` components to the new spec without adding
  backend routes.
- A focused frontend test bead should update or add `DashboardPage` tests for
  the cockpit hierarchy, existing data-source usage, and empty/loading/error
  states.

**Backend**
- No backend endpoint or schema change.

**Out of scope**
- Implementing the frontend in this change.
- Changing the six-field `GET /api/dashboard/briefing` response.
- Adding a new dashboard aggregation endpoint for Overview data.
