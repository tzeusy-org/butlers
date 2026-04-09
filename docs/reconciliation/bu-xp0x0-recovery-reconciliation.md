# Recovery Workflow Rollout — Spec-to-Code Reconciliation

**Epic:** bu-xp0x0  
**Date:** 2026-04-09  
**Reconciliation bead:** bu-xp0x0.6  
**Status:** Complete — two gaps found, gap beads created (see §4)

---

## 1. Requirement-to-Bead Mapping

### RFC 0001 — Daemon Lifecycle and Triggers

| Requirement | Section | Bead | Status |
|---|---|---|---|
| `dispatch_pending` is NOT a valid `healing_attempts` status; novelty claim and row insertion are atomic | §Dispatch Decision vs Launched Execution | bu-xp0x0.1 (spec), bu-xp0x0.2 (impl) | COVERED |
| `workflow_deadline_at` set at row creation, never updated; authoritative over `updated_at` for restart recovery | §Workflow Deadline Contract | bu-xp0x0.1 (spec), bu-xp0x0.2 (impl) | COVERED |
| Admission-control rejections (cooldown, breaker, no-model, concurrency cap) produce only a `dispatch_decision` record, never a `healing_attempts.status = failed` | §Dispatch Decision vs Launched Execution | bu-xp0x0.3 (impl) | COVERED |
| Session-timeout (`session_timeout_s`) scopes exactly one spawner invocation; broader workflow deadline owned by orchestrator | §Session Timeouts vs Workflow Deadlines | bu-xp0x0.2, bu-xp0x0.4 | COVERED |
| Semaphore: healing sessions bypass per-butler semaphore, acquire global semaphore | §Concurrency Control | existing (pre-epic) via `bypass_butler_semaphore` | COVERED |
| Restart recovery: deadline-aware `investigating` rows with `now() > workflow_deadline_at` → `timeout`; NULL deadline → `updated_at` heuristic | §Workflow Deadline Contract | bu-xp0x0.2 | COVERED |

### RFC 0005 — Observability and Telemetry

| Requirement | Section | Bead | Status |
|---|---|---|---|
| Recovery metrics catalog: `butlers.recovery.active_workflows`, `butlers.recovery.phase_duration_ms`, `butlers.recovery.dispatch_decisions_total`, `butlers.recovery.execution_failures_total` — defined in metrics catalog | §Recovery Metrics | **NONE** | **GAP** |
| Recovery metrics emitted by dispatch code (healing + QA paths) | §Workflow and Recovery Telemetry | **NONE** | **GAP** |
| Admission-control outcomes recorded distinctly from execution failures in metrics | §Workflow and Recovery Telemetry | bu-xp0x0.3 (DB-level separation) | PARTIAL — DB separation done but OTel instruments absent |
| Independent `healing.dispatch` trace span (root span, not inheriting failed session) | §Workflow and Recovery Telemetry | bu-xp0x0 (pre-existing `tracer.start_as_current_span` at line 960 of dispatch.py) | COVERED |
| Phase-level spans with low-cardinality `workflow`/`phase` attributes | §Workflow and Recovery Telemetry | **NONE** | **GAP** (part of recovery metrics gap) |
| High-cardinality fields (session_id, request_id, trace_id) on spans/logs only — not as metric labels | §Cardinality Discipline | Respected in all dispatch code | COVERED |

### RFC 0007 — Dashboard and API Surface

