# Vertical E (/system page) — Gen-1 Reconciliation Checklist

**Issue:** bu-ngfzz.8
**Date:** 2026-05-03
**Epic:** bu-ngfzz — Frontend redesign E: /system page — sovereignty visible
**Status:** Gap beads created; gen-2 reconciliation bead (bu-k1b0d) opened.

---

## Epic Acceptance Criteria vs. Implementation

| # | Epic Acceptance Criterion | Bead | Status | Notes |
|---|---|---|---|---|
| 1 | openspec/changes/system-page-capability drafted with full proposal + delta specs | bu-ngfzz.1 | CLOSED (PR #1344) | 5 domains, privacy contract enforced server-side, 19 reviewer fixes incorporated. Specs exist in changes/ dir only — sync pending (bu-27nof). |
| 2 | /api/system/* routes return version, uptime, db size, backup recency, egress facts, per-butler last-touch | bu-ngfzz.2 | CLOSED (PR #1350) | 5 endpoints shipped with 28 tests. Two ACs unmet: OTel span missing (bu-4tluf), Prometheus counters missing (bu-vup9h). |
| 3 | /system route exists and a nav entry appears under Telemetry section in nav-config.ts | bu-ngfzz.3 | CLOSED (commit 907ed4d1) | Route wired, nav entry confirmed in nav-config.ts line 84. |
| 4 | SystemPage renders via `<Page archetype='overview'>` at /system | bu-ngfzz.4 | CLOSED (PR #1357) | Archetype correct. Breadcrumbs prop missing (bu-y30mn). Tile grid sizing not differentiated (bu-ozbtv). |
| 5 | Six tile components implemented and wired to /api/system data | bu-ngfzz.5, .6, .7 | CLOSED (PRs #1360, #1362, #1364) | All 6 tiles shipped. DbSizeTile sparkline deferred to v2 (documented). |
| 6 | gen-1 reconciliation closed clean | bu-ngfzz.8 | IN PROGRESS | 6 gap beads created. Gen-2 bead (bu-k1b0d) depends on all gaps. |

---

## Per-Bead Audit

### bu-ngfzz.1 — Draft openspec change 'system-page-capability'

**Closed:** PR #1344

| AC | Status | Finding |
|---|---|---|
| 1. openspec/changes/system-page-capability/ exists with proposal.md, tasks.md, specs, and base-spec deltas | PASS | Files present: proposal.md, tasks.md, design.md, specs/system-overview-page/spec.md, specs/butler-base-spec/spec.md |
| 2. Privacy contract section defines actor enumeration + visibility rules | PASS | Server-side owner-contact assertion via public.contacts -> public.entities join; owner-only in v1 |
| 3. /opsx validate passes | UNVERIFIED | Needs re-run in current worktree; no evidence in PR or close reason |
| 4. Reviewer confirms scope | PASS | 19 reviewer fixes incorporated (close reason) |

**Gap:** opsx:sync not run — specs are in changes/ dir only. Filed: bu-27nof.

---

### bu-ngfzz.2 — Build /api/system endpoints

**Closed:** PR #1350 (commit 62b3e2f6)

| AC | Status | Finding |
|---|---|---|
| 1. 5 endpoints return 200 with documented shapes | PASS | Verified in source: /instance, /database, /backups, /egress, /butlers/heartbeat all present |
| 2. Pydantic models exist in models.py colocated | PARTIAL | Models exist but are embedded in system.py, not a separate models.py (acceptable per project conventions) |
| 3. Tests exist for each endpoint under tests/api/test_system_router.py | PASS | File is tests/api/test_system.py (28 tests per close reason) |
| 4. /opsx validate still passes | UNVERIFIED | No evidence in close reason |
| 5. Each endpoint emits structured logs with request_id | PARTIAL | Standard FastAPI middleware provides request IDs; no explicit per-endpoint request-id logging in system.py beyond `logger.warning()` calls |
| 6. /api/system/egress emits OTel span 'system.egress.read' | FAIL | No span emitted anywhere in system.py. No tracer import. No test coverage. **Filed: bu-4tluf (P1)** |
| 7. Each endpoint registers a Prometheus counter | FAIL | No prometheus_client import in system.py. No Counter defined. Other routers (google_health.py) have the correct pattern. **Filed: bu-vup9h (P2)** |

---

### bu-ngfzz.3 — Add /system route + nav-config entry

**Closed:** commit 907ed4d1

| AC | Status | Finding |
|---|---|---|
| 1. /system route resolves to a placeholder or stub | PASS | Route at router.tsx line 122; SystemPage is now the real page (stub replaced by .4) |
| 2. nav-config.ts has System entry | PASS | nav-config.ts line 84: `{ path: '/system', label: 'System', tooltip: 'Instance ownership and runtime facts' }` in Telemetry section |
| 3. Browser smoke: clicking System shows a placeholder | UNVERIFIED — see Live Verification section |

---

### bu-ngfzz.4 — Build SystemPage shell

**Closed:** PR #1357

| AC | Status | Finding |
|---|---|---|
| 1. SystemPage.tsx renders via `<Page archetype='overview'>` at /system | PASS | `archetype="overview"` confirmed in SystemPage.tsx line 83 |
| 2. Tile grid sizing differentiated (EgressCatalogTile full-width, BackupTile/ButlerHeartbeatTile 2-col, compact 1-col for others) | FAIL | Grid is uniform `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`, no col-span classes applied per-tile. **Filed: bu-ozbtv (P2)** |
| 3. use-system hooks exist for each /api/system endpoint | PASS | frontend/src/hooks/use-system.ts exists |
| 4. Visual smoke: page loads, tiles show loading skeletons, then live data | UNVERIFIED — see Live Verification section |
| breadcrumbs (from bead description) | FAIL | bu-ngfzz.4 description specifies `breadcrumbs: [{label: 'Home', path: '/'}, {label: 'System'}]`. SystemPage.tsx passes no breadcrumbs prop. Page component supports it. **Filed: bu-y30mn (P3)** |

---

### bu-ngfzz.5 — Build VersionTile, UptimeTile, DbSizeTile

**Closed:** PR #1360

| AC | Status | Finding |
|---|---|---|
| 1. Three tile components render correctly with live data | PASS | All three files present in frontend/src/components/system/ |
| 2. DbSizeTile sparkline displays last-7-days growth | DEFERRED | Explicitly documented as v1 deferral in DbSizeTile.tsx line 8-10: `growth_rate_bytes_per_day` always null in v1; no recharts sparkline. Documented deferral is acceptable. |
| 3. All three use `<Time>` from vertical C for time renders | PASS | VersionTile line 94: `<Time value={facts.started_at} mode="relative" />` |
| 4. Visual smoke on /system | UNVERIFIED |  |
| 5. Vitest render test covers happy + loading + error state | PASS | .test.tsx files present for all three components |

---

### bu-ngfzz.6 — Build BackupTile and EgressCatalogTile

**Closed:** PR #1362

| AC | Status | Finding |
|---|---|---|
| 1. BackupTile renders backup recency + 7-day status row | PASS | BackupTile.tsx present; uses `<Time value={facts.last_backup_at} mode="relative" />` |
| 2. EgressCatalogTile lists external actors with timestamps | PASS | EgressCatalogTile.tsx present |
| 3. EgressCatalogTile empty state renders local-only headline | PASS (source review) | Component present; empty state expected |
| 4. Privacy: actor list matches openspec privacy contract | PASS | Server-side actor registry in system.py matches openspec-defined actors |
| 5. Vitest render test covers happy + loading + error state | PASS | BackupTile.test.tsx, EgressCatalogTile.test.tsx present |

---

### bu-ngfzz.7 — Build ButlerHeartbeatTile

**Closed:** PR #1364

| AC | Status | Finding |
|---|---|---|
| 1. ButlerHeartbeatTile renders one row per butler with last-touch and status | PASS | ButlerHeartbeatTile.tsx present |
| 2. Stale threshold is configurable (constant in component, documented) | PASS | STALE_THRESHOLD_SECONDS = 5 * 60 (5 minutes, documented in comments at line 7-27) |
| 3. If topology moved here per d5, renders cleanly | PASS | TopologyTile is a separate tile in SystemPage; co-exists cleanly |
| 4. Vitest render test covers happy + loading + stale + offline states | PASS | ButlerHeartbeatTile.test.tsx present |

---

## Gap Beads Created

| Bead | Priority | Title | Root Cause |
|---|---|---|---|
| bu-4tluf | P1 | Add OTel span 'system.egress.read' to /api/system/egress | AC #6 of bu-ngfzz.2 unmet; privacy auditing depends on this |
| bu-vup9h | P2 | Add Prometheus counters to /api/system/* endpoints | AC #7 of bu-ngfzz.2 unmet |
| bu-ozbtv | P2 | Fix SystemPage tile grid sizing to match spec | AC #2 of bu-ngfzz.4 unmet; uniform grid, no differentiated sizing |
| bu-y30mn | P3 | Add breadcrumbs to SystemPage | bu-ngfzz.4 description requirement unmet |
| bu-27nof | P2 | Run opsx:sync for system-page-capability | Specs only in changes/ dir, not promoted to openspec/specs/ |
| bu-t102m | P3 | File backup strategy bead (Q6.3 deferral) | tasks.md Q6.3 directed filing follow-up; not filed |

Pre-existing open bead (not new):
- bu-ngfzz.9 (P2): RFC 0007 amendment for /system + /api/system namespaces — already tracked

**Gen-2 reconciliation bead:** bu-k1b0d — blocked by all 6 gap beads + bu-ngfzz.9.

---

## OTel Egress Span Verification

**Verdict: SPAN NOT PRESENT.**

Grep for `system.egress.read` in `src/butlers/api/routers/system.py` returns no results. No tracer import (`opentelemetry.trace`) is present in system.py. No test in tests/api/test_system.py references OTel exporters or span names.

This is a P1 gap — privacy auditing (as specified in bu-ngfzz.2 AC #6 and the openspec privacy contract) depends on this span being emitted on every read. Gap bead bu-4tluf filed.

---

## Live Verification

**Dev environment not reachable during this reconciliation run.** Verification performed via source-code audit only. Visual smoke tests (tile rendering, nav click, loading skeletons) are deferred to the gen-2 reconciliation bead (bu-k1b0d) where a live browser run is required.

---

## RFC 0007 Amendment Status

RFC 0007 (`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`) route table does NOT include `/system` or `/api/system/*`. The amendment bead bu-ngfzz.9 is **open** and not yet implemented. Gen-2 (bu-k1b0d) is blocked on bu-ngfzz.9 closing.

---

## opsx:sync Recommendation

Run `/opsx:sync` for the `system-page-capability` change. The delta specs (`system-overview-page` and `butler-base-spec` updates) need to be promoted from `openspec/changes/system-page-capability/specs/` into the authoritative `openspec/specs/` directory. Currently `openspec/specs/system-overview-page/` does not exist. Filed as gap bead bu-27nof.

---

## Final Verdict

**Gen-1 reconciliation is NOT clean.** Six gaps were identified and filed as child beads under the epic. The most critical gap (bu-4tluf: missing OTel egress span) is P1 and directly affects privacy auditing. The epic has a follow-up gen-2 reconciliation bead (bu-k1b0d) that tracks all gap closure.

The epic can close only after bu-k1b0d closes clean.
