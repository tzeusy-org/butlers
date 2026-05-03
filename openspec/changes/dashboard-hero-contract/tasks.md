## 1. Spec Landing

- [ ] 1.1 Land this proposal, design, and `dashboard-overview` spec via standard OpenSpec
      review workflow (PR review, owner confirmation).
- [ ] 1.2 Confirm that `dashboard-shell` does not need a delta. The home page route `/`
      is already registered there; the content contract now lives in `dashboard-overview`.
- [ ] 1.3 Once merged, run `openspec sync dashboard-hero-contract` to promote the
      `dashboard-overview` spec into `openspec/specs/dashboard-overview/spec.md`.

## 2. Session Stripe Chart (bu-2okpr.2)

- [ ] 2.1 Build `frontend/src/components/dashboard/SessionStripeChart.tsx` with stacked
      bar or area chart (recharts), butler-colored stripes.
- [ ] 2.2 Implement client-side bucketing: group sessions by `started_at` into hourly
      buckets for the 24-hour window.
- [ ] 2.3 Color strategy: deterministic mapping from butler name to `--category-N` (mod 8)
      token. Document the mapping in the component file.
- [ ] 2.4 Implement empty, loading, and error states.
- [ ] 2.5 Write component tests (vitest or jest) covering: renders with mocked data,
      renders empty state, renders loading state.

## 3. Recent Moments Feed (bu-2okpr.3)

- [ ] 3.1 Build `frontend/src/components/dashboard/RecentMoments.tsx` showing the 5-10
      most recent completed sessions.
- [ ] 3.2 Each row: relative time, butler name/glyph, one-line session summary.
- [ ] 3.3 Link each row to `/sessions/:id` for drill-down.
- [ ] 3.4 Implement empty state.

## 4. Stat Strip Demotion (bu-2okpr.4)

- [ ] 4.1 Replace the four-tile `StatsCard` grid in `DashboardPage.tsx` with a compact
      horizontal stat strip.
- [ ] 4.2 Use `text-sm` or `text-base` type; remove the `Card` wrapper per metric.
- [ ] 4.3 Retain all four metrics: butler health ratio, sessions today, estimated cost,
      pending approvals.

## 5. Topology Graph Demotion (bu-2okpr.5)

- [ ] 5.1 Remove the topology graph from the primary region of the home page.
- [ ] 5.2 Decision: relocate to `/system` (if the System page from bu-ngfzz.3 exists),
      demote to a small secondary card on the home page, or defer to the `/butlers` page.
      Document the decision in the implementation PR.
- [ ] 5.3 Update `dashboard-overview` spec with the topology decision once bu-2okpr.5 closes.

## 6. DashboardPage Integration (bu-2okpr.6)

- [ ] 6.1 Wire SessionStripeChart as the primary region.
- [ ] 6.2 Wire RecentMoments feed below the chart.
- [ ] 6.3 Place stat strip below the RecentMoments feed.
- [ ] 6.4 Retain Failed Notifications card, Issues panel, and QaWidget below the fold.
- [ ] 6.5 Adopt `<Page archetype='overview'>` shell when Vertical A (bu-vj0h3) lands.
- [ ] 6.6 Replace any `new Date(...).toLocaleString()` calls with `<Time>` when
      Vertical C (bu-9r7js or equivalent) lands.

## 7. Spec Sync and Reconciliation (bu-2okpr.7)

- [ ] 7.1 Run `openspec sync dashboard-hero-contract` to merge delta specs into the
      authoritative spec tree.
- [ ] 7.2 Verify all acceptance criteria in the parent epic (bu-2okpr) against the
      delivered implementation.
- [ ] 7.3 Live browser verification: load `/` in a real browser and confirm the
      SessionStripeChart is above the fold and visually dominant.
- [ ] 7.4 Close reconciliation bead once all checks pass.

## 8. Open Questions

- [ ] 8.1 Topology graph destination (bu-2okpr.5): resolve whether it lands on `/system`,
      stays on home as a secondary card, or moves to `/butlers`. Update this spec once decided.
- [ ] 8.2 Backend aggregation endpoint: monitor session volume. If client-side bucketing
      becomes slow (e.g. >1000 sessions/day), file a follow-up bead for
      `GET /api/sessions/aggregate`.
- [ ] 8.3 Time window selector: a "last 7 days" / "last 30 days" picker on the stripe
      chart is a natural follow-up for a future vertical.