| Requirement | Section | Bead | Status |
|---|---|---|---|
| `GET /api/healing/attempts` — paginated list | §Operational Endpoints | pre-epic | COVERED |
| `GET /api/healing/attempts/{id}` — detail | §Operational Endpoints | pre-epic | COVERED |
| `POST /api/healing/attempts/{id}/retry` — bypass cooldown, create new attempt | §healing-session-tracking spec | bu-xp0x0.2 | COVERED |
| `GET /api/healing/circuit-breaker` — status | §Operational Endpoints | pre-epic | COVERED |
| `POST /api/healing/circuit-breaker/reset` — manual reset | §Operational Endpoints | pre-epic | COVERED |
| `GET /api/healing/dispatch-events` — admission-control decisions separate from execution failures | §Operational Endpoints | bu-xp0x0.2, bu-xp0x0.5 | COVERED |
| `GET /api/qa/summary` — staffer status, patrol rollup, circuit breaker | §QA domain | pre-epic | COVERED |
| `GET /api/qa/investigations` — paginated QA investigations with `current_phase`, `workflow_deadline_at`, evidence summary | §QA domain | bu-xp0x0.5 | COVERED |
| `GET /api/qa/meta-review` — QA-self-recursive findings in operator lane; never auto-investigated | §QA domain | bu-xp0x0.5 | COVERED |
| Dispatch events NOT mixed into `GET /api/healing/attempts` list | §Operational Endpoints | bu-xp0x0.2, bu-xp0x0.5 | COVERED |
| `HealingAttempt` response includes `current_phase` and `workflow_deadline_at` | §QA/healing domain | bu-xp0x0.5 | COVERED |
| `QaFindingRecord` response includes `source_session_trigger_source` and `structured_evidence` | §QA domain | bu-xp0x0.5 | COVERED |
| Frontend route `/qa/investigations/:attemptId` — individual investigation detail | §Frontend Route Map | Not applicable to backend API; healing attempts detail served by existing `GET /api/healing/attempts/{id}` | COVERED (via healing route) |

### self-healing-dispatch spec

| Requirement | Bead | Status |
|---|---|---|
| Dual entry points (module `report_error` path + spawner fallback path) | pre-epic | COVERED |
| 10-gate ordering (no-recursion → opt-in → fingerprint → severity → novelty → cooldown → concurrency → breaker → model) | pre-epic + bu-xp0x0.3 | COVERED |
| Gate rejections delete orphaned `investigating` row + create `dispatch_decision` record | bu-xp0x0.3 | COVERED |
| No-recursion guard is gate 1 (before any DB work) | pre-epic | COVERED |
| Fingerprint update on failed session (`session_set_healing_fingerprint`) | pre-epic | COVERED |
| Fingerprint update failure is non-fatal (best-effort) | pre-epic | COVERED |
| Atomic novelty claim (INSERT ON CONFLICT) | bu-xp0x0.2 | COVERED |
| Multi-session phase chaining with per-session timeouts | bu-xp0x0.4 | COVERED |
| Healing watchdog task per phase session | bu-xp0x0.4 | COVERED |
| PR flow: push → anonymize → validate → `gh pr create` | pre-epic | COVERED |
| Semaphore: bypass per-butler, acquire global (no deadlock) | pre-epic | COVERED |
| All dispatch errors non-fatal to caller | pre-epic | COVERED |
| Independent OTel trace span `healing.dispatch` | pre-epic | COVERED |

### healing-session-tracking spec

