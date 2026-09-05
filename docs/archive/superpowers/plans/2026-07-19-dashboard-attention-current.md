> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Implementation plan executed and archived as the make-dashboard-attention-current change.
> **Successor:** `openspec/changes/archive/2026-07-19-make-dashboard-attention-current`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Dashboard Attention Currentness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Overview and briefing describe live attention rather than historical aggregates, and refresh briefing state after a QA breaker reset.

**Architecture:** Keep the existing dashboard endpoints and composition boundaries. The backend applies closed 12-hour audit and 24-hour notification/QA windows before briefing classification; the frontend uses the same captured `since`/`until` notification window in the pure model and drill-down query. The existing in-process cache is invalidated only after the reset marker writes successfully.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, pytest, React 18, TypeScript, TanStack Query, Vitest, OpenSpec.

---

### Task 1: Protect the QA Reset Cache Boundary

**Files:**

- Modify: `tests/dashboard/test_briefing_cache_invalidation.py`
- Modify: `src/butlers/api/routers/qa.py`

- [x] **Step 1: Write the failing cache-reset tests**

```python
cache = BriefingCache(ttl_seconds=300)
cache.set("owner", {"state_class": "urgent"})
response = await client.post("/api/qa/circuit-breaker/reset")
assert response.status_code == 200
assert response.json()["data"]["reset"] is True
assert cache.get("owner") is None
```

Add a companion no-op reset test where the breaker rows are fewer than the threshold and assert `cache.get("owner") is not None`.

- [x] **Step 2: Run the focused regression to verify RED**

Run: `uv run pytest tests/dashboard/test_briefing_cache_invalidation.py -q`

Expected: the successful-reset test fails because the entry remains cached.

- [x] **Step 3: Add minimal reset invalidation**

```python
from butlers.api.briefing.cache import BriefingCache, get_cache

async def reset_circuit_breaker(
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
) -> ApiResponse[CircuitBreakerResetResponse]:
    # Verify the breaker is tripped, then persist the reset marker.
    await pool.execute(
        "INSERT INTO public.breaker_resets (breaker, reset_by, reason) VALUES ('qa', 'dashboard', $1)",
        "Manual reset via QA dashboard",
    )
    cache.invalidate_all()
```

Place `invalidate_all()` immediately after the successful `INSERT`, never in the no-op branch.

- [x] **Step 4: Run the focused regression to verify GREEN**

Run: `uv run pytest tests/dashboard/test_briefing_cache_invalidation.py -q`

Expected: all cache-invalidation tests pass.

### Task 2: Bound Backend Briefing Attention

**Files:**

- Modify: `tests/dashboard/test_briefing.py`
- Modify: `src/butlers/api/routers/dashboard_briefing.py`

- [x] **Step 1: Write failing briefing unit tests**

```python
with patch("butlers.api.routers.notifications.notification_stats", new=AsyncMock(return_value=response)) as stats:
    failed, degraded = await _fetch_notifications_state(MagicMock())
stats.assert_awaited_once()
assert stats.await_args.kwargs["since"] is not None
assert failed == 0
assert degraded is False
```

Add tests that a 15-hour-old audit group is absent from `attention_items`, a fresh audit group remains, dispatched/novel-only QA state returns no attention item, and `active_cases_now=1` returns an active-investigation row.

- [x] **Step 2: Run the briefing target to verify RED**

Run: `uv run pytest tests/dashboard/test_briefing.py -q`

Expected: the new time-bound and active-QA assertions fail against the current all-time/dispatched behavior.

- [x] **Step 3: Implement bounded briefing composition**

```python
where_extra=("\n                  AND created_at >= NOW() - INTERVAL '12 hours'"
             "\n                  AND created_at <= NOW()")
response = await notification_stats(since=now - timedelta(hours=24), until=now, db=db)
```

Restrict the last-patrol query to the prior 24 hours, query QA-originated `public.healing_attempts` for `dispatch_pending`, `investigating`, and `pr_open`, and make `_qa_attention_item()` select breaker, recent patrol failure, then `active_cases_now` only.

- [x] **Step 4: Run the briefing target to verify GREEN**

Run: `uv run pytest tests/dashboard/test_briefing.py -q`

Expected: all briefing tests pass.

### Task 3: Make the Overview Model Current-Aware

**Files:**

- Modify: `frontend/src/components/overview/model.test.ts`
- Modify: `frontend/src/components/overview/model.ts`
- Modify: `frontend/src/pages/DashboardPage.test.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx`

- [x] **Step 1: Write failing frontend regressions**

```ts
const model = deriveOverviewTriageModel(
  { issues: [issue({ last_seen_at: "2026-05-13T21:00:00.000Z" })] },
  { now: new Date("2026-05-14T12:00:00.000Z") },
);
expect(model.attentionRows.some((row) => row.kind === "issue")).toBe(false);
expect(model.hiddenOldIssueGroups).toBe(1);
```

Add tests that an active QA case is attention, a dispatch-only QA summary is a `Now` activity with `last 24 hours` wording, and the page calls `useNotificationStats` with a closed 24-hour window whose row link preserves `status=failed`, `since`, and `until`.

- [x] **Step 2: Run the model/page targets to verify RED**

