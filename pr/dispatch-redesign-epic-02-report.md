# Epic 02: System Runtime Summary Card — Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-bm58r.3
Reporter: Beads Worker (automated reconciliation)

---

## 1. Merged PRs

### PR #1494 — feat: RuntimeSummaryKpi 4-cell KPI card from existing hooks [bu-bm58r.1]

**Merged:** 2026-05-09T16:32:12Z
**Commit:** 5a1eddc0

**Files changed:**
- `frontend/src/components/overview/RuntimeSummaryKpi.tsx` — new component
- `frontend/src/components/overview/RuntimeSummaryKpi.test.tsx` — RTL tests (230 lines)
- `frontend/src/pages/DashboardPage.tsx` — integrated `<RuntimeSummaryKpi />` into overview spine
- `frontend/src/pages/DashboardPage.test.tsx` — updated page tests

**Summary:** Introduced the `RuntimeSummaryKpi` component as a dedicated 4-cell KPI card (total butlers / healthy / sessions_24h / pending approvals). Component uses only existing hooks (`useButlers`, `useApprovalMetrics`). DashboardPage places it in the left narrative column after AttentionList, matching the RECIPES.md spine order. RTL tests pin all four cells, loading state (`—`), zero state (`0`), staffer exclusion, and accessible aria-label.

---

### PR #1497 — feat(dashboard): wire 30s polling + stale-while-revalidate to RuntimeSummaryKpi [bu-bm58r.2]

**Merged:** 2026-05-09T16:45:58Z
**Commit:** 1c1e0b0b

**Files changed:**
- `frontend/src/hooks/use-butlers.ts` — added `staleTime: 30_000` (refetchInterval was already present)
- `frontend/src/hooks/use-approvals.ts` — added `refetchInterval: 30_000` and `staleTime: 30_000` to `useApprovalMetrics`
- `frontend/src/hooks/use-butlers-polling.test.ts` — new test file verifying polling options
- `frontend/src/components/overview/RuntimeSummaryKpi.test.tsx` — stale-while-revalidate test added

**Summary:** Completed the 30s polling contract by ensuring both hooks pass `refetchInterval: 30_000` and `staleTime: 30_000` to TanStack Query. This gives the KPI card the same background-refresh behavior as the butler-list page, keeps stale data visible during background refetches (no flicker to loading state), and shares the TanStack cache entry with ButlersPage.

---

## 2. No New HTTP Endpoints (Verified)

**Result: PASS**

Neither PR touched any file under `src/butlers/api/`. Both merge commits confirm zero diffs to the Python backend. The KPI card is sourced exclusively from existing hooks backed by existing routes:

| Data cell | Hook | Existing endpoint |
|-----------|------|-------------------|
| Total butlers | `useButlers()` → `getButlers()` | `GET /api/system/butlers` |
| Healthy butlers | `useButlers()` (filtered) | same |
| Sessions · 24h | `useButlers()` (aggregated) | same (`sessions_24h` field per butler) |
| Pending approvals | `useApprovalMetrics()` → `getApprovalMetrics()` | `GET /api/approvals/metrics` |

Epic acceptance criterion 1 (no new endpoints) is satisfied.

---

## 3. Design-Language KPI Strip Compliance

**Result: PASS**

`DESIGN_LANGUAGE.md §4b` specifies:

```
mono-eyebrow  (10px, muted, uppercase)
mega-number   (32px, sans 500, tracking -0.03em, tnum)
mono-delta    (10px, muted)
No background fills. No card chrome.
```

`KpiStrip.tsx` implements this exactly:
- Eyebrow: `fontFamily: var(--font-mono)`, `fontSize: 10px`, `letterSpacing: 0.14em`, `uppercase`, `color: var(--muted-foreground)` — compliant.
- Value: `fontFamily: var(--font-sans)`, `fontSize: 32px`, `fontWeight: 500`, `letterSpacing: -0.03em`, `className="tnum"` — compliant.
- Delta: optional, `font-mono`, `10px`, `muted-foreground`, `tnum` — compliant.
- No background fills, no card chrome. Hairline `border-right: 1px solid var(--border)` on cells 0–2, none on cell 3 — compliant.