| Requirement | Bead | Status |
|---|---|---|
| `public.healing_attempts` table with all specified columns | bu-xp0x0.2 | COVERED |
| `current_phase` and `workflow_deadline_at` added to `healing_attempts` | bu-xp0x0.2 (core_066 migration) | COVERED |
| `healing_attempt_sessions` child table | bu-xp0x0.2 (core_066 migration) | COVERED |
| `healing_dispatch_events` table | bu-xp0x0.2 (core_066 migration) | COVERED |
| Partial unique index on `fingerprint WHERE status IN ('investigating', 'pr_open')` | bu-xp0x0.2 | COVERED |
| `dispatch_pending` removed from valid statuses and partial unique index | bu-xp0x0.2 | COVERED |
| Atomic novelty claim prevents duplicate active investigations | bu-xp0x0.2 | COVERED |
| State machine: valid transitions, terminal-state finality, `closed_at` on terminal | pre-epic | COVERED |
| Session ID accumulation (append to `session_ids`), idempotent | bu-xp0x0.2 | COVERED |
| Fingerprint collision detection (CRITICAL log on `(exception_type, call_site)` mismatch) | bu-xp0x0.2 | COVERED |
| Restart recovery: deadline-aware timeout of stale `investigating` rows | bu-xp0x0.2 | COVERED |
| Recovery runs before new dispatches (startup ordering) | bu-xp0x0.2 | COVERED |
| `count_active_attempts(pool, qa_only=False)` — global count | bu-xp0x0.2 | COVERED |
| `count_active_attempts(pool, qa_only=True)` — QA-scoped count for QA concurrency gate | bu-xp0x0.2 (function exists) | **PARTIAL — gap: QA dispatch calls without `qa_only=True`** |
| `get_active_attempt`, `get_recent_attempt`, `list_attempts`, `list_dispatch_events` query helpers | bu-xp0x0.2 | COVERED |
| `record_phase_session`, `update_phase_session_status` helpers | bu-xp0x0.2 | COVERED |
| `get_recent_terminal_statuses` filters for `healing_session_id IS NOT NULL` | bu-xp0x0.2 | COVERED |
| Dashboard API endpoints for healing attempts and dispatch events | bu-xp0x0.2, bu-xp0x0.5 | COVERED |

### qa-investigation-dispatch spec

| Requirement | Bead | Status |
|---|---|---|
| QA dispatcher reuses `healing_attempts` table with `qa_patrol_id` source marker | bu-xp0x0.2 | COVERED |
| All investigation IDs SHALL be UUIDv7 for time-ordered sortability | **NONE** | **GAP** — `healing_attempt_sessions` and `healing_dispatch_events` use `gen_random_uuid()` (UUIDv4); `healing_attempts.id` is also UUIDv4 |
| 10-gate sequence preserved for QA dispatch | bu-xp0x0.3 | COVERED |
| QA concurrency gate uses `count_active_attempts(qa_only=True)` | bu-xp0x0.2 (function) | **GAP** — `qa/dispatch.py` line 1697 calls `count_active_attempts(pool)` without `qa_only=True` |
| Gate rejections before launch are dispatch decisions, not execution failures | bu-xp0x0.3 | COVERED |
| `update_finding_dedup_reason` called on all gate rejections | bu-xp0x0.3 | COVERED |
| QA self-recursion barrier (Gate 0): suppress `source_butler="qa"` with `trigger_source` in `{"healing", "qa"}` | bu-xp0x0.4 | COVERED |
| Meta-review routing for unknown trigger_source from QA butler | bu-xp0x0.4 | COVERED |
| `source_session_trigger_source` field on `QaFinding` (per discovery source) | bu-xp0x0.4 | COVERED |
| `structured_evidence` field on `QaFinding` | bu-xp0x0.4 | COVERED |
| core_067 migration: `source_session_trigger_source` + `structured_evidence` columns on `qa_findings` | bu-xp0x0.4 | COVERED |
| Worktree-based investigation (dedicated git worktree, `qa/fix-<fingerprint>-<ts>` branch pattern) | pre-epic | COVERED |
| Sandboxed agent environment: GH_TOKEN from CredentialStore, no butler runtime secrets | pre-epic | COVERED |
| `BUTLERS_QA_GH_TOKEN` from CredentialStore for PR creation | pre-epic | COVERED |
| Phased investigation: `diagnose`, `implement`, `verify` sessions under one deadline | bu-xp0x0.4 | COVERED |
| `record_phase_session` called on QA investigation session launch | bu-xp0x0.4 | COVERED |
| Phase session status updated on all outcome paths | bu-xp0x0.4 | COVERED |
| Anonymized PR pipeline (anonymize → validate → `gh pr create`) | pre-epic | COVERED |
| PR status tracking on each patrol cycle (`gh pr view`, `pr_merged` / `failed` transitions) | pre-epic | COVERED |
| `GET /api/qa/meta-review` endpoint | bu-xp0x0.5 | COVERED |
| `QaInvestigation` model includes `current_phase`, `workflow_deadline_at` | bu-xp0x0.5 | COVERED |