Run: `npm test -- --run src/components/overview/model.test.ts src/pages/DashboardPage.test.tsx`

Expected: the new 12-hour, active-case, and bounded-query assertions fail.

- [x] **Step 3: Implement the minimal frontend changes**

```ts
const DEFAULT_RECENT_ISSUE_HOURS = 12;
const nowMs = useTickingNow(60_000);
const notificationSince = new Date(nowMs - 24 * 60 * 60 * 1000).toISOString();
const notificationUntil = new Date(nowMs).toISOString();
const notificationStatsQuery = useNotificationStats({ since: notificationSince, until: notificationUntil });
```

Treat a missing or invalid issue timestamp as historical, pass the same `now` into QA attention/Now derivation, and separate completed QA dispatch activity from active QA attention.

- [x] **Step 4: Run the model/page targets to verify GREEN**

Run: `npm test -- --run src/components/overview/model.test.ts src/pages/DashboardPage.test.tsx`

Expected: all selected frontend tests pass.

### Task 4: Pin the Cross-Surface Contract

**Files:**

- Modify: `frontend/src/components/overview/__fixtures__/attention-contract-scenarios.json`
- Modify: `tests/dashboard/test_briefing_attention_contract.py`
- Modify: `frontend/src/components/overview/model.contract.test.ts`

- [x] **Step 1: Add failing shared scenarios**

```json
{
  "name": "quiet-historical-audit-and-completed-qa-work",
  "failed_notifications": 0,
  "historical_audit_group": { "last_seen_at": "2026-07-11T21:00:00.000Z" },
  "issues": [{ "last_seen_at": "2026-07-11T21:00:00.000Z" }],
  "qa": { "dispatched_investigations": 1, "active_cases_now": 0 },
  "expect": {
    "backend_state_class": "quiet",
    "max_attention_rows": 1,
    "hidden_old_issue_groups": 1,
    "requires_qa_activity": true
  }
}
```

The one allowed frontend row is the non-actionable historical-issues rollup;
add fresh notification and active-QA scenarios that remain medium attention.

- [x] **Step 2: Run the cross-surface targets to verify RED**

Run: `uv run pytest tests/dashboard/test_briefing_attention_contract.py -q && npm test -- --run src/components/overview/model.contract.test.ts`

Expected: historical-only fails before both implementations are current-aware.

- [x] **Step 3: Wire fixtures through both real models**

Extend each contract adapter to supply issue groups and `active_cases_now`; pass fixed `NOW` into each model so the 12-hour boundary is deterministic.

- [x] **Step 4: Run the cross-surface targets to verify GREEN**

Run: `uv run pytest tests/dashboard/test_briefing_attention_contract.py -q && npm test -- --run src/components/overview/model.contract.test.ts`

Expected: every named shared scenario passes on backend and frontend.

### Task 5: Validate and Ship the Scoped Change

**Files:**

- Modify: `openspec/changes/make-dashboard-attention-current/tasks.md`
- Verify: `openspec/changes/make-dashboard-attention-current/specs/dashboard-briefing/spec.md`
- Verify: `openspec/changes/make-dashboard-attention-current/specs/dashboard-overview/spec.md`

- [x] **Step 1: Mark each completed OpenSpec task and run focused suites**

Run: `uv run pytest tests/dashboard/test_briefing.py tests/dashboard/test_briefing_attention_contract.py tests/dashboard/test_briefing_cache_invalidation.py -q`

Run: `npm test -- --run src/components/overview/model.test.ts src/components/overview/model.contract.test.ts src/pages/DashboardPage.test.tsx`

- [x] **Step 2: Run static and contract checks**

Run: `uv run ruff check src/butlers/api/routers/dashboard_briefing.py src/butlers/api/routers/qa.py tests/dashboard`

Run: `uv run ruff format --check src/butlers/api/routers/dashboard_briefing.py src/butlers/api/routers/qa.py tests/dashboard`

Run: `npm run lint && npm run build`

Run: `openspec validate make-dashboard-attention-current --strict`

- [x] **Step 3: Commit and publish the reviewable branch**

```bash
git add openspec/changes/make-dashboard-attention-current docs/archive/superpowers/plans/2026-07-19-dashboard-attention-current.md src/butlers/api/routers/dashboard_briefing.py src/butlers/api/routers/qa.py tests/dashboard/test_briefing.py tests/dashboard/test_briefing_attention_contract.py tests/dashboard/test_briefing_cache_invalidation.py frontend/src/components/overview/model.ts frontend/src/components/overview/model.test.ts frontend/src/components/overview/model.contract.test.ts frontend/src/components/overview/__fixtures__/attention-contract-scenarios.json frontend/src/pages/DashboardPage.tsx frontend/src/pages/DashboardPage.test.tsx
git commit -m "fix: make dashboard attention current"
git push -u origin agent/bu-kzum9
gh pr create --base main --head agent/bu-kzum9 --draft --title "fix: make dashboard attention current" --body "## Summary
- bound dashboard audit, delivery, and QA attention to current state
- invalidate cached briefings after a committed QA breaker reset
- preserve historical records on their dedicated pages

## Verification
- focused dashboard backend and frontend regressions
- lint, build, and OpenSpec validation"
```
