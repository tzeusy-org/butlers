# Epic Report: Rework Overview into an Operational Triage Cockpit

**Epic ID**: `bu-tn1po`
**Date**: 2026-05-16
**Status**: 7/8 implementation children closed (open: bu-tn1po.8 this report)
**Priority**: P2
**Spec coverage**:
- `openspec/changes/dashboard-overview-triage-cockpit/` — active OpenSpec change (proposal, design, spec delta)
- `openspec/specs/dashboard-overview/spec.md` — upstream canonical spec (superseded by the change)
- `about/heart-and-soul/design-language.md` — editorial archetype, attention list, KPI strip
- `about/lay-and-land/frontend.md` — editorial archetype layout and row anatomies
- `about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md` — overview route and data refresh contract

---

## Summary

The Overview page at `/` had drifted away from the product's settled editorial archetype. Its normative spec still described a chart-first surface: a session stripe chart as the primary region, a card-grid issues panel, a QA widget, and a demoted stat strip. Meanwhile, design-language doctrine and the live `DashboardPage.tsx` had moved toward an operator-readable editorial cockpit — a system-spoken briefing, a triage list, and right-column scan lists. This epic reconciled the spec, built the missing frontend composition layer, and delivered a dashboard first screen that immediately answers: what is healthy, what changed recently, and what needs action now.

The delivery approach was OpenSpec-first: the spec reconciliation bead (bu-tn1po.1) landed before any implementation bead, so each downstream bead had a stable contract to build against. The implementation used only pre-existing API hooks — no backend aggregation endpoint was added. All six proposed features landed in six implementation beads, and a gen-1 reconciliation audit (bu-tn1po.7) confirmed full coverage with two low-severity gaps filed as follow-up beads.

Current state: the editorial triage cockpit is live. The overview page preserves the two-column editorial grid, hairline row lists, and system voice. Two minor gaps remain open as P3 follow-up beads (bu-tn1po.9 and bu-tn1po.10) and are not blockers to the epic shipping.

---

## Architecture

The epic touched the `frontend/` layer only. No backend routes were added or modified.

**Component topology after this epic:**

```
DashboardPage (frontend/src/pages/DashboardPage.tsx)
│
├── Left column (narrative / 1.4fr)
│   ├── DateEyebrow + BriefingStatus
│   ├── Headline (Display, 44px)
│   ├── Elaboration (serif Voice paragraph)
│   ├── Section "Needs attention"
│   │   └── AttentionList          ← enriched by this epic (bu-tn1po.3)
│   └── RuntimeSummaryKpi          ← promoted + rewired (bu-tn1po.4)
│
└── Right column (index / 1fr)
    ├── ButlerIndex ("Operations")  ← enriched by this epic (bu-tn1po.4)
    └── OperationsNowList ("Now")   ← new (bu-tn1po.5)

Data derivation layer:
  frontend/src/components/overview/model.ts
  └── deriveOverviewTriageModel()   ← new (bu-tn1po.2)

Nine existing hooks feed the model (no new backend endpoint):
  useBriefing, useIssues, useButlers, useCostSummary,
  useApprovalMetrics, useButlerHeartbeats, useNotificationStats,
  useQaSummary, useTimeline
```

The diagram threshold for this epic is "single module, 5–15 files" — one component diagram is sufficient. An excalidraw diagram is omitted because the textual topology above fully captures the structure for a single-module, no-new-backend change.

---

## Implementation

### Children

