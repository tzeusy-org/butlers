# Migration-Drift Sentinel: Codebase-Head vs. Deployed-Revision Reconciliation

## Why

bd bu-zhfd0: seven merged core-chain revisions (core_155..161) sat dark in
prod because the migrations one-shot exited 0 against a pre-core_155 image,
and nothing could tell. `public.deployments` (bu-9r3hd.2, PR #3082, already
merged) now records what git SHA and migration head each `butlers up` boot
ran with — but a ledger alone only answers "what did the last boot record,"
not "is the live database schema actually in sync with the codebase right
now." Between deploys, a schema can drift (a hand-run migration, a merge that
was never actually deployed, an interrupted upgrade) with zero signal.

This is slice 1 of epic bu-9r3hd ("Deploy spine"), move 7 of the 2026-07-10
JARVIS pursuit (`docs/redesigns/2026-07-10-jarvis-pursuit.md` §Ranked moves
#7). The deployments-ledger spec delta (`openspec/specs/system-overview-page`,
"Deployment Ledger Facts" requirement, landed with bu-9r3hd.2) explicitly
forward-references this as "a separate capability (bu-9r3hd.1)" — this change
is that capability.

Doctrine anchor: `about/heart-and-soul/vision.md` §"What Success Looks Like" —
"Butlers succeeds when it runs for weeks without intervention" — merged-but-
undeployed drift is structural at the fleet's commit velocity, and until now
nothing could even detect it, let alone act on it.

## What Changes

- **New capability: `deployment-and-drift`.** Defines the three-way
  comparison contract (codebase Alembic head vs. per-schema applied
  `alembic_version` revision vs. what the sentinel does with the result),
  the hourly cadence, the `GET /api/system/drift` response contract, and the
  QA-escalation contract for drift persisting more than 24h. No new database
  table — the comparison reads existing `alembic_version` tables directly and
  persists its own debounce state in the existing `public.audit_log` and
  escalates via the existing `public.healing_attempts` case-tracking
  machinery (`core.healing.tracking`), never a fresh schema.
- **`butlers.migrations.get_chain_head` / `get_chain_revision_ids`**: new
  read-only, DB-free helpers resolving one migration chain's codebase head
  and full revision-id set from disk (used by the sentinel; also useful for
  any future tooling that needs "what does the codebase say this chain's
  head is").
- **`butlers.jobs.deploy_drift`**: the comparison (`compute_drift_report`),
  the escalation decision (`maybe_escalate_drift`), and the hourly background
  loop (`run_migration_drift_loop`), run as an `asyncio.Task` inside the
  dashboard-api process (mirrors `butlers.jobs.secrets_lifecycle`) rather
  than a butler daemon's scheduler — the dashboard-api's `DatabaseManager`
  already reads cross-schema catalog data from one pool for the sibling
  `/api/system/database` and `/api/system/deployments` endpoints; a butler
  daemon's own pool is schema-scoped and, once per-butler Postgres role
  enforcement is active (`BUTLERS_POSTURE=hardened`), could not read other
  butlers' `alembic_version` tables at all.
- **`GET /api/system/drift`**: new endpoint, computes the comparison live on
  every request (always at least as fresh as the hourly loop) and surfaces
  the loop's escalation state (first-detected timestamp, whether a QA case
  has been opened). Follows the fleet-wide degraded-envelope convention: a
  failed comparison returns HTTP 200 with `drift_check_available: false` and
  every other field zeroed — never a fabricated all-clear, never a 503.
- **`/system` page**: new `DriftTile` (green "In sync" / red "N chains
  drifted" with the offending schema/chain/expected/actual triples) plus a
  `SystemVerdictBanner` problem line when drifted, escalation-annotated once
  a QA case has been opened.
- **QA escalation**: reuses `core.healing.tracking.create_or_join_attempt` /
  `update_attempt_status` directly — the same primitives the self-healing
  `report_error` MCP tool and QA patrol dispatch already use. A migration
  drift is an ops/infra issue, not a code bug a PR can fix, so the case is
  created and immediately transitioned to the terminal `unfixable` status
  with a human-action marker in `error_detail` (the existing convention
  `core.qa.severity.failed_with_human_action` / `state_of_case` already uses
  to classify "needs a human" cases in the QA dossier). Nothing polls
  `healing_attempts` for new `investigating` rows outside the explicit
  `dispatch_healing`/QA-dispatch call sites, so this never triggers an
  unwanted healing-agent PR attempt.

## Out of Scope (deferred to sibling epic slices)

- `butlers deploy` one-command build/migrate/verify-health pipeline
  (bu-9r3hd.3 — see `openspec/changes/deploy-command-verb`).
- Feeding drift (and other infra-health signals: connector-offline,
  backup-stale, heartbeat-stale) into the QA patrol's `DiscoverySource`
  pipeline as a first-class discovery source (bu-9r3hd.4) — this slice's
  escalation writes directly to `public.healing_attempts` without going
  through a patrol cycle, which is sufficient for QA-dossier visibility but
  not yet a full discovery-source integration.
- Backup status honesty (bu-9r3hd.5).

## Impact

- Affected specs: `deployment-and-drift` (new capability, this change).
- Affected code: `src/butlers/migrations.py`, `src/butlers/jobs/deploy_drift.py`
  (new), `src/butlers/api/routers/system.py`, `src/butlers/api/app.py`,
  `frontend/src/components/system/DriftTile.tsx` (new),
  `frontend/src/components/system/SystemVerdictBanner.tsx`,
  `frontend/src/pages/SystemPage.tsx`, `frontend/src/hooks/use-system.ts`,
  `frontend/src/api/{types,client,index}.ts`.
- No database migration in this slice — the comparison reads existing state
  and persists debounce/escalation markers in existing tables.
