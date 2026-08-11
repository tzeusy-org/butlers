## 1. Isolated executor privilege and operator boundary

- [ ] 1.1 Add a checked-in one-shot privileged bootstrap provisioner plus
  idempotent `scripts/init-db.sql` support for a distinct restore-drill executor
  login: `LOGIN CREATEDB NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION`, no
  runtime-role membership, and no broad live-schema grants. Its password must
  be read from a file-backed deployment secret rather than a tracked file,
  `DATABASE_URL`, or shared `POSTGRES_*` value; the migration/connecting user,
  every `butler_*_rw` role, and `connector_writer` remain `NOCREATEDB`.
  Boundary implementation evidence for `bu-kqnum.8.3` is present in
  `scripts/init-db.sql` and `scripts/provision_restore_drill_executor.sh` with
  bootstrap regressions; final exact-SHA verification remains pending.
- [ ] 1.2 Add the migration-owned, fixed-`search_path` security-definer
  interface through which that executor alone reads due state and records a
  restore-drill result plus fixed audit-projection provenance. Its owner-private result ledger
  is the sole due/API authority; the fixed public audit projection is
  unauthoritative, and the shared dashboard login receives only the fixed
  read function rather than direct ledger access. Grant no direct
  general-table or schema-creation access to the executor; make
  `createdb`/`dropdb` name their maintenance database explicitly.
  Boundary implementation evidence for `bu-kqnum.8.3` is
  `alembic/versions/core/core_196_restore_drill_executor_boundary.py`; result-aware
  retries and attention semantics remain allocated to `bu-kqnum.8.4`, and
  final exact-SHA verification remains pending.
- [ ] 1.3 Add a dedicated deterministic restore-drill executor/service and move
  scheduling plus CLI subprocess launch out of dashboard-api. Its compose
  definition keeps the credentialed executor on a dedicated Docker `internal`
  relay network, with an uncredentialed relay as its only policy-permitted
  peer. Docker `internal` limits membership but is not by itself a bridge-gateway/host
  denial: the root-owned project-scoped host policy must default-deny the
  executor bridge except its created relay peer at the configured port, and
  default-deny relay-bridge forwarded and bridge-to-host traffic except the
  configured PostgreSQL endpoint and port; supported
  `scripts/compose.sh` and `butlers deploy` paths must treat stop/down failure
  as terminal before they create the executor,
  invoke only a fixed root-owned firewall wrapper with validated literal
  arguments and a versioned pre-create capability preparation, discover the
  exact created executor/relay topology, install both policies, then bind its
  per-created-generation nonce in the executor marker to the observable
  executor generation/IP/gateway and relay-alias IP; reject
  stale installed wrappers and stale same-boot down/recreate markers before
  secret use, then start the protected services;
  disable executor
  auto-restart while retaining an untrimmed DNS `verify-full` TLS identity (not
  `localhost` or any numeric IPv4 spelling) separately from the resolved IPv4
  firewall endpoint. Launcher, deploy, and executor boundaries must require
  that DNS identity; only firewall/proxy relay-target boundaries require a
  canonical dotted-decimal remote-unicast IPv4 and canonical ASCII-decimal port
  matching `[1-9][0-9]{0,4}` in `1..65535`;
  noncanonical, loopback, unspecified, link-local,
  multicast, documentation, and policy-reserved IPv4 routes are denied,
  while RFC1918, CGNAT/tailnet, and valid public unicast remain supported.
  Legacy decimal, octal, hexadecimal, and abbreviated `inet_aton` spellings
  are rejected before DNS resolution. The pre-source endpoint-literal grammar
  supports simple `KEY=value` or `export KEY=value` with optional leading
  spaces/tabs; raw RHS whitespace is rejected before sourcing. Other Bash
  command forms are outside this pre-source endpoint-literal grammar; their
  resulting endpoint values are validated without trimming or reinterpretation.
  The
  relay has a small fixed no-queue admission cap, bounded listener backlog, and
  finite connect, idle, and total-session deadlines with cancellation-safe
  cleanup, including a one-second bounded graceful close before forced transport
  abort. Mount a dedicated noncredential CA root read-only for `verify-ca`/`verify-full`
  (fail closed when it is missing or invalid, without requiring it for
  `require`); add a
  read-only backup mount, no
  listener, Docker socket, `backend`, `frontend`, or `egress` membership, and a
  private secret-file mount. It MUST NOT inherit `x-postgres-env`, receive
  `POSTGRES_USER`/`POSTGRES_PASSWORD`/`DATABASE_URL`, or expose the executor
  credential to dashboard-api; dashboard-api only reads recorded results.
  Boundary implementation evidence for `bu-kqnum.8.3` is the db-only executor,
  protected Compose fragment, relay, and supported-launcher/deploy paths;
  final exact-SHA verification remains pending.
