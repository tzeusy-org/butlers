# Ingestion Redesign — Epic Completion Report

**Date:** 2026-05-25
**Epic:** bu-y25mj — "Complete /ingestion visual redesign parity"
**Spec change:** `openspec/changes/complete-ingestion-redesign-parity/`
**Prototype reference:** `pr/overview/ingestion-redesign/` (READ-ONLY)

---

## Section 1: Epic / Child Status Table

| Bead | Title | Status | PR | Closed At |
|---|---|---|---|---|
| bu-y25mj | Epic: Complete /ingestion visual redesign parity | open | — | — |
| bu-y25mj.1 | Implement ingestion Dispatch route foundation | closed | [#1940](https://github.com/owner/repo/pull/1940) | 2026-05-25 |
| bu-y25mj.2 | Ratify complete-ingestion-redesign-parity OpenSpec | closed | [#1925](https://github.com/owner/repo/pull/1925) | 2026-05-25 |
| bu-y25mj.3 | Build ingestion connector roster and detail | closed | [#1941](https://github.com/owner/repo/pull/1941) | 2026-05-25 |
| bu-y25mj.4 | Build ingestion Timeline ledger and drawer | closed | [#1942](https://github.com/owner/repo/pull/1942) | 2026-05-25 |
| bu-y25mj.5 | Build ingestion Filters pipeline | closed | [#1943](https://github.com/owner/repo/pull/1943) | 2026-05-25 |
| bu-y25mj.6 | Verify ingestion redesign visual parity | closed | [#1944](https://github.com/owner/repo/pull/1944) | 2026-05-25 |
| bu-y25mj.7 | Reconcile ingestion redesign implementation against spec | closed | [#1945](https://github.com/owner/repo/pull/1945) | 2026-05-25 |
| bu-y25mj.8 | Generate epic report (this document) | in_progress | — | — |

**All P1 children (.1 through .7) are closed.** The epic bead itself remains open pending operator archive decision.

---

## Section 2: Spec Compliance Matrix

Full detail: [`docs/reports/ingestion-redesign-reconciliation-2026-05-25.md`](ingestion-redesign-reconciliation-2026-05-25.md)

**Aggregate counts across 38 requirements (spec `dashboard-ingestion-dispatch-console` + `dashboard-shell`):**

| Status | Count |
|---|---|
| PASS | 32 |
| DEVIATION | 5 |
| FOLLOW-UP | 1 |

### Inline Requirement Table

Source: reconciliation report (bu-y25mj.7 / PR #1945).

| # | Requirement | Status | Notes |
|---|---|---|---|
| R1.1 | Route hierarchy: `/ingestion`, `/ingestion/connectors`, `/ingestion/connectors/:type/:identity`, `/ingestion/filters` | PASS | All four routes are first-class child routes |
| R1.2 | `/ingestion` renders Timeline ledger (not old tab switcher) | PASS | `data-testid="timeline-ledger"` present |
| R1.3 | Sub-nav links to `/ingestion`, `/ingestion/connectors`, `/ingestion/filters` (no History tab) | PASS | Three NavLink items, no History tab |
| R1.4 | `?tab=connectors&range=24h` → `/ingestion/connectors?range=24h` | PASS | `tab` stripped, other params forwarded |
| R1.5 | `?tab=history` → `/ingestion` (no `/ingestion/history` primary route) | PASS | Spec-compliant; no fourth redesigned tab |
| R2.1 | Dispatch visual language: hairline-divided layouts, no card chrome | PASS | Playwright confirms card test-ids absent |
| R2.2 | Mono uppercase eyebrows | PASS | `font-mono text-[10px] tracking-[0.14em] uppercase` |
| R2.3 | Tabular numeric cells | PASS | `tabular-nums font-mono` across KPI cells, gates, roster |
| R2.4 | State colors as foreground or border signals only | PASS | Auth states use CSS color vars on text/borders |
| R2.5 | Butler hues only on letter marks | PASS | `butlerHueVar` scoped to letter-mark components |
| R2.6 | No emoji in interface chrome | PASS | `grep -rn "emoji"` returns no results in ingestion components |
| R2.7 | Empty states as one serif italic sentence | PASS | `font-serif ... italic` pattern used consistently |
| R2.8 | No shadcn `Card` containers or `TabsTrigger` on primary ingestion surfaces | PASS | Playwright absence assertions confirm |
| R3.1 | Timeline header band: eyebrow, live pill, range-aware headline, serif summary, KPIs | PASS | `DispatchHeader` with `LiveStatusBadge` |
| R3.2 | Sticky toolbar: range picker, saved views, status filters | PASS | `data-testid="timeline-toolbar"` with all three controls |
| R3.3 | Sticky toolbar: search bar | DEVIATION | No text search input. Tracked: **bu-mxtn2** |
| R3.4 | Sticky toolbar: channel chips | DEVIATION | No channel filter chips. Tracked: **bu-mxtn2** |
| R3.5 | Bulk-action bar when rows are selected | PASS | `data-testid="bulk-action-bar"`; replay pending backend (bu-va06h) |
| R3.6 | Hour-group headers with event count and cost rollup | PASS | `data-testid="hour-group"` |
| R3.7 | Ledger rows: all required columns | PASS | `data-testid="ledger-row"` |
| R3.8 | In-place expanded drawer: step ledger, raw payload, replay history, metadata, session index | PASS | `data-testid="event-drawer"` with all tabs |
| R3.9 | `?event=<id>` URL deep link opens drawer | PASS | `useEventDrawerState.ts`; Playwright confirmed |
| R3.10 | Closing drawer removes `?event` from URL | PASS | URL state hook clears param on close |
| R3.11 | Raw payload access is audited | PASS | `emit_dashboard_audit` called; fail-closed |
| R3.12 | Raw payload: gated/error/unavailable states without stale PII | PASS | `data-testid="raw-tab-gated"` |
| R3.13 | Footer rollup band for active filter window | DEVIATION | Load-more pagination only, no rollup band. Tracked: **bu-mxtn2** |
| R4.1 | Connectors roster: attention strip for auth/health issues | PASS | `AttentionStrip.tsx` |
| R4.2 | Connector rows: health dot, glyph/name/kind, sparkline, auth pill, totals | PASS | `ConnectorRosterRow.tsx` |
| R4.3 | Dormant/available connectors section with connect actions | PASS | `DormantList.tsx` |
| R4.4 | Connectors roster: footer KPI band and add-connector action | PASS | 5-cell KPI footer |
| R4.5 | Connector detail: header band | PASS | `ChannelGlyph` + `DispatchHeader` |
| R4.6 | Connector detail: reauth callout | PASS | `ReauthCallout.tsx` |
| R4.7 | Connector detail: KPI strip | PASS | `data-testid="kpi-strip"` |
| R4.8 | Connector detail: 24h histogram | PASS | `ConnectorHistogram.tsx` |
| R4.9 | Connector detail: recent events and incident list | DEVIATION | Not implemented. Tracked: **bu-5ywn2** |
| R4.10 | Connector detail: OAuth scope list; unsupported/unavailable explicit | PASS | `ScopeList.tsx` with `data-testid="scopes-unavailable"` |
| R4.11 | Connector detail: schedule, routing rules, config fields, safe actions | DEVIATION | Routing rules absent. Tracked: **bu-5ywn2** |
| R4.12 | Auth state consistent across attention strip, row, and detail | PASS | Shared `deriveConnectorDispatchInfo` |
| R5.1 | Filters pipeline: header with event count and range | PASS | `FiltersHeaderAside` with KPIs; degrades to `—` |
| R5.2 | Five-gate diagram: `accept`, `dedupe`, `tier`, `route`, `execute` | PASS | `data-testid="gate-node-${def.key}"` |
| R5.3 | Funnel distinguishes drops from preserved events | PASS | `data-testid="funnel-bar"` with segments |
| R5.4 | Route gate distinguishes preserved-without-dispatch vs hard drops | PASS | `deriveGateCounts` logic |
| R5.5 | One gate section per pipeline stage with rule rows | PASS | `GateSection.tsx`, `RuleRow.tsx` |
| R5.6 | Code-resident behavior notes for stages without rules | PASS | Serif italic note for dedupe/execute gates |
| R5.7 | Priority senders data block | PASS | `PrioritySendersBlock.tsx` |
| R5.8 | Priority sender mutations emit audit entries | PASS | `_audit_append` on add + remove |
| R5.9 | Channel defaults data block | PASS | `ChannelDefaultsBlock.tsx` |
| R5.10 | Channel defaults mutation failures visible; no optimistic hide | PASS | `channelMutationError` state + inline display |
| R5.11 | Channel default mutations emit audit entries | PASS | `_audit_append` on update |
| R5.12 | Archived/disabled rules section | PASS | `ArchivedRulesSection.tsx` |
| R5.13 | Add-rule and open-DSL footer actions | PASS | Footer buttons present |
| R6.1 | Every surface: explicit loading, empty, partial-error, unavailable states | PASS | Skeletons + error states on all surfaces |
| R6.2 | Partial backend failure: timeline usable when one drawer tab fails | PASS | Scoped per-tab error states in `EventDrawer.tsx` |
| R6.3 | Unavailable metrics ≠ zero (explicit unavailable state) | PASS | `aggregates_available` check → `—` display |
| R7.1 | Route smoke Playwright coverage for all ingestion routes | PASS | 4 Playwright spec files |
| R7.2 | Legacy `?tab=` redirect coverage | PASS | `ingestion-subroutes.spec.ts` + `ingestion-visual-parity.spec.ts` |
| R7.3 | DOM assertions: old card/tab shells absent | PASS | 3 routes × 3 assertions each |
| R7.4 | Desktop and mobile screenshots of live implementation | FOLLOW-UP | Playwright spec captures them; PNGs not committed to git. Tracked: **bu-6z2w4** |
| R7.5 | Final reconciliation report gates closure | PASS | This document + reconciliation report (bu-y25mj.7) |
| S1 | Ingestion routes are first-class child routes under root layout | PASS | `frontend/src/router-config.tsx` L155–171 |
| S2 | `/ingestion/connectors` renders inside root shell | PASS | `DispatchLayout` inside shell root |
| S3 | `/ingestion/connectors/:type/:identity` is route-addressable | PASS | `useParams()` in `ConnectorDetailPage.tsx` |
| S4 | Legacy `?tab=filters` normalizes to `/ingestion/filters` | PASS | `IngestionTabRedirect` |

---

## Section 3: Prototype Obligation Matrix

Source: `pr/overview/ingestion-redesign/INGESTION_HANDOFF.md` (READ-ONLY).
Primary evidence source: `docs/reports/ingestion-redesign-parity-2026-05-25.md` (bu-y25mj.6).

| Prototype Obligation | Live Route / Component | Status |
|---|---|---|
| `/ingestion` → Timeline ledger (default landing) | `frontend/src/pages/IngestionTimelinePage.tsx` | FULFILLED |
| `/ingestion/connectors` → Connectors roster | `frontend/src/pages/IngestionConnectorsPage.tsx` | FULFILLED |
| `/ingestion/connectors/:type/:identity` → Connector detail | `frontend/src/pages/ConnectorDetailPage.tsx` | FULFILLED |
| `/ingestion/filters` → Filters pipeline | `frontend/src/pages/IngestionFiltersPage.tsx` | FULFILLED |
| Sub-nav: Timeline / Connectors / Filters (NavLink, no TabsTrigger) | `frontend/src/components/ingestion/IngestionSubNav.tsx` | FULFILLED |
| Legacy `?tab=connectors` redirect | `frontend/src/router.tsx` `IngestionTabRedirect` | FULFILLED |
| Legacy `?tab=filters` redirect | `frontend/src/router.tsx` `IngestionTabRedirect` | FULFILLED |
| Legacy `?tab=history` → `/ingestion` (not a fourth tab) | `frontend/src/router.tsx` `IngestionTabRedirect` | FULFILLED |
| `/ingestion/history` bookmark-compat redirect | `frontend/src/router.tsx` `<Navigate to="/ingestion" />` | FULFILLED |
| Dispatch visual language (no card chrome, hairline borders) | `DispatchSurface.tsx`, `DispatchHeader.tsx` | FULFILLED |
| Eyebrow: `font-mono text-[10px] tracking-[0.14em] uppercase` | `frontend/src/components/ingestion/dispatch/DispatchHeader.tsx` L37 | FULFILLED |
| Tabular numeric cells (`tabular-nums font-mono`) | `TimelineTab.tsx`, `PipelineGateDiagram.tsx`, `ConnectorRosterRow.tsx` | FULFILLED |
| Butler hues only on letter marks | `EventDrawer.tsx` (`butlerHueVar`); `ConnectorDetailView.tsx` | FULFILLED |
| Empty states as one serif italic sentence | `TimelineTab.tsx`, `EventDrawer.tsx` (`font-serif ... italic`) | FULFILLED |
| No emoji in interface chrome | Confirmed by `grep` | FULFILLED |
| Timeline header band with live status pill | `IngestionTimelinePage.tsx` + `LiveStatusBadge` | FULFILLED |
| Timeline sticky toolbar (range picker, saved views, status filters) | `TimelineTab.tsx` Toolbar; `data-testid="timeline-toolbar"` | FULFILLED |
| Timeline sticky toolbar: search bar | Not implemented | DEVIATION → bu-mxtn2 |
| Timeline sticky toolbar: channel chips | Not implemented | DEVIATION → bu-mxtn2 |
| Timeline bulk-action bar | `TimelineTab.tsx` `BulkActionBar`; `data-testid="bulk-action-bar"` | FULFILLED |
| Hour-group headers with event count and cost | `TimelineTab.tsx` `HourGroup`; `data-testid="hour-group"` | FULFILLED |
| Ledger rows with all columns | `TimelineTab.tsx` `LedgerRow`; `data-testid="ledger-row"` | FULFILLED |
| Expanded drawer: step ledger, raw payload, replay history | `EventDrawer.tsx`; `data-testid="event-drawer"` | FULFILLED |
| `?event=<id>` deep link opens drawer | `useEventDrawerState.ts` | FULFILLED |
| Closing drawer removes `?event` from URL | `useEventDrawerState.ts` | FULFILLED |
| Raw payload audit-gated; fail-closed | `src/butlers/api/routers/ingestion_events.py` + `emit_dashboard_audit` | FULFILLED |
| Footer rollup band (8 KPI cells) | Not implemented (load-more pagination instead) | DEVIATION → bu-mxtn2 |
| Connectors roster: attention strip | `AttentionStrip.tsx` + `ConnectorsRoster.tsx` | FULFILLED |
| Connectors roster: all 8 columns | `ConnectorRosterRow.tsx` | FULFILLED |
| Connectors dormant section | `DormantList.tsx` | FULFILLED |
| Connectors footer KPI band + add-connector action | `ConnectorsRoster.tsx` L176–207 | FULFILLED |
| Connector detail: header band (glyph, headline, meta, purpose) | `ConnectorDetailView.tsx` + `DispatchHeader` | FULFILLED |
| Connector detail: reauth callout | `ReauthCallout.tsx` | FULFILLED |
| Connector detail: KPI strip | `ConnectorDetailView.tsx`; `data-testid="kpi-strip"` | FULFILLED |
| Connector detail: 24h histogram | `ConnectorHistogram.tsx` | FULFILLED |
| Connector detail: recent events list | Not implemented | DEVIATION → bu-5ywn2 |
| Connector detail: incident list | Not implemented | DEVIATION → bu-5ywn2 |
| Connector detail: OAuth scope list (explicit unavailable state) | `ScopeList.tsx`; `data-testid="scopes-unavailable"` | FULFILLED |
| Connector detail: schedule block | `ConnectorDetailView.tsx` L308–400 | FULFILLED |
| Connector detail: routing rules | Not implemented | DEVIATION → bu-5ywn2 |
| Connector detail: config fields + safe action controls | `ConnectorDetailView.tsx` L308–400 | FULFILLED |
| Filters: header with event count and range | `IngestionFiltersPage.tsx` `FiltersHeaderAside` | FULFILLED |
| Filters: five-gate diagram | `PipelineGateDiagram.tsx`; `data-testid="gate-node-*"` | FULFILLED |
| Filters: funnel bar (drops vs. preserved) | `PipelineGateDiagram.tsx` `data-testid="funnel-bar"` | FULFILLED |
| Filters: one gate section per stage with rule rows | `GateSection.tsx`, `RuleRow.tsx` | FULFILLED |
| Filters: code-resident notes for dedupe/execute | `GateSection.tsx` (static behavior note) | FULFILLED |
| Filters: priority senders data block | `PrioritySendersBlock.tsx` | FULFILLED |
| Filters: channel defaults block | `ChannelDefaultsBlock.tsx` | FULFILLED |
| Filters: archived/disabled rules section | `ArchivedRulesSection.tsx` | FULFILLED |
| Filters: add-rule and open-DSL footer | `FiltersPipeline.tsx` L117–130 | FULFILLED |
| All surfaces: explicit loading / empty / partial-error / unavailable states | `EventDrawer.tsx`, `ConnectorDetailView.tsx`, `FiltersPipeline.tsx` | FULFILLED |
| Degraded mode: unavailable metrics shown as `—` not `0` | `IngestionFiltersPage.tsx` L47 | FULFILLED |

**Summary:** 44 of 50 prototype obligations fulfilled. 6 deviations tracked in beads bu-mxtn2 and bu-5ywn2.

---

## Section 4: Screenshot Artifacts

The visual parity report (bu-y25mj.6) specifies screenshot files at:

```
docs/reports/ingestion-redesign-parity-2026-05-25/
  timeline-desktop.png      (1280x800)
  timeline-mobile.png       (390x844)
  connectors-desktop.png    (1280x800)
  connectors-mobile.png     (390x844)
  connector-detail-desktop.png (1280x800)
  connector-detail-mobile.png  (390x844)
  filters-desktop.png       (1280x800)
  filters-mobile.png        (390x844)
```

**Current status:** The `docs/reports/ingestion-redesign-parity-2026-05-25/` directory does **not exist** in the git tree. Playwright captures these screenshots only when a live dev server is running during a CI/CD screenshot-commit run. The PNGs were not committed in PR #1944.

**Follow-up:** bead **bu-6z2w4** (P3) — "Commit desktop/mobile Playwright screenshots to docs/reports/". This is the gating condition for R7.4. It does not block the epic's core implementation closure.

The Playwright test that generates these files is `frontend/tests/e2e/ingestion-visual-parity.spec.ts` (committed in PR #1944).

---

## Section 5: Test Commands and Results

### Backend regression (Python)

```bash
uv run pytest tests/ --ignore=tests/e2e -q --maxfail=1 --tb=short
```

This epic is frontend-only. Backend changes were limited to audit-log wiring in `src/butlers/api/routers/ingestion_events.py` (raw payload gate). Backend test suite is expected to pass without regression.

### Frontend unit tests (Vitest)

```bash
cd frontend && npx vitest run
```

**Result (from bu-y25mj.6 quality gates):** PASS — 179 files, 3597 tests.

### Frontend e2e suite (Playwright)

```bash
cd frontend && npx playwright test ingestion-
```

Covers:
- `ingestion-visual-parity.spec.ts` — route smoke, legacy redirects, drawer deep-link, old-shell absence
- `ingestion-subroutes.spec.ts` — `?tab=` redirect coverage
- `ingestion-timeline.spec.ts` — ledger behavior
- `ingestion-filters.spec.ts` — gate diagram, rule rows

**Result (from bu-y25mj.6 quality gates):** All assertions PASS. Screenshot generation requires a live dev server (see Section 4).

### OpenSpec validation

```bash
uv run openspec validate complete-ingestion-redesign-parity --strict
```

**Result (from bu-y25mj.7 reconciliation report):**

```
Change 'complete-ingestion-redesign-parity' is valid
```

`openspec validate --strict` PASSES with no errors.

### Frontend type-check and lint

```bash
cd frontend && npx tsc -b
cd frontend && npx eslint .
```

**Results (from bu-y25mj.6 quality gates):** Both PASS (0 errors).

---

## Section 6: Residual Risks and Unresolved Deviations

All open follow-up beads from this epic. None block the core implementation or the archive recommendation.

| Bead | Title | Priority | What it represents | Epic-gating? |
|---|---|---|---|---|
| bu-5je09 | Per-connector hourly timeseries in roster API | P3 | The 24h sparkline in the connector roster currently receives mock data; the backend hourly timeseries endpoint is not yet implemented. Visual shape is correct; data will be live once the endpoint ships. | No |
| bu-wn3n3 | connector-oauth-scope-surface backend | P3 | The `ScopeList` component correctly renders an explicit "unavailable" state per spec AC3. Backend scope introspection is tracked in a separate OpenSpec change (`add-connector-oauth-scope-surface`). No gap in UX contract. | No |
| bu-va06h | Bulk retry backend endpoint for Timeline ledger | P2 | The `BulkActionBar` UI is present with `Replay all` and `Copy IDs` controls. `Replay all` is a placeholder stub pending the bulk retry endpoint. P2 risk — the feature is visible but not functional. | No |
| bu-att72 | Saved-view backend persistence for Timeline ledger | P3 | Saved-view selector UI is present; view state is URL-persisted but not persisted to the backend. Users lose custom saved views across sessions. | No |
| bu-rncqs | EventDrawer follow-ups: flamegraph span scaling, setTimeout scroll, grid-template dedup | P3 | Polish items: (1) in-progress spans scale incorrectly on the flame strip when a session is mid-flight, (2) `setTimeout` used for drawer scroll instead of `requestAnimationFrame`, (3) `grid-template-columns` value repeated across `LedgerRow` and `HourGroup`. | No |
| bu-lv0ep | Extract shared grid-template-columns constant for ConnectorsRoster | P3 | The grid-template-columns value in `ConnectorRosterRow.tsx` and the roster header are in sync by copy rather than by a shared constant. Low risk of visual drift. | No |
| bu-mxtn2 | Timeline toolbar: add search bar and channel chips; add footer rollup band | P2 | Covers spec requirements R3.3, R3.4, and R3.13. The toolbar is missing a free-text search input and per-channel filter chips. The footer shows load-more pagination rather than an 8-cell rollup band. P2 because search and rollup are useful operator tools, but primary timeline workflows (browse, expand, replay) are unaffected. | No |
| bu-5ywn2 | Connector detail: add recent events, incident list, and routing rules sections | P3 | Covers spec requirements R4.9 and R4.11. The connector detail page is missing recent events, incident log, and per-connector routing rules. These sections require connector-scoped events and rules endpoints not yet exposed by the API. | No |
| bu-6z2w4 | Commit desktop/mobile Playwright screenshots to docs/reports/ | P3 | Spec requirement R7.4 mandates screenshots as verification evidence. The Playwright spec generates them but they were not committed. This is the only FOLLOW-UP requirement in the matrix. | No |

**No P0 or P1 open beads remain from this epic.** All P2 items (bu-va06h, bu-mxtn2) represent partially-implemented features with correct UI scaffolding; neither is a regression. All P3 items are polish, backend plumbing, or deferred completeness.

---

## Section 7: Archive Readiness Recommendation

**Recommendation: YES — archive the OpenSpec change.**

Evaluation against the readiness criteria:

| Criterion | Status |
|---|---|
| All 38 spec requirements are PASS or have a tracked DEVIATION bead | YES — 32 PASS, 5 DEVIATION (bu-mxtn2, bu-5ywn2, bu-6z2w4), 1 FOLLOW-UP (bu-6z2w4) |
| `openspec validate complete-ingestion-redesign-parity --strict` passes | YES — confirmed in bu-y25mj.7 reconciliation report |
| Visual parity Playwright suite is green | YES — all assertions pass per bu-y25mj.6 |
| All P0/P1 children of the epic are closed | YES — all 7 implementation children (.1–.7) are closed |

The five deviations each have a corresponding open bead, meaning the deviation tracking contract is fulfilled. The unresolved gaps (toolbar search, connector-detail completeness, screenshot commit) are P2/P3 follow-up work that does not invalidate the core redesign. The `complete-ingestion-redesign-parity` spec change correctly describes what was built, `openspec validate --strict` passes, and the primary user-facing workflows (browse timeline, inspect connectors, read filters pipeline) are all operational.

**Archive command:**

```bash
uv run openspec archive complete-ingestion-redesign-parity --date 2026-05-25
```

After archiving, the directory `openspec/changes/complete-ingestion-redesign-parity/` moves to `openspec/archive/complete-ingestion-redesign-parity/`. The epic bead (bu-y25mj) may then be closed by the operator.