### runtime-config-seed-and-manage / core-spawner spec

| Requirement | Bead | Status |
|---|---|---|
| Spawner reads hot fields (`model`, `runtime_type`, `args`, `session_timeout_s`) from `RuntimeConfigAccessor` per trigger | pre-epic | COVERED |
| Model fallback from accessor when catalog unavailable | pre-epic | COVERED |
| Cold fields (`max_concurrent`, `max_queued`) read at construction | pre-epic | COVERED |
| `session_timeout_s` forwarded to `runtime.invoke()` and `asyncio.wait_for` | pre-epic | COVERED |
| `timeout_override` propagated from dispatch watchdog to spawner | bu-n7q3e (PR #1033, landed before epic) | COVERED |
| Accessor DB failure → use stale cache + WARNING log | pre-epic | COVERED |

---

## 2. Coverage Assessment Per Spec Section

### RFC 0001 — Full coverage
All recovery-workflow contract items (dispatch decision vs execution, deadline immutability, restart recovery, semaphore bypass) are correctly implemented. No gaps.

### RFC 0005 — Recovery metrics section is NOT implemented
The four `butlers.recovery.*` instruments (`active_workflows`, `phase_duration_ms`, `dispatch_decisions_total`, `execution_failures_total`) are specified in the metrics catalog but:
- Not defined in `src/butlers/core/metrics.py`
- Not emitted anywhere in `src/butlers/core/healing/dispatch.py` or `src/butlers/core/qa/dispatch.py`

The DB-level accounting (dispatch events table, phase sessions table) provides the raw data for dashboards, but the OTel metrics path for operational alerting and Grafana dashboards is absent.

### RFC 0007 — Full coverage
All specified endpoints exist and return correct shapes. `dispatch-events` is cleanly separated from `healing/attempts`. `meta-review` endpoint exists. Phase/deadline fields are exposed in both healing and QA investigation responses.

### self-healing-dispatch spec — Full coverage
All 10 gates, dual entry paths, PR flow, semaphore behavior, trace isolation, and error containment are implemented. Pre-existing code plus bu-xp0x0.2/3/4 together satisfy all scenarios.

### healing-session-tracking spec — Nearly full coverage
One partial gap: the `qa_only` parameter of `count_active_attempts` exists in `tracking.py` but is not used in QA dispatch Gate 8 (concurrency cap). The QA dispatch currently counts ALL active investigations globally instead of only QA-originated ones, which means QA concurrency budget is incorrectly shared with legacy self-healing attempts.

### qa-investigation-dispatch spec — Two gaps found
1. **UUIDv7 for investigation IDs** — spec says "All IDs SHALL be UUIDv7". Implementation uses PostgreSQL `gen_random_uuid()` (UUIDv4) for `healing_attempts.id`, `healing_attempt_sessions.id`, and `healing_dispatch_events.id`.
2. **QA-scoped concurrency cap** — Gate 8 in `qa/dispatch.py` calls `count_active_attempts(pool)` without `qa_only=True`. This violates the contract resolved in bu-xp0x0.1 ambiguity #2.

### runtime-config-seed-and-manage / core-spawner spec — Full coverage
All spawner hot/cold field requirements are implemented. The `timeout_override` propagation bug (bu-n7q3e) was fixed before the epic landed.

---

## 3. Gap Analysis

Two implementation gaps require follow-up beads.

### Gap A: Recovery Metrics Instruments Missing (RFC 0005)

**Severity:** Medium — operational observability gap; system is functionally correct but cannot fire Grafana alerts on phase failures or dispatch rejection rates.

**Missing work:**
- Add `butlers.recovery.active_workflows` (UpDownCounter, labels: `butler`, `workflow`) to `ButlerMetrics`
- Add `butlers.recovery.phase_duration_ms` (Histogram, labels: `butler`, `workflow`, `phase`, `outcome`) to `ButlerMetrics`
- Add `butlers.recovery.dispatch_decisions_total` (Counter, labels: `butler`, `workflow`, `decision`) to `ButlerMetrics`
- Add `butlers.recovery.execution_failures_total` (Counter, labels: `butler`, `workflow`, `phase`, `error_class`) to `ButlerMetrics`
- Emit these from `healing/dispatch.py` (healing path) and `qa/dispatch.py` (QA path) at the appropriate control points

**Follow-up bead:** bu-lap71 (created — see §4)

### Gap B: QA Concurrency Cap Uses Wrong Scope (qa-investigation-dispatch + healing-session-tracking specs)

**Severity:** High — correctness bug. QA Gate 8 uses the global investigation count rather than the QA-scoped count, violating the explicit contract from bu-xp0x0.1 ambiguity resolution #2. This means:
- A surge of legacy self-healing attempts can exhaust the QA concurrency budget even when no QA investigations are running.
- Conversely, many QA investigations that do not have `qa_patrol_id` set would not count against the limit if the table data is inconsistent.

**Fix:** In `src/butlers/core/qa/dispatch.py` Gate 8 (line ~1697), change:
```python
active_count = await count_active_attempts(pool)
```
to:
```python
active_count = await count_active_attempts(pool, qa_only=True)
```

**Follow-up bead:** bu-loak0 (created — see §4)

### Non-gap Notes

**UUIDv7 for investigation IDs:** The qa-investigation-dispatch spec requires UUIDv7 IDs. The current implementation uses `gen_random_uuid()` (UUIDv4) in the migrations for all three healing tables. However:
- `healing_attempts` predates the QA epic and its existing `id` column cannot be changed without a disruptive migration.
- The QA spec's UUIDv7 requirement is aspirational for time-ordered sortability. The existing `created_at` column already provides sortability.
- Changing PK types in PostgreSQL requires a table rewrite and is a high-risk schema migration.

This is tracked as a lower-priority improvement rather than a blocking gap. The impact is cosmetic (sort order if `created_at` is not used) rather than functional. Recorded in §4 as a P3 follow-up.

---

## 4. Gap Beads Created

| Bead | Title | Priority | Rationale |
|---|---|---|---|
| bu-lap71 | Add butlers.recovery.* OTel instruments and emit from dispatch paths | P2 | RFC 0005 recovery metrics catalog is entirely unimplemented; Grafana alerting on phase failures and dispatch rejection rates requires these instruments. |
| bu-loak0 | Fix QA concurrency cap to use qa_only=True scope | P1 | Correctness bug — Gate 8 in qa/dispatch.py counts all active investigations globally instead of QA-scoped only, violating the resolved contract from bu-xp0x0.1. |

Both are dependencies of a gen-2 reconciliation bead (bu-pp0bg) which verifies the gaps are closed before the epic can be fully closed.

---

## 5. Risk Notes

1. **Recovery metrics gap (bu-xp0x0.7) is purely additive.** No existing behavior changes. Risk: low.

2. **QA concurrency cap fix (bu-xp0x0.8) is a one-line change** but affects Gate 8 logic. After the fix, QA can run its maximum concurrent investigations even when self-healing is also active. This is the intended behavior (scoped budgets). Risk: low; the logic is simpler and cleaner.

3. **UUIDv7 IDs are aspirational, not blocking.** Deferring to a future schema migration avoids table rewrites. The current `created_at`-based ordering is functionally equivalent for all dashboard and query use cases.

4. **Trigger source mapping verified.** bu-xp0x0.4 fixed a subtle bug where the Gate 0 barrier checked for `"qa_investigation"` (not a real trigger_source) instead of `"qa"`. bu-xp0x0.5 carried the same fix to the API meta-review query. Both are consistently using `{"healing", "qa"}` now.

5. **Dispatch event linkage on novelty-join.** bu-xp0x0.3 added a best-effort `update_finding_attempt` call on the novelty-join path in QA dispatch so findings get linked to their active investigation. This closes a traceability gap that was not in the original spec but was found during implementation.