- [ ] 1.4 Add configuration, bootstrap, compose, and role-boundary tests that
  prove the executor role is isolated, dashboard/butler/connector credentials
  remain `NOCREATEDB`, a dashboard-style shared credential cannot create the
  scratch database, an effective full-core-chain ACL matrix denies both narrow
  functions to shared/butler/connector/PUBLIC subjects, and no rendered dashboard service receives the private
  executor secret. Cover direct base and direct merged rendering, proving the
  executor has only the internal relay network and no direct route/credentials;
  prove the root-owned policy separately default-denies executor bridge
  forwarding and host/gateway paths except its created relay peer;
  cover canonical endpoint parity at every supported boundary, nondefault-port
  forwarding, overload rejection before an upstream dial, and connect/idle/
  session/shutdown cleanup. Cite `REQ-database-security-006` in the test.
  `tests/config/test_restore_drill_executor_acl_integration.py` is valid
  Testcontainers ACL/core-chain boundary evidence only: it does not create a
  scratch database or invoke restore clients. That real lifecycle proof remains
  allocated to `bu-kqnum.8.6`. Boundary implementation evidence also includes
  bootstrap, Compose, endpoint-policy, and relay regressions; final exact-SHA
  verification remains pending.
- [ ] 1.5 Update `docs/operations/backup-restore.md` to name the managed
  executor bootstrap prerequisite, file-secret boundary, fixed scratch
  lifecycle, single-executor assumption, no-live-database-mutation rule, and
  rollback boundary; document the two-network relay, endpoint validation and
  bounded relay posture, and the read-only merged-file inspection helper; remove
  the ad hoc `ALTER ROLE` and manually pre-created scratch-database workaround.
  Boundary implementation evidence for `bu-kqnum.8.3` is in the operations and
  deployment contract updates; it does not authorize a live drill or operator
  role mutation, and final exact-SHA verification remains pending.
- [ ] 1.6 Run focused bootstrap/compose/docs checks and review rendered command
  examples to confirm they neither disclose a credential nor direct a live role
  mutation.
  Boundary coverage is retained for this slice, but final exact-SHA verification
  remains pending.

## 2. Truthful restore result, retry, and attention provenance (allocated to `bu-kqnum.8.4`)

- [ ] 2.1 Refactor `src/butlers/jobs/backup_health.py` and the isolated executor
  entry point so `RestoreDrillResult` carries stable failure stage/code plus
  bounded sanitized detail, a pass is contingent on successful post-cleanup,
  and the shared dashboard credential is never passed to `createdb`/`psql`.
  Classify a `createdb` permission denial as
  `createdb_permission_denied` at stage `create` without retaining raw
  subprocess output.
- [ ] 2.2 Move `run_restore_drill_check()` scheduling and CLI execution out of
  dashboard-api, while keeping its result reader available to the API. The
  executor's constrained persistence interface must schedule no result
  immediately, pass at seven days, and failure at 24 hours; compute the
  contiguous failure epoch from persisted result order and never parse detail
  text for policy.
- [ ] 2.3 Extend the core chain after the completed `core_196` executor-boundary
  migration with the then-current next `core_<n>` revision to admit
  `source="restore_drill"` in `public.attention_ledger`. Preserve existing rows
  and, on downgrade, remove only that source before restoring the narrower
  constraint. This later migration must not weaken or rename the `core_196`
  boundary interface.
- [ ] 2.4 Extend `src/butlers/core/attention_ledger.py` and the failed-drill
  path to record a best-effort `restore_drill`/`failed` event after the audit
  result is durable, with null notification provenance and only sanitized
  structured metadata; a ledger failure must not alter audit persistence or
  retry scheduling.
- [ ] 2.5 Expand `tests/jobs/test_backup_health.py` for every classified
  failure stage/code, no-result/pass/fail/recovery cadence, failure-epoch reset,
  cleanup failure, executor credential isolation, and audit-write versus
  ledger-write failure independence. Cite `REQ-system-overview-page-006` in the
  new coverage.
- [ ] 2.6 Add the later result/attention migration and reader regressions for
  the new source constraint, `restore_drill` filter, null notification
  reference, and separate failed outcome. Keep them distinct from
  `tests/config/test_restore_drill_executor_acl_integration.py`, which proves
  the already-completed `core_196` privilege boundary. Cite
  `REQ-core-notify-026` and `REQ-core-notify-008`.

