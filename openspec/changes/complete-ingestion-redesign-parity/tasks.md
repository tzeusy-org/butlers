# Tasks

This change is planning-only until ratified. Implementation beads created from
this task list must not close without live visual evidence.

## 1. Spec Landing

- [x] 1.1 Create `complete-ingestion-redesign-parity` as the page-level
      OpenSpec change for the remaining ingestion redesign gap.
- [x] 1.2 Add a binding `dashboard-ingestion-dispatch-console` capability that
      treats the prototype handoff as implementation input.
- [x] 1.3 Add a `dashboard-shell` route-map delta for ingestion sub-routes.
- [x] 1.4 Run `openspec validate complete-ingestion-redesign-parity --strict`
      and fix structural issues.
- [x] 1.5 Obtain operator signoff on the change before implementation begins.

## 2. Frontend Foundation Bead

- [ ] 2.1 Replace the page-level `TabsTrigger` ingestion shell with
      route-backed `IngestionSubNav`.
- [ ] 2.2 Add shared Dispatch primitives: `Eyebrow`, `Mono`, `PillBtn`,
      `ChannelGlyph`, `StatusBadge`, `KV`, and one canonical `FlameStrip`.
- [ ] 2.3 Add URL-state helpers for range, channels, statuses, saved view, and
      expanded event.
- [ ] 2.4 Preserve legacy `?tab=` URLs through redirects that retain compatible
      query parameters.
- [ ] 2.5 Add foundation tests proving the old tab shell is not rendered on the
      redesigned routes.

## 3. Timeline Ledger Bead

- [ ] 3.1 Implement the Timeline header band, live status pill, KPI rail,
      toolbar, channel chip strip, status filters, and saved views.
- [ ] 3.2 Replace the `Ingestion Events` card/table with hour-grouped ledger
      rows, selection state, inline flame strips, and a bulk-action bar.
- [ ] 3.3 Implement the expanded drawer with step ledger, raw payload, replay
      history, request metadata, session index, copy actions, and URL-backed
      drawer state.
- [ ] 3.4 Backfill or adapt API hooks for cursor pagination, search, status
      filters, replay history, raw payload gating, and rollup totals.
- [ ] 3.5 Add focused component/API tests and a Playwright drawer-deep-link
      smoke test.

## 4. Connectors Roster and Detail Beads

- [ ] 4.1 Implement `/ingestion/connectors` as the dense roster: attention
      strip, health/auth columns, sparkline, 24h aggregates, dormant section,
      and KPI footer.
- [ ] 4.2 Replace the card-grid connector list with roster rows and route
      disclosure to connector detail.
- [ ] 4.3 Implement connector detail with header mark, reauth callout, KPI
      strip, 24h histogram, recent events, incidents, scope list, schedule,
      routing rules, and config actions.
- [ ] 4.4 Consume `add-connector-oauth-scope-surface` fields when available and
      render explicit unsupported/unavailable states otherwise.
- [ ] 4.5 Add API tests for any new connector roster/detail fields and
      component tests for auth issue rendering.

## 5. Filters Pipeline Bead

- [ ] 5.1 Implement `/ingestion/filters` as the pipeline explanation surface:
      header, five-gate diagram, proportional funnel, gate sections, rule rows,
      archived rules, and footer actions.
- [ ] 5.2 Add priority senders and channel defaults blocks backed by real API
      responses.
- [ ] 5.3 Replace legacy card-based filter content on this route.
- [ ] 5.4 Add API/component tests for pipeline stats, priority contacts,
      channel defaults, and rule grouping.

## 6. Visual Verification Bead

- [ ] 6.1 Add Playwright coverage for `/ingestion`,
      `/ingestion/connectors`, connector detail, and `/ingestion/filters`.
- [ ] 6.2 Capture desktop and mobile screenshots of the live routes.
- [ ] 6.3 Capture prototype reference screenshots, or document a deterministic
      fallback if the prototype bundle cannot render in headless automation.
- [ ] 6.4 Add assertions that the redesigned routes do not expose the old
      `Ingestion Events` card shell or page-level `Timeline/Connectors/Filters/History`
      tab switcher.
- [ ] 6.5 Store visual evidence in `docs/reports/` or another committed
      report location referenced by the epic report.

## 7. Reconciliation, Report, and Archive

- [ ] 7.1 Reconcile implemented code against every requirement in
      `dashboard-ingestion-dispatch-console`.
- [ ] 7.2 Generate the epic completion report with prototype-to-live evidence,
      unresolved deviations, and screenshot links.
- [ ] 7.3 Run `openspec validate complete-ingestion-redesign-parity --strict`.
- [ ] 7.4 Archive the OpenSpec change only after spec, implementation,
      verification, and report beads are closed.

## Acceptance Criteria

- [ ] `/ingestion` no longer looks like the old card/table tab page.
- [ ] All three primary sub-routes are first-class routes, not hidden tabs.
- [ ] Timeline, Connectors, connector detail, and Filters surfaces are backed
      by real endpoint data or explicit unavailable states.
- [ ] Raw payload and replay actions are audited or gated as specified.
- [ ] Desktop and mobile visual evidence exists for the live implementation.
- [ ] The final report maps every prototype obligation to pass, deliberate
      deviation, or follow-up bead.