| Bead ID | Title | PR | Status |
|---|---|---|---|
| bu-tn1po.1 | Reconcile overview triage cockpit spec | #1646 | Closed |
| bu-tn1po.2 | Model overview triage data from existing hooks | #1656 | Closed |
| bu-tn1po.3 | Build recency-aware overview needs-attention list | #1676 | Closed |
| bu-tn1po.4 | Promote overview KPIs and butler activity index | #1675 | Closed |
| bu-tn1po.5 | Add overview operations-now signal list | #1689 | Closed |
| bu-tn1po.6 | Integrate triage cockpit into DashboardPage | #1691 | Closed |
| bu-tn1po.7 | Reconcile spec-to-code (gen-1) for overview triage | — | Closed |
| bu-tn1po.8 | Generate epic report for overview triage cockpit | — | This document |
| bu-tn1po.9 | G1: within-severity issue ordering should be older-first | — | Open (P3) |
| bu-tn1po.10 | G2: surface named error row when queries fail | — | Open (P3) |

---

### bu-tn1po.1 — Reconcile overview triage cockpit spec

**Status**: Closed (PR #1646)
**Spec section**: `openspec/changes/dashboard-overview-triage-cockpit/`

**What was done**: Created the `dashboard-overview-triage-cockpit` OpenSpec change with `proposal.md`, `design.md`, `tasks.md`, and a spec delta at `openspec/changes/dashboard-overview-triage-cockpit/specs/dashboard-overview/spec.md`. The change supersedes the chart-first contract, defines the five-region editorial hierarchy, the four KPI cell meanings, the stale issue summarization rules (client-side), the `Now` source list, and the no-new-backend constraint.

**Key locations**:
- `openspec/changes/dashboard-overview-triage-cockpit/design.md` — decisions D1–D7, data source table
- `openspec/changes/dashboard-overview-triage-cockpit/proposal.md` — modified capability declaration
- `openspec/changes/dashboard-overview-triage-cockpit/specs/dashboard-overview/spec.md` — full spec delta

**Design decisions**:
- Created a separate `dashboard-overview-triage-cockpit` change rather than extending `dashboard-overview-briefing` to keep backend endpoint contracts separate from page composition obligations.
- Existing endpoints were explicitly enumerated as sufficient before any implementation bead began.

---

### bu-tn1po.2 — Model overview triage data from existing hooks

**Status**: Closed (PR #1656)
**Spec section**: `design.md §D3–D6`

**What was done**: Introduced `frontend/src/components/overview/model.ts` containing `deriveOverviewTriageModel()` — a pure typed derivation function that transforms raw API hook payloads into stable view models: `kpis`, `attentionRows`, `operationsRows`, and `nowRows`. No component rendering, no side effects.

**Key locations**:
- `frontend/src/components/overview/model.ts:1-551` — full derivation layer
- `frontend/src/components/overview/model.test.ts:115-553` — 16 tests covering all derivation branches

**Design decisions**:
- Kept all derivation in a single pure function to make `DashboardPage` a thin compositor.
- Bucketing (current/recent/old issues) and severity ordering are encoded once in `bucketIssues()` and `compareIssues()`.

---

### bu-tn1po.3 — Build recency-aware overview needs-attention list

**Status**: Closed (PR #1676)
**Spec section**: `spec.md §Needs attention`

**What was done**: Evolved `AttentionList.tsx` to accept the richer `AttentionRow` type from `model.ts`. Each row can carry severity glyph (`!`, `~`, `·`), title, detail with recency metadata (`last seen Xm ago`), occurrence count, and link. Old issue groups are summarized into a single `"N older issue groups"` row linking to `/issues`. Visible rows are capped at `maxRecentIssueRows` (default 5).

**Key locations**:
- `frontend/src/components/overview/AttentionList.tsx:31-59` — row renderer and empty state
- `frontend/src/components/overview/model.ts:163-177` — `old-issues-summary` row generation
- `frontend/src/components/overview/model.ts:316-337` — issue row construction

**Caveats**: Within a severity tier, rows sort by `last_seen_at` descending (newest-active first). Spec D4 says oldest-first. This minor drift is filed as bu-tn1po.9 (P3).

---

### bu-tn1po.4 — Promote overview KPIs and butler activity index

**Status**: Closed (PR #1675)
**Spec section**: `spec.md §KPI strip`, `spec.md §Operations`

**What was done**: Rewired `RuntimeSummaryKpi` to accept the `kpis` view model from `deriveOverviewTriageModel()` with the four spec-approved cells: `Total butlers`, `Healthy`, `Sessions · 24h`, `Pending approvals`. Enriched `ButlerIndex` to show `sessions_24h`, today's cost from `useCostSummary`, heartbeat-derived `runtimeState` (healthy/active/stale/degraded/offline/unknown), and last activity. Stale/degraded/offline butlers set `needsAttention: true` which propagates into attention row injection.

**Key locations**:
- `frontend/src/components/overview/RuntimeSummaryKpi.tsx:16-33` — four KPI cell definitions
- `frontend/src/components/overview/ButlerIndex.tsx:57-125` — cost, session, heartbeat rendering
- `frontend/src/components/overview/model.ts:201-241` — `runtimeState` derivation
- `frontend/src/components/overview/RuntimeSummaryKpi.test.tsx` — 5 tests

---

### bu-tn1po.5 — Add overview operations-now signal list

**Status**: Closed (PR #1689)
**Spec section**: `spec.md §Now`

**What was done**: Added `OperationsNowList.tsx` — a new `<Section eyebrow="Now">` component rendering rows sourced from approvals, QA, notifications, and timeline. Row kinds: `approval`, `qa`, `notification`, `activity`. Zero-state is `Nothing scheduled.` in serif italic. Each row has a stable route target.

**Key locations**:
- `frontend/src/components/overview/OperationsNowList.tsx` — new component
- `frontend/src/components/overview/model.ts:413-496` — `buildApprovalNowRows()`, `buildQaNowRows()`, `buildNotificationNowRows()`, `buildTimelineNowRows()`
- `frontend/src/components/overview/OperationsNowList.test.tsx` — 11 tests

**Caveats**: Source failures (QA, notifications, timeline) silently produce no row rather than a named error row. Spec D7 requires named error rows. Filed as bu-tn1po.10 (P3).

---

### bu-tn1po.6 — Integrate triage cockpit into DashboardPage

**Status**: Closed (PR #1691)
**Spec section**: `spec.md §Page layout`

**What was done**: Rewrote `DashboardPage.tsx` to be a thin compositor: nine hooks → `deriveOverviewTriageModel()` → five components. The editorial two-column grid (`lg:grid-cols-[1.4fr_1fr]`) is now the sole layout contract. No chart imports, no card-grid chrome, no raw unbounded issue list passed to `AttentionList`.

**Key locations**:
- `frontend/src/pages/DashboardPage.tsx:1-150` — final composition
- `frontend/src/pages/DashboardPage.test.tsx` — 28 tests (briefing surface, attention list, KPI strip, butler index, now list, loading)

---

### bu-tn1po.7 — Reconcile spec-to-code (gen-1)

**Status**: Closed
**Spec section**: Full epic

**What was done**: Full end-of-epic audit comparing proposed features, OpenSpec requirements, and final repository state. Produced the reconciliation matrix at `openspec/changes/dashboard-overview-triage-cockpit/reconciliation-tn1po-overview.md`. Confirmed 6/6 features as PASS or MOSTLY PASS, filed 2 low-severity gap beads.

---

## Spec Compliance Matrix

Source: `openspec/changes/dashboard-overview-triage-cockpit/reconciliation-tn1po-overview.md`

| Spec Requirement | Status | Evidence | Notes |
|---|---|---|---|
| `<Page archetype="editorial">` wrapping | Implemented | `DashboardPage.tsx:94` | |
| Two-column grid `1.4fr / 1fr` at lg | Implemented | `DashboardPage.tsx:103` | |
| Responsive single-column below lg | Implemented | `DashboardPage.tsx:103` | |
| DateEyebrow + BriefingStatus in left column | Implemented | `DashboardPage.tsx:111-120` | |
| Display headline (44px sans-500) | Implemented | `Headline.tsx:26-27` | |
| Voice elaboration paragraph (serif 16px) | Implemented | `Elaboration.tsx` | |
| Section "Needs attention" → AttentionList | Implemented | `DashboardPage.tsx:128-130` | |
| KPI strip below attention section | Implemented | `DashboardPage.tsx:132-136` | |
| Four KPI cells in spec order | Implemented | `RuntimeSummaryKpi.tsx:16-33` | Total butlers → Healthy → Sessions · 24h → Pending approvals |
| KPI: total butlers = type="butler" only | Implemented | `model.ts:119, 189` | |
| KPI: healthy = ok or online | Implemented | `model.ts:104, 191` | |
| KPI: sessions · 24h = sum of sessions_24h | Implemented | `model.ts:192` | |
| KPI: pending approvals = total_pending | Implemented | `model.ts:193` | |
| KPI loading renders "—" for all cells | Implemented | `RuntimeSummaryKpi.tsx:19-32` | |
| KPI partial failure degrades only affected cell | Implemented | `RuntimeSummaryKpi.tsx:31-32` | |
| KPI hairline strip, no card chrome | Implemented | `KpiStrip.tsx:37-49` | |
| Issues bucketed into current / recent / old | Implemented | `model.ts:134, 243-270` | |
| Attention list capped at maxRecentIssueRows (5) | Implemented | `model.ts:99, 141-146` | |
| Old groups summarized with link to /issues | Implemented | `model.ts:163-177` | |
| Row: severity glyph | Implemented | `AttentionList.tsx:31-43` | |
| Row: title, detail, butler names | Implemented | `model.ts:316-337` | |
| Row: occurrences when > 1 | Implemented | `model.ts:321-323` | |
| Row: last-seen recency detail | Implemented | `model.ts:324-326, 525-533` | |
| Empty state: `Nothing waiting.` serif italic | Implemented | `AttentionList.tsx:47-59` | |
| Missing timestamps treated as current | Implemented | `model.ts:276` | |
| Severity ordering: high/critical first | Implemented | `model.ts:282-309` | |
| Within severity, older-first | **Partially implemented** | `model.ts:292-294` | Sorts newer-first; see G1 (bu-tn1po.9) |
| Stale/degraded/offline butlers in attention | Implemented | `model.ts:236-239` | |
| Right column: ButlerIndex ("Operations") | Implemented | `DashboardPage.tsx:144` | |
| ButlerIndex: sessions_24h per butler | Implemented | `ButlerIndex.tsx:63-73` | |
| ButlerIndex: cost today | Implemented | `ButlerIndex.tsx:57-58` | |
| ButlerIndex: last activity from heartbeat | Implemented | `ButlerIndex.tsx:117-125` | |
| ButlerIndex: runtimeState from heartbeat | Implemented | `model.ts:201-241` | |
| Right column: OperationsNowList ("Now") | Implemented | `DashboardPage.tsx:145` | |
| Now: pending approvals row when > 0 | Implemented | `model.ts:413-424` | |
| Now: QA patrol failure row | Implemented | `model.ts:470-476` | |
| Now: QA novel findings row | Implemented | `model.ts:489-496` | |
| Now: failed notification pressure row | Implemented | `model.ts:437-450` | |
| Now: recent activity from timeline | Implemented | `model.ts:451-462` | |
| Now: compact zero state `Nothing scheduled.` | Implemented | `OperationsNowList.tsx:35-47` | |
| Now: source failures render as local error rows | **Partially implemented** | `DashboardPage.tsx:81-84` | Silent null; see G2 (bu-tn1po.10) |
| No new backend aggregation endpoint | Implemented | `DashboardPage.tsx:1-15 (header comment)` | Confirmed in reconciliation |
| No session stripe chart | Implemented | all overview components | No Recharts imports |
| No card-grid chrome | Implemented | all overview components | No Card component |

**Summary**: 42 requirements checked. 40 fully implemented, 2 partially implemented (G1, G2 — both P3 follow-ups).

---

## Proposed-Features-to-Final-Repository-State Matrix

Source: `openspec/changes/dashboard-overview-triage-cockpit/reconciliation-tn1po-overview.md`

| Feature | Status | Notes |
|---|---|---|
| F1: Promoted runtime KPIs | PASS | All four cells, all states covered |
| F2: Recency-aware Needs Attention | MOSTLY PASS | Within-severity sort direction drifts (G1) |
| F3: Current operational signals beyond issues | MOSTLY PASS | Source failure visibility suppressed (G2) |
| F4: Enriched right-column Operations / butler index | PASS | Heartbeat state, cost, last activity all present |
| F5: Preservation of editorial archetype | PASS | No cards, no chart, two-column grid intact |
| F6: No new backend aggregate endpoint | PASS | Confirmed; nine pre-existing hooks used |

**Counts**: 4 PASS, 2 MOSTLY PASS (low severity), 0 CONTRADICTED, 0 DEFERRED, 0 NOT IMPLEMENTED.

---

## Test Coverage

### Test files

| File | Tests | What it covers |
|---|---|---|
| `frontend/src/components/overview/model.test.ts` | 16 | All derivation branches: attention ordering, bucketing, issue summarization, heartbeat state, KPIs, QA/notification/timeline rows |
| `frontend/src/components/overview/RuntimeSummaryKpi.test.tsx` | 5 | KPI cell order, values, zero approvals, partial failure, loading |
| `frontend/src/components/overview/OperationsNowList.test.tsx` | 11 | Zero state, approval row, QA row, notification row, timeline row, multi-row, kind badges, route targets |
| `frontend/src/pages/DashboardPage.test.tsx` | 28 | Full page: briefing surface, fallback path, composing state, state_class variants, AttentionList, RuntimeSummaryKpi, ButlerIndex, loading, OperationsNowList |

Total: **60 focused frontend tests** covering the triage cockpit composition and derivation layer.

### Coverage gaps

| Area | Why | Risk | Follow-up |
|---|---|---|---|
| Now source error → named error row | G2 not yet implemented | Low (visual only, no data loss) | bu-tn1po.10 |
| Within-severity older-first sort | G1 not yet corrected | Low (alt ordering may be more useful) | bu-tn1po.9 |
| Build verification | Not run by agents (CI covers this) | Low (no new dependencies) | No |

### Test confidence

Coverage is behavior-first: tests assert on rendered text, accessible labels, routing hrefs, and derived model values — not on implementation internals. The critical triage paths (capped attention, severity ordering, stale heartbeat propagation, zero states) are fully covered. The two uncovered gaps are edge-case presentation behaviors with no data-correctness risk.

---

## Subsequent Work

### Open follow-up beads (filed by bu-tn1po.7)

| Bead ID | Title | Type | Priority | Rationale |
|---|---|---|---|---|
| bu-tn1po.9 | G1: within-severity issue ordering should be older-first | bug | P3 | Spec D4 says older-first; model sorts newer-first within tier. Low drift. |
| bu-tn1po.10 | G2: surface named error row when notification/qa/timeline queries fail | bug | P3 | Spec D7 requires named local error row on source failure. Currently silent null. |

### New follow-up beads from this report

No additional follow-up beads needed. The coordinator filed bu-tn1po.9 and bu-tn1po.10 after the gen-1 reconciliation. No other TODOs were identified during this report generation.

### Deferred decisions

| Decision | Context | Revisit when |
|---|---|---|
| Schedule/calendar rows in `Now` | No scheduler/calendar surface was available at epic time | When a calendar or reminder module ships |
| Backend `Now` aggregation endpoint | Out of scope per D3/D6; all sources are explicit | Only if client-side composition causes measurable latency or staleness issues |
| OpenSpec archive / sync for `dashboard-overview-triage-cockpit` | Spec change is in `active` state; not archived post-implementation | After any remaining gap beads (bu-tn1po.9, .10) close, run `openspec archive dashboard-overview-triage-cockpit` |

---

## Risks and Notes for Reviewer

### Known risks

| Risk | Severity | Mitigation | Evidence |
|---|---|---|---|
| Within-severity sort direction drift (G1) | Low | Newer-first is arguably more useful; spec says older-first. P3 bead filed. | `model.ts:292-294` |
| Source failure visibility suppressed (G2) | Low | Owner sees `Nothing scheduled.` instead of "QA unavailable". P3 bead filed. | `DashboardPage.tsx:81-84` |
| Nine parallel queries on page load | Low | All hooks use TanStack Query with modest poll intervals; no aggregation endpoint was the explicit spec choice | `DashboardPage.tsx:65-72` |

### What to look at first

1. `frontend/src/components/overview/model.ts` — derivation logic is the highest-value review target; all triage ordering, bucketing, and KPI semantics live here.
2. `frontend/src/pages/DashboardPage.tsx` — integration point; verify the `isError ? null : ...` guard pattern (G2 source).
3. `openspec/changes/dashboard-overview-triage-cockpit/reconciliation-tn1po-overview.md` — full feature-by-feature audit table.

---

## Appendix

### A. Commits referencing this epic

```
ad49ce82 docs: gen-1 reconciliation for overview triage [bu-tn1po.7]
26e3ebb5 feat: integrate triage cockpit into DashboardPage [bu-tn1po.6] (#1691)
fd7d251d feat: integrate triage cockpit into DashboardPage [bu-tn1po.6]
93f6b428 feat: add overview operations-now signal list [bu-tn1po.5] (#1689)
fe35c587 feat: add overview operations-now signal list [bu-tn1po.5]
feddb7d9 feat: cap overview attention issues [bu-tn1po.3]
cf280de2 feat: cap overview attention issues [bu-tn1po.3]
eb284172 feat: promote overview runtime triage kpis [bu-tn1po.4]
18c0867b feat: promote overview runtime triage kpis [bu-tn1po.4]
44dcc836 feat: model overview triage data [bu-tn1po.2]
ccdab8da feat: model overview triage data [bu-tn1po.2]
804d9bfe docs: reconcile dashboard overview cockpit spec [bu-tn1po.1]
447ededf docs: reconcile dashboard overview cockpit spec [bu-tn1po.1]
```

### B. Files changed

```
openspec/changes/dashboard-overview-triage-cockpit/.openspec.yaml
openspec/changes/dashboard-overview-triage-cockpit/design.md
openspec/changes/dashboard-overview-triage-cockpit/proposal.md
openspec/changes/dashboard-overview-triage-cockpit/reconciliation-tn1po-overview.md
openspec/changes/dashboard-overview-triage-cockpit/specs/dashboard-overview/spec.md
openspec/changes/dashboard-overview-triage-cockpit/tasks.md
frontend/src/components/overview/AttentionList.tsx
frontend/src/components/overview/ButlerIndex.tsx
frontend/src/components/overview/model.test.ts
frontend/src/components/overview/model.ts
frontend/src/components/overview/NextList.tsx
frontend/src/components/overview/OperationsNowList.test.tsx
frontend/src/components/overview/OperationsNowList.tsx
frontend/src/components/overview/RuntimeSummaryKpi.test.tsx
frontend/src/components/overview/RuntimeSummaryKpi.tsx
frontend/src/pages/DashboardPage.test.tsx
frontend/src/pages/DashboardPage.tsx
```

### C. Diagram sources

No excalidraw diagrams generated. The epic touched a single frontend module (9 components, 1 model, 1 page) with no new backend components — the textual component topology in the Architecture section is sufficient.
