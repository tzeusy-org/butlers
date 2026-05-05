# Vertical D Reconciliation — Gen-2 (bu-e3248)

Date: 2026-05-06
Bead: bu-e3248 — Reconcile spec-to-code (gen-2) for vertical D after gap bead closure
Epic: bu-2okpr — Frontend redesign D: home page — sessions as hero

---

## Bootstrap Proof

- Worktree: `/home/tze/gt/butlers/.worktrees/parallel-agents/bu-e3248`
- Branch: `agent/bu-e3248`
- Context validator: PASS (`assert_worker_context.py` returned `status: ok`)

---

## Gap Bead Status

All five gap beads tracked since gen-1 are confirmed closed:

| Bead | Title | Status | PR |
|------|-------|--------|----|
| bu-ch3uj | Update frontend.md Overview archetype to reflect vertical D | closed | #1380 |
| bu-3ztj8 | Fix SessionStripeChart empty state text | closed | #1420 |
| bu-2okpr.8 | Verify and complete dashboard-hero-contract openspec change | closed | #1388 |
| bu-ewwz4 | Use shared ChartSkeleton in SessionStripeChart loading state | closed | #1381 |
| bu-u4s65 | Wire useAutoRefresh hook in session-stripe-utils | closed | #1383 |

---

## Spec Sync Status

`openspec/changes/dashboard-hero-contract/` exists and contains the authoritative
`specs/dashboard-overview/spec.md`. Tasks 7.1 (opsx:sync) and the corresponding
`dashboard-overview/` promotion into `openspec/specs/` have **not yet been run**.
The spec content was corrected in PR #1388 (bu-2okpr.8), but the sync step that
copies `changes/dashboard-hero-contract/specs/` into `openspec/specs/dashboard-overview/`
has not been executed.

This is a known deferred step (tasks.md item 7.1, still unchecked). All implementation
work is complete and correct. The sync is a tooling promotion step, not a code gap.
See "Remaining Work" below.

---

## Full AC Re-Audit

### AC 1: SessionStripeChart empty state text matches spec

**Spec**: `spec.md:70` — SHALL render "No sessions in the past 24 hours"

**Evidence**: `SessionStripeChart.tsx:172` renders exactly `No sessions in the past 24 hours`

**Status: PASS**

---

### AC 2: ChartSkeleton used in loading state

**Spec**: `spec.md:79` — skeleton SHALL use the standard `ChartSkeleton` component

**Evidence**:
- `SessionStripeChart.tsx:21`: `import { ChartSkeleton } from "@/components/skeletons"`
- `SessionStripeChart.tsx:152`: `return <ChartSkeleton height="h-[200px]" testId="session-stripe-skeleton" />`
- No local `SessionStripeChartSkeleton` remains in the file.

**Status: PASS**

---

### AC 3: useAutoRefresh hook wired

**Spec**: `spec.md:96` — refresh SHALL use the existing `useAutoRefresh` hook pattern

**Evidence**:
- `SessionStripeChart.tsx:23`: `import { useAutoRefresh } from "@/hooks/use-auto-refresh"`
- `SessionStripeChart.tsx:126`: `const { refetchInterval } = useAutoRefresh(60_000)`
- `SessionStripeChart.tsx:127`: `const { data, isLoading, isError } = useSessionStripeData(windowHours, refetchInterval)`
- `session-stripe-utils.ts:138`: `useSessionStripeData(windowHours = 24, refetchInterval: number | false = 60_000)` — accepts the interval as a parameter, no longer hardcodes it internally.

**Status: PASS**

---

### AC 4: Dashboard overview (/ route) renders sessions-as-hero with correct 5-region structure

**Spec**: `spec.md` requires 5 regions in order: (1) session stripe, (2) recent moments, (3) secondary card grid, (4) QA widget, (5) demoted stat strip.

