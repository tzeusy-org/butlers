## 1. Spec Landing

- [x] 1.1 Land this proposal, design, and `dashboard-overview` spec via standard OpenSpec
      review workflow (PR review, owner confirmation). _(Delivered bu-2okpr.1, closed)_
- [x] 1.2 Confirm that `dashboard-shell` does not need a delta. The home page route `/`
      is already registered there; the content contract now lives in `dashboard-overview`.
- [ ] 1.3 Once merged, run `openspec sync dashboard-hero-contract` to promote the
      `dashboard-overview` spec into `openspec/specs/dashboard-overview/spec.md`.

## 2. Session Stripe Chart (bu-2okpr.2) — SHIPPED (PR #1345)

- [x] 2.1 Build `frontend/src/components/dashboard/SessionStripeChart.tsx` with stacked
      bar or area chart (recharts), butler-colored stripes.
- [x] 2.2 Implement client-side bucketing: group sessions by `started_at` into hourly
      buckets for the 24-hour window.
- [x] 2.3 Color strategy: deterministic mapping from butler name to `--category-N` (mod 8)
      token. Document the mapping in the component file.
- [x] 2.4 Implement empty, loading, and error states.
- [x] 2.5 Write component tests (vitest or jest) covering: renders with mocked data,
      renders empty state, renders loading state.

## 3. Recent Moments Feed (bu-2okpr.3) — SHIPPED (PR #1346)

- [x] 3.1 Build `frontend/src/components/dashboard/RecentMoments.tsx` showing the 5-10
      most recent completed sessions.
- [x] 3.2 Each row: relative time, butler name/glyph, one-line session summary.
- [x] 3.3 Link each row to `/sessions/:id` for drill-down.
- [x] 3.4 Implement empty state.

## 4. Stat Strip Demotion (bu-2okpr.4) — SHIPPED (PR #1351)

- [x] 4.1 Replace the four-tile `StatsCard` grid in `DashboardPage.tsx` with a compact
      horizontal stat strip.
- [x] 4.2 Use `text-sm font-medium tabular-nums` for values; `text-xs text-muted-foreground`
      for labels; remove the `Card` wrapper per metric.
- [x] 4.3 Retain all four metrics: butler health ratio, sessions today, estimated cost,
      pending approvals.

## 5. Topology Graph Demotion (bu-2okpr.5) — SHIPPED (PR #1361)

- [x] 5.1 Remove the topology graph from the primary region of the home page.
- [x] 5.2 Decision: **relocated to `/system`** (System page was available via bu-ngfzz.3).
      The topology graph no longer appears on `/`.
- [x] 5.3 Update `dashboard-overview` spec with the topology decision. _(Reflected in
      spec.md — topology is absent from the home page; it lives at `/system`.)_

## 6. DashboardPage Integration (bu-2okpr.6) — SHIPPED (PR #1363)

- [x] 6.1 Wire SessionStripeChart as the primary region (region 1).
- [x] 6.2 Wire RecentMoments feed below the chart (region 2).
- [x] 6.3 Place stat strip as the final region (region 5, below the QaWidget).
- [x] 6.4 Secondary card grid (Failed Notifications + IssuesPanel) wired as region 3.
      QaWidget wired as region 4 (added by bu-yo4bt.9, PR #1380 — standalone card
      showing QA patrol status/findings and active investigations, between secondary
      grid and stat strip).
- [x] 6.5 Adopted `<Page archetype='overview'>` shell — `<Page>` primitive shipped via
      Vertical A (bu-vj0h3).
- [x] 6.6 `<Time mode="relative">` used in RecentMoments for relative timestamps.

## 7. Spec Sync and Reconciliation (bu-2okpr.7)

- [ ] 7.1 Run `openspec sync dashboard-hero-contract` to merge delta specs into the
      authoritative spec tree.
- [ ] 7.2 Verify all acceptance criteria in the parent epic (bu-2okpr) against the
      delivered implementation.
- [ ] 7.3 Live browser verification: load `/` in a real browser and confirm the
      SessionStripeChart is above the fold and visually dominant.
- [ ] 7.4 Close reconciliation bead once all checks pass.

## 8. Open Questions

- [x] 8.1 Topology graph destination (bu-2okpr.5): resolved — relocated to `/system`.
      Home page has no topology graph.
- [ ] 8.2 Backend aggregation endpoint: monitor session volume. If client-side bucketing
      becomes slow (e.g. >1000 sessions/day), file a follow-up bead for
      `GET /api/sessions/aggregate`.
- [ ] 8.3 Time window selector: a "last 7 days" / "last 30 days" picker on the stripe
      chart is a natural follow-up for a future vertical.
