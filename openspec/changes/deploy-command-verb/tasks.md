# Tasks — deploy-command-verb (bu-9r3hd.3, epic bu-9r3hd slice 3/5)

## 1. Deploy orchestration (spec: deployment-and-drift)

- [x] 1.1 `src/butlers/core/deploy.py`: `DeployConfig`, `resolve_git_sha`,
  `build_image`, `run_migrations` (`run --rm`, never `up`), `recreate_services`
  (no `--profile`, `COMPOSE_PROFILES` stripped), `wait_for_health` (bounded
  poll), `run_deploy` (full pipeline orchestration, injectable pool for
  tests, records success/failed to `public.deployments` on every path).
- [x] 1.2 `src/butlers/cli.py`: `butlers deploy` command wiring
  (`--dir`, `--timeout`), phase-specific error message, non-zero exit on
  failure.
- [x] 1.3 `docker-compose.yml`: "PROD DEPLOYS" header comment documenting the
  profile-isolation contract inline.

## 2. Tests

- [x] 2.1 `tests/core/test_deploy.py`: every phase function in isolation
  (subprocess mocked), the no-`--profile`/stripped-`COMPOSE_PROFILES`
  structural guards, `wait_for_health` success/retry/timeout/connection-error
  paths, `run_deploy` orchestration (success, failure at each phase records a
  failed row and re-raises with the right phase, migration-head-read failure
  degrades to `null` rather than crashing, pool-ownership semantics,
  idempotent rerun after a failure).
- [x] 2.2 `tests/cli/test_cli_deploy.py`: command registration/help, success
  output, failure exit code + phase message, `--timeout` threading.
- [x] 2.3 `tests/integration/test_deploy_ledger_roundtrip.py`: real Postgres
  (testcontainers) — success and each failure phase write the expected row
  to the real `public.deployments` table via the production writer;
  `migration_head` reflects the real `alembic_version` row, not a mock.

## Deferred (sibling epic slices, not implemented here)

- bu-9r3hd.4: feed a failed/stale deploy into the QA patrol's
  `DiscoverySource` pipeline.
- bu-9r3hd.5: backup status honesty.
- Host verification of actual container recreation against a live prod
  Docker daemon — cannot run in CI; tracked as a follow-up.

## Close-out

- [ ] `openspec validate deploy-command-verb --strict`
- [ ] Archive on merge; add the new requirement to
  `openspec/specs/deployment-and-drift/` (or fold into the sibling
  `deploy-drift-sentinel` archive if that lands first).