**Evidence** (from `DashboardPage.tsx`):
- `DashboardPage.tsx:151`: `<Page archetype="overview" title="Overview">`
- Region 1 (line 153-161): `<Card>` containing `<SessionStripeChart butlers={butlers} />`
- Region 2 (line 163-172): `<Card>` containing `<RecentMoments limit={7} />`
- Region 3 (line 174-206): `<div className="grid gap-6 lg:grid-cols-2">` with Failed Notifications card + `<IssuesPanel>`
- Region 4 (line 208-209): `<QaWidget />` standalone card
- Region 5 (line 211-233): `<div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-3">` with four `<StatItem>` entries — no Card wrapper, text-sm weight
- Zero `TopologyGraph` references in `DashboardPage.tsx`

**Status: PASS**

---

### AC 5: frontend.md Overview archetype updated (bu-2okpr.6 AC7)

**Evidence** (from `about/lay-and-land/frontend.md`):
- Lines 139-175: Overview archetype now describes the post-vertical-D pattern with
  hero chart → feed → secondary cards → QA widget → demoted stat strip.
- Lines 543-554: Per-archetype layout rules section lists all 5 regions for `DashboardPage`.
- TSX snippet in lines 148-167 matches actual `DashboardPage.tsx` structure.
- Old pre-D text (`text-2xl`, `StatsCard` boilerplate as primary pattern) no longer
  appears in the Overview archetype description.

**Status: PASS**

---

### AC 6: dashboard-hero-contract spec content is complete

**Evidence** (from `openspec/changes/dashboard-hero-contract/specs/dashboard-overview/spec.md`):
- Five requirements defined: Home Page Information Hierarchy, Session Stripe Chart,
  Recent Moments Feed, Secondary Card Grid, QA Widget, Supporting Stat Strip,
  Page Archetype Compliance.
- QA Widget requirement added (region 4) reflecting actual shipped state (PR #1380).
- Spec content was corrected and reviewed in bu-2okpr.8 (PR #1388, reviewed by bu-obbzo
  with 3 factual fixes applied).

**Status: PASS (spec content) / DEFERRED (opsx:sync promotion)**

---

## Per-Bead AC Summary

| Bead | AC | Status |
|------|----|--------|
| bu-2okpr.2 | SessionStripeChart exists, butler-colored, recharts stacked | PASS |
| bu-2okpr.2 | Empty state: "No sessions in the past 24 hours" | PASS (was GAP, fixed by bu-3ztj8) |
| bu-2okpr.2 | Loading state uses shared ChartSkeleton | PASS (was GAP, fixed by bu-ewwz4) |
| bu-2okpr.2 | Auto-refresh uses useAutoRefresh hook | PASS (was GAP, fixed by bu-u4s65) |
| bu-2okpr.3 | RecentMoments exists, Time mode=relative, session link | PASS |
| bu-2okpr.4 | Four-stat strip: text-sm, no Card, border-t separator | PASS |
| bu-2okpr.5 | TopologyGraph removed from DashboardPage, relocated to /system | PASS |
| bu-2okpr.6 | Page archetype="overview", 5-region structure | PASS |
| bu-2okpr.6 | frontend.md Overview archetype updated | PASS (was GAP, fixed by bu-ch3uj) |
| bu-2okpr.8 | dashboard-hero-contract spec content complete | PASS |

---

## Remaining Work

### Deferred: opsx:sync for dashboard-hero-contract (non-blocking)

`openspec/changes/dashboard-hero-contract/specs/dashboard-overview/spec.md` has
correct, reviewed content. The sync step (`openspec sync dashboard-hero-contract`)
that promotes this into `openspec/specs/dashboard-overview/spec.md` has not been run.

This is the final mechanical step to retire the change directory. It does not block
the epic closure — all implementation AC items pass. A follow-up bead or coordinator
action should run the sync to complete the openspec housekeeping.

---

## Quality Gates

- `make lint`: PASS (ruff check: all checks passed)
- Tests: not run (docs-only report; no code changes in this bead)

---

## Final Verdict

**Epic bu-2okpr is closeable.** All 5 gap beads are closed. All implementation AC
items pass against the current codebase. The only remaining item is the opsx:sync
promotion step (mechanical tooling action, not a code gap), which does not block
the epic.

No new gap beads discovered during this audit.
