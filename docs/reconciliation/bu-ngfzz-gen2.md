# Vertical E (/system page) — Gen-2 Reconciliation Report

**Issue:** bu-k1b0d
**Date:** 2026-05-06
**Epic:** bu-ngfzz — Frontend redesign E: /system page — sovereignty visible
**Based on gen-1 report:** docs/reconciliation/bu-ngfzz-gen1.md (bu-ngfzz.8)
**Gap beads resolved since gen-1:**
- bu-4tluf — Add OTel span 'system.egress.read' to /api/system/egress — CLOSED via PR #1378 (merged 2026-05-03)
- bu-vup9h — Add Prometheus counters to /api/system/* endpoints — CLOSED via PR #1396 (merged 2026-05-03)
- bu-ozbtv — Fix SystemPage tile grid sizing to match spec — CLOSED via PR #1384 (merged 2026-05-03)
- bu-y30mn — Add breadcrumbs to SystemPage — CLOSED via PR #1421 (merged 2026-05-05)
- bu-27nof — Run opsx:sync for system-page-capability — CLOSED via PR #1389 (merged 2026-05-03)
- bu-ngfzz.9 — RFC 0007 Amendment 1: register /system + /api/system namespaces — CLOSED via PR #1387 (merged 2026-05-03)

---

## Re-Audit: All Epic Acceptance Criteria

### AC1 — openspec/specs/system-overview-page/ exists and is authoritative

**Status: PASS**

`openspec/specs/system-overview-page/spec.md` exists and is fully populated (synced via
bu-27nof, PR #1389). The delta specs from `openspec/changes/system-page-capability/specs/`
have been promoted into the canonical `openspec/specs/` tree. The change directory is no
longer the only home for these specs.

---

### AC2 — /api/system/* routes return correct shapes

**Status: PASS**

All 5 endpoints are present in `src/butlers/api/routers/system.py`:
- `GET /api/system/instance` → `ApiResponse<InstanceFacts>`
- `GET /api/system/database` → `ApiResponse<DatabaseFacts>`
- `GET /api/system/backups` → `ApiResponse<BackupFacts>`
- `GET /api/system/egress` → `ApiResponse<EgressCatalog>` (owner-only, HTTP 403 on non-owner)
- `GET /api/system/butlers/heartbeat` → `ApiResponse<HeartbeatFacts>`

28 tests covering endpoint contracts exist in `tests/api/test_system.py`.

**OTel span (gen-1 gap bu-4tluf):** RESOLVED

`src/butlers/api/routers/system.py` now imports `from opentelemetry import trace` (line 36).
The `/api/system/egress` handler emits a `system.egress.read` span with `actor_count`
attribute set to the number of distinct actors returned:

```python
with trace.get_tracer("butlers").start_as_current_span("system.egress.read") as span:
    span.set_attribute("actor_count", len(egress_actors))
```

**Prometheus counters (gen-1 gap bu-vup9h):** RESOLVED

`src/butlers/api/routers/system.py` imports `from prometheus_client import Counter` (line 37)
and defines one counter per endpoint (lines 55–79):

```
system_instance_reads_total        # /api/system/instance
system_database_reads_total        # /api/system/database
system_backups_reads_total         # /api/system/backups
system_egress_reads_total          # /api/system/egress
system_butlers_heartbeat_reads_total  # /api/system/butlers/heartbeat
```

Each counter is incremented at the start of the corresponding handler.

---

### AC3 — /system route and nav entry

**Status: PASS**

- `frontend/src/router.tsx` line 135: `{ path: '/system', element: <SystemPage /> }` confirmed.
- `frontend/src/components/layout/nav-config.ts` line 84: `{ path: '/system', label: 'System', tooltip: 'Instance ownership and runtime facts' }` in the Telemetry section confirmed.

---

### AC4 — SystemPage renders via `<Page archetype='overview'>` at /system

**Status: PASS**

`frontend/src/pages/SystemPage.tsx` line 83: `<Page archetype="overview" ...>` confirmed.

**Breadcrumbs (gen-1 gap bu-y30mn):** RESOLVED

`SystemPage.tsx` lines 86–89 pass breadcrumbs:

```tsx
breadcrumbs={[
  { label: "Home", href: "/" },
  { label: "System" },
]}
```

**Tile grid sizing (gen-1 gap bu-ozbtv):** RESOLVED

The tile grid at `SystemPage.tsx` line 91 uses differentiated `col-span` classes:
- VersionTile, UptimeTile, DbSizeTile: default 1-col (compact)
- BackupTile: `lg:col-span-2` (2-col)
- EgressCatalogTile: `lg:col-span-3` (full-width)
- ButlerHeartbeatTile: `lg:col-span-2` (2-col)

This matches the spec layout contract from `openspec/specs/system-overview-page/spec.md`.

---

### AC5 — All 6 tile components implemented and wired

**Status: PASS**

All 6 tile components are present in `frontend/src/components/system/`:
- `VersionTile.tsx` + `VersionTile.test.tsx`
- `UptimeTile.tsx` + `UptimeTile.test.tsx`
- `DbSizeTile.tsx` + `DbSizeTile.test.tsx`
- `BackupTile.tsx` + `BackupTile.test.tsx`
- `EgressCatalogTile.tsx` + `EgressCatalogTile.test.tsx`
- `ButlerHeartbeatTile.tsx` + `ButlerHeartbeatTile.test.tsx`

All 6 tiles are imported and rendered in `SystemPage.tsx` (lines 9–14 imports, lines 92–103
render calls). Data hooks are wired via `frontend/src/hooks/use-system.ts` which exports one
TanStack Query hook per `/api/system/*` endpoint.

**DbSizeTile sparkline:** DEFERRED (documented)

`DbSizeTile.tsx` documents the sparkline deferral at lines 8–10. The `growth_rate_bytes_per_day`
field is always null in v1. This is an intentional, documented deferral — not a gap.

---

### AC6 — RFC 0007 Amendment 1 registers /system and /api/system/*

**Status: PASS** (gen-1 gap bu-ngfzz.9 — RESOLVED)

`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md` now includes Amendment 1
(merged via PR #1387). The amendment registers:
- `/system` in the frontend route table
- `/api/system/instance`, `/api/system/database`, `/api/system/backups`,
  `/api/system/egress`, `/api/system/butlers/heartbeat` in the API surface
- Privacy contract for `/api/system/egress` (owner-only; HTTP 403 for non-owners)
- OTel span requirement (`system.egress.read` with `actor_count` attribute)

---

### AC7 — opsx:sync promoted specs to authoritative tree

**Status: PASS** (gen-1 gap bu-27nof — RESOLVED)

`openspec/specs/system-overview-page/spec.md` exists and contains the full specification
(requirements for route, instance facts, database facts, backup facts, egress catalog,
butler heartbeats, privacy contract, tile layout, and data hooks).

---

### AC8 — gen-1 reconciliation closed clean

**Status: PASS (this report is the gen-2 reconciliation)**

All 6 gen-1 gap beads are closed. bu-t102m (backup strategy) remains open but is
operator-level infrastructure work deferred to a future milestone.

---

## Gen-2 Checklist Summary

| # | Requirement | Gen-1 Status | Gen-2 Status | PR |
|---|---|---|---|---|
| AC1 | Specs in openspec/specs/ | FAIL (bu-27nof) | PASS | #1389 |
| AC2a | 5 endpoints return correct shapes | PASS | PASS | — |
| AC2b | OTel span 'system.egress.read' | FAIL (bu-4tluf) | PASS | #1378 |
| AC2c | Prometheus counters per endpoint | FAIL (bu-vup9h) | PASS | #1396 |
| AC3 | /system route + nav entry | PASS | PASS | — |
| AC4a | Page archetype='overview' | PASS | PASS | — |
| AC4b | Breadcrumbs present | FAIL (bu-y30mn) | PASS | #1421 |
| AC4c | Differentiated tile grid sizing | FAIL (bu-ozbtv) | PASS | #1384 |
| AC5 | All 6 tiles implemented + wired | PASS | PASS | — |
| AC6 | RFC 0007 Amendment 1 registered | FAIL (bu-ngfzz.9) | PASS | #1387 |

---

## Open Items (Not Blocking Epic Closure)

### bu-t102m — Backup strategy (operator-level, deferred)

**Status: OPEN (acknowledged deferred)**

The `openspec/changes/system-page-capability/tasks.md` Q6.3 directed filing a follow-up
bead for the backup strategy. bu-t102m is open at P3. This is operator infrastructure
work (configuring an actual backup provider and wiring it to the butler stack) — it is not
a code gap in the /system page implementation. The BackupTile degrades gracefully
(`backup_source_reachable: false`, null fields) when no backup strategy is configured.

**This bead does not block epic closure.** It tracks a future operational deployment task.

---

## Live Browser Verification

**Dev environment not reachable during this reconciliation run.** Verification performed
via source-code audit and git history review. All gaps identified in gen-1 have been
confirmed resolved at the code and spec level. Live visual smoke tests remain deferred
to a future operator or QA cycle.

---

## Epic-Level Verdict

**Vertical E (bu-ngfzz) is closeable.**

All 6 epic acceptance criteria are met at the code level. All 6 gen-1 gap beads are
closed. The RFC 0007 amendment is merged. The OpenSpec is synced into the authoritative
tree. The OTel span, Prometheus counters, breadcrumbs, and tile grid sizing are all
confirmed present in the merged codebase.

The only open child bead is bu-t102m (backup strategy, P3), which is operator
infrastructure work deferred by design. It does not represent an unimplemented epic
requirement — BackupTile is fully implemented and degrades gracefully when no backup
provider is configured.

**Recommendation:** The coordinator may close bu-ngfzz and bu-k1b0d after reviewing
this report.

---

## Candidates for Closure

- **bu-ngfzz** (parent epic) — all ACs met; all code gaps closed; specs synced; RFC amended
- **bu-k1b0d** (this gen-2 bead) — reconciliation complete; verdict: closeable
