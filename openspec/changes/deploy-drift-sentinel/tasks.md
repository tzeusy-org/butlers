# Tasks — deploy-drift-sentinel (bu-9r3hd.1, epic bu-9r3hd slice 1/5)

## 1. Codebase-head resolution (spec: deployment-and-drift)

- [x] 1.1 `butlers.migrations.get_chain_head(chain)` / `get_chain_revision_ids(chain)`:
  scoped `ScriptDirectory` reads (no DB connection) resolving one migration
  chain's codebase head and full revision-id set from disk.

## 2. Drift comparison + escalation (spec: deployment-and-drift)

- [x] 2.1 `src/butlers/jobs/deploy_drift.py`: `compute_drift_report()` --
  per-schema chain resolution mirroring `cli._migrate_all`, reads each
  schema's `alembic_version` rows, compares against codebase heads. Degrades
  (never crashes, never fabricates "no drift") on pool/query failure;
  treats a missing `alembic_version` table as a legitimate empty state.
- [x] 2.2 `drift_fingerprint()`: stable SHA-256 fingerprint of a drift
  composition, order-independent.
- [x] 2.3 `get_drift_escalation_state()` / `maybe_escalate_drift()`:
  first-detected/escalated debounce persisted in `public.audit_log` (no new
  migration); escalation past the 24h threshold opens a
  `public.healing_attempts` case via the existing
  `create_or_join_attempt`/`update_attempt_status` primitives, immediately
  closed to `unfixable` with a human-action marker.
- [x] 2.4 `run_migration_drift_check()` / `run_migration_drift_loop()`:
  hourly background loop, wired into `butlers.api.app.lifespan` (mirrors
  `run_secrets_lifecycle_loop`), cancelled cleanly on shutdown.

## 3. API surface (spec: deployment-and-drift)

- [x] 3.1 `GET /api/system/drift`: computes the comparison live per request;
  reads (never writes) the escalation debounce state for display. Always
  HTTP 200; `drift_check_available: false` on a degraded check.

## 4. Frontend (spec: deployment-and-drift)

- [x] 4.1 `DriftTile`: green "In sync" / red "N chains drifted" with
  schema/chain/expected/actual detail, first-detected time, "escalated to
  QA" marker.
- [x] 4.2 `SystemVerdictBanner`: drift problem line (annotated once
  escalated), drift-check-unavailable problem line, drift added to the
  page's stillLoading gate.
- [x] 4.3 Wired into `SystemPage`; `use-system.ts` hook; `api/{types,client,index}.ts`.

## 5. Tests

- [x] 5.1 `tests/config/test_migration_chain_head.py`: real-repo `core`
  chain head resolution; every recognized chain has exactly one head;
  unknown-chain and multiple-unmerged-heads error paths (synthetic temp
  chain).
- [x] 5.2 `tests/jobs/test_deploy_drift.py`: chain resolution, all-aligned,
  dark-revision detection, never-applied-chain, degraded-check paths,
  fingerprint stability, escalation state machine (first sighting / within
  threshold / past threshold escalates once / no re-escalation / escalation
  failure degrades).
- [x] 5.3 `tests/integration/test_deploy_drift_roundtrip.py`: real Postgres
  (testcontainers) -- a freshly migrated schema is not drifted; a schema
  manually rolled back to the head's parent revision is detected as drifted
  with the correct expected/actual pair.
- [x] 5.4 `tests/api/test_system.py`: `/api/system/drift` happy path,
  drifted + escalation-state display, degraded-check-never-503.
- [x] 5.5 Frontend: `DriftTile.test.tsx` (loading/error/unavailable/clean/
  drifted/escalated); `SystemPage.test.tsx` + `ButlerDetailPage.test.tsx`
  `use-system` mocks updated for the new hook.

## 6. Close-out

- [ ] 6.1 `openspec validate deploy-drift-sentinel --strict`
- [ ] 6.2 Archive on merge; add `deployment-and-drift` to `openspec/specs/`.

## Deferred (sibling epic slices, not implemented here)

- bu-9r3hd.3: `butlers deploy` one-command build/migrate/verify-health
  pipeline.
- bu-9r3hd.4: feed drift (and other infra-health signals) into the QA
  patrol's `DiscoverySource` pipeline as a first-class discovery source --
  this slice escalates directly to `public.healing_attempts` without a
  patrol cycle.
- bu-9r3hd.5: backup status honesty.