## 3. System API and dashboard truthfulness (allocated to `bu-kqnum.8.5`)

- [ ] 3.1 Extend `RestoreDrillFacts`, `_read_restore_drill_facts()`, and
  `GET /api/system/backups` in `src/butlers/api/routers/system.py` with
  additive `failure_code`, `failure_stage`, and `failing_since` fields; return
  them only from durable drill records and return null provenance/age for pass,
  pending, and degraded states.
- [ ] 3.2 Update `frontend/src/api/types.ts` and
  `frontend/src/components/system/BackupTile.tsx` so a current failed drill
  renders an accessible `Failed since` age alongside its verdict and secondary
  code/detail, while pass, pending, and degraded states render no stale age.
- [ ] 3.3 Update `frontend/src/components/system/SystemVerdictBanner.tsx` so its
  restore-drill problem reflects the same current failure/recovery contract and
  never suggests an owner notification or a historical failure is current.
- [ ] 3.4 Extend `tests/api/test_system.py`,
  `frontend/src/components/system/BackupTile.test.tsx`, and
  `frontend/src/pages/SystemPage.test.tsx` for failed-only failure age,
  reset-after-pass, pending/degraded null behavior, and semantic failed text.
  Cite `REQ-system-overview-page-005` in the backend regression tests.

## 4. Environment-proof PostgreSQL integration evidence (allocated to `bu-kqnum.8.6`)

The existing `tests/config/test_restore_drill_executor_acl_integration.py` is
Testcontainers ACL/core-chain boundary evidence only. It deliberately does not
create a scratch database or invoke restore clients; the real-client lifecycle
proof remains open and allocated to `bu-kqnum.8.6`.

- [ ] 4.1 Add `tests/integration/test_restore_drill_postgres.py`, marked
  `integration`, using the shared `postgres_container`/migration helpers and
  real PostgreSQL client binaries. Seed data, create a real compressed
  plain-SQL dump, invoke the production isolated-executor command path, and
  assert restored non-system data plus final absence of the scratch database.
  This real-client proof is intentionally not implemented in `bu-kqnum.8.3`.
- [ ] 4.2 In the same integration module, create a disposable dashboard-style
  `NOCREATEDB` login and the isolated executor login. Prove the former reports
  stage `create` and code `createdb_permission_denied`, the latter can complete
  the bounded scratch lifecycle, and neither case leaks a scratch database.
  Exercise a controlled cleanup-error path and assert it never reports a pass.
  The fixture teardown must remove any scratch state intentionally left by that
  controlled cleanup-error case.
- [ ] 4.3 Give the integration module an explicit Docker/client-binary
  prerequisite guard that skips only when those real prerequisites are absent;
  do not substitute subprocess mocks, print dump content, or log credentials.
  Cite `REQ-testing-031` in the module.

## 5. Verification and operational handoff

- [ ] 5.1 Run the `bu-kqnum.8.3` boundary selection after its latest correction
  is committed and verified on the exact final SHA:
  `uv run pytest -n 0 tests/config/test_init_db_bootstrap.py tests/config/test_init_db_restore_drill_role_boundary.py tests/config/test_restore_drill_executor_acl_integration.py tests/config/test_schema_acl_isolation.py tests/config/test_restore_drill_executor_compose.py tests/core/test_deploy.py tests/jobs/test_backup_health.py tests/jobs/test_restore_drill_executor.py tests/scripts/test_restore_drill_tcp_proxy.py -q`.
- [ ] 5.2 Run the `bu-kqnum.8.6` real integration proof:
  `uv run pytest tests/integration/test_restore_drill_postgres.py -m integration -q`;
  record an explicit prerequisite skip if Docker or a PostgreSQL client is
  intentionally unavailable.
- [ ] 5.3 Run focused frontend coverage and production checks:
  `cd frontend && npx vitest run --configLoader runner src/components/system/BackupTile.test.tsx src/pages/SystemPage.test.tsx`, then
  `npm run lint` and `npm run build`.
- [ ] 5.4 (allocated to `bu-kqnum.8.7` final handoff) Run `uv run ruff check src/ tests/ roster/ conftest.py`,
  `uv run ruff format --check src/ tests/ roster/ conftest.py`, and the
  repository's final `make test-qg` gate after the targeted failures are fixed.
- [ ] 5.5 (allocated to `bu-kqnum.8.7` exact-scope reconciliation) Run `openspec validate restore-drill-recovery-truthfulness --strict`
  and the repository-portable `git diff --check origin/main...HEAD`; verify the
  implementation does not execute a live drill, alter a live role, deploy/restart
  services, or manually repair data as part of the change.
