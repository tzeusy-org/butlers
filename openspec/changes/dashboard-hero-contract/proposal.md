## Why

The dashboard home page today shows four equal-weight stats (Total Butlers, Healthy,
Sessions Today, Estimated Cost) and a full-width topology graph. None of those four stats
answers the question the owner actually asks when they open the dashboard: "Have my butlers
been working on my behalf today?"

The owner-confirmed answer is: butler sessions. A session is the atomic unit of the system
doing work. When sessions are happening at the right rate, the system is healthy. When they
drop, something is wrong. That is the signal that deserves the home page.

`about/heart-and-soul/design-language.md` Settled Direction #4 is explicit:

> Hero metric: butler sessions. The single number that tells the owner whether their system
> is doing its job today is sessions -- how many times butlers spun up to act on the owner's
> behalf. Cost, health, and pending approvals stay on the home page as supporting context, but
> session count is the one that gets visual primacy.

And Settled Direction #2 requires every page to aim for Chronicles-grade feature richness:
a real primary visualization, not a stats bar.

This change documents the contract that code changes bu-2okpr.2 through bu-2okpr.6 are
implementing. The openspec change backfills the doctrine before the implementation merges.

## What Changes

- **New capability**: `dashboard-overview` -- the home page at `/` SHALL have sessions over
  time as its primary visualization. The topology graph and four equal-weight stat tiles are
  demoted to supporting context. The page follows the Overview/Dashboard archetype defined in
  `about/lay-and-land/frontend.md`.
- The `dashboard-shell` spec is not modified. It already registers `/` as a route; this
  change defines what that route renders inside.
- No backend API changes are required. The existing `/api/sessions` endpoint (with `since`,
  `until`, and `limit` support) is sufficient.

## Capabilities

### New Capabilities

- `dashboard-overview`: The `/` home page contract -- primary visualization (sessions over
  time, butler-colored stripes), secondary feed (recent moments), and a demoted supporting
  stat strip (health, cost, pending approvals). The topology graph is removed from the home
  page or relocated to `/system`.

### Modified Capabilities

None. `dashboard-shell` already owns the route map and navigation. `dashboard-api` already
owns the sessions data contract. No capability spec needs a delta from this change.

## Impact

- **New spec**: `openspec/specs/dashboard-overview/spec.md` -- created by this change.
- **Frontend**: `frontend/src/pages/DashboardPage.tsx` (implementation bead bu-2okpr.6),
  `frontend/src/components/dashboard/SessionStripeChart.tsx` (bu-2okpr.2),
  `frontend/src/components/dashboard/RecentMoments.tsx` (bu-2okpr.3),
  stat strip refactor (bu-2okpr.4), topology demotion (bu-2okpr.5),
  Page archetype adoption (bu-2okpr.6).
- **No database changes.**
- **No new API endpoints.**