`RuntimeSummaryKpi` wraps `KpiStrip` in a `<section aria-label="System runtime summary">` with no additional chrome, background, or border. Tabular-nums applied via `tnum` class on all value slots.

`RECIPES.md` spine for Overview page: `... → attention list → KPI strip`. `DashboardPage.tsx:122-125` places `<RuntimeSummaryKpi />` directly after `<AttentionList />` in the left narrative column — compliant.

Epic acceptance criteria 2 and 4 (design-language KPI strip, tabular-nums) are satisfied.

---

## 4. 30s Polling + Cache Sharing

**Result: PASS**

### useButlers (cache key `["butlers"]`)

```typescript
// frontend/src/hooks/use-butlers.ts
useQuery({
  queryKey: ["butlers"],
  queryFn: () => getButlers(),
  refetchInterval: 30_000,  // 30s background poll
  staleTime: 30_000,        // stale-while-revalidate
});
```

- Polling cadence: 30s ✓
- Stale-while-revalidate: ✓ (data visible during background refetch; no flicker to loading)
- Cache key `["butlers"]` is identical to the key used by ButlersPage. A single network call serves both surfaces.

### useApprovalMetrics (cache key `["approvals", "metrics"]`)

```typescript
// frontend/src/hooks/use-approvals.ts (useApprovalMetrics)
useQuery({
  queryKey: approvalKeys.metrics(),  // ["approvals", "metrics"]
  queryFn: () => getApprovalMetrics(),
  refetchInterval: 30_000,
  staleTime: 30_000,
});
```

- Polling cadence: 30s ✓
- Stale-while-revalidate: ✓

### Test coverage

`use-butlers-polling.test.ts` asserts:
- `useButlers`: `refetchInterval=30_000`, `staleTime=30_000`, `queryKey=["butlers"]`
- `useApprovalMetrics`: `refetchInterval=30_000`, `staleTime=30_000`

`RuntimeSummaryKpi.test.tsx (stale-while-revalidate)` verifies that when `isFetching=true, isLoading=false`, cached values remain visible and no `—` placeholder appears.

Epic acceptance criteria 3 and 5 (30s polling, shared cache, stale-while-revalidate) are satisfied.

---

## 5. Loading and Zero-State Branches

**Result: PASS**

`RuntimeSummaryKpi` uses a combined `isLoading = butlersLoading || approvalsLoading` guard:
- Loading: all four cells render `"—"` until both data sources are ready (prevents partial-render layout shifts).
- Zero-state: numeric `0` rendered with `tnum`, not a dash.
- Stale/background-refetch: cached values remain visible (isFetching does not affect cell display).

RTL tests pin all three branches.

---

## 6. Acceptance Criteria Checklist

| # | Criterion | Status |
|---|-----------|--------|
| 1 | No new HTTP endpoints introduced | PASS |
| 2 | Card visible on `/` (DashboardPage) via `<RuntimeSummaryKpi />` | PASS |
| 3 | Four stats sourced via existing hooks (`useButlers`, `useApprovalMetrics`) | PASS |
| 4 | Loading, error, and zero-state branches render predictable copy | PASS |
| 5 | Conforms to design-language KPI strip rules (no card chrome, tabular-nums, hairline grid) | PASS |
| 6 | 30s polling matches butler-list page spec | PASS |
| 7 | Shared cache with butler-list page (no duplicate network calls) | PASS |
| 8 | Stale data visible during background refetch (no flicker) | PASS |

All eight criteria met. No gaps found.

---

## 7. Gaps and Follow-Up Items

None discovered. The implementation is complete and spec-compliant.

Possible future improvements (not gaps, not required for epic closure):
- An RTL test using fake timers asserting the UI updates after a simulated 30s interval was not included. The `use-butlers-polling.test.ts` approach (asserting options passed to `useQuery`) is an acceptable substitute and simpler to maintain. If the team wants a higher-fidelity timer test, it can be added as a separate task.

---

## 8. Conclusion

Epic 02 (bu-bm58r) is fully implemented across PRs #1494 and #1497. All acceptance criteria pass. No new backend endpoints were introduced. The KPI card conforms to design-language rules, uses existing hooks, polls at 30s, shares the TanStack cache with the butler-list page, and keeps stale data visible during background refetches.
