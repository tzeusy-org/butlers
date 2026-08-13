## 1. Database contract and role boundary

- [ ] 1.1 Add failing real-PostgreSQL migration tests in `tests/migrations/test_codex_auth_generation_provenance_migration.py` for a fresh database, an upgraded database with a valid legacy shared row, malformed legacy content, a missing/mismatched binding, direct reserved-row DML, and value-free provenance columns.
- [ ] 1.2 Add one additive core migration after the live core head that creates the singleton authority state, opaque generation and operation tables, the nullable `public.butler_secrets.codex_auth_generation_id` binding, constraints/indexes, fixed-search-path guarded operations, and the direct-DML compatibility guard without copying or parsing a credential during migration.
- [ ] 1.3 Add real effective-role ACL tests proving `PUBLIC`, connectors, and unrelated runtime roles cannot write/read the provenance boundary; authorized Codex/dashboard paths can use only the narrow mutation operations; and ordinary non-Codex `butler_secrets` writes retain their established behavior.
- [ ] 1.4 Add migration upgrade/core-only tests proving the guard leaves a legacy raw row untouched until fence-aware adoption, makes post-initialization legacy writes unprovable, and allows no orphaned `current_generation_id`/secret-binding state.

## 2. Typed system-global authority repository

- [ ] 2.1 Add failing unit tests in `tests/config/test_credential_store.py` for opaque one-time legacy adoption, complete-current reads, exact operation preparation/launch, conditional successor completion, health-only finalization, direct owner replace/revoke precedence, duplicate completion, safe malformed/unavailable results, and no secret/provenance leakage from repr/logging.
- [ ] 2.2 Add value-redacted `CodexAuthLaunchLease` and `CodexAuthBootstrapLease`, safe outcome/result enums, and typed internal methods to `src/butlers/credential_store.py`; keep a normal-operation raw document transient and `repr=False`, make bootstrap prove only the guarded never-initialized absence, preserve all non-Codex credential semantics, and remove Codex raw-value CAS as the authority identity.
- [ ] 2.3 Implement the short transactions that lock the singleton state, validate the existing raw-row/generation binding, prepare/mark/complete exactly one operation, atomically write an accepted successor, record safe health, supersede stale operations, and revoke without a local-file fallback.
- [ ] 2.4 Implement deterministic expiry/cleanup in the core maintenance path with database-time expiry and terminal-operation-only 90-day pruning; retain current/referenced opaque generations and never include raw error/provider output in terminal reasons.

## 3. Runtime and local-stage conversion

- [ ] 3.1 Add failing adapter tests in `tests/adapters/test_codex_auth_sync.py`, `tests/adapters/test_codex_refresh_lock.py`, and `tests/adapters/test_codex_adapter.py` for exact-generation launch revalidation, private stage isolation across two daemons, stale dashboard replacement, stale health withholding, malformed stage discard, no unlocked projection launch, and restart orphan non-promotion.
- [ ] 3.2 Refactor `src/butlers/core/runtimes/_codex_auth_sync.py` to remove content-digest/process-cache local successor inference; retain only strict parsing, atomic database-originated projection where required, and value-free safe diagnostics.
- [ ] 3.3 Refactor `src/butlers/core/runtimes/codex.py` so invocation and prewarm prepare/mark/complete their exact durable operations, use operation-private auth stages instead of a shared mutable auth result, carry one existing absolute deadline, and discard all stale/unprovable finalization.
- [ ] 3.4 Update `src/butlers/core/spawner.py`, `src/butlers/connectors/discretion_dispatcher.py`, and their focused tests so every Codex adapter construction receives the typed system-global authority boundary and unavailable provenance refuses only the affected Codex launch.

## 4. Dashboard, device-auth, and startup callers

- [ ] 4.1 Add failing device-auth tests in `tests/cli/test_cli_auth.py`, `tests/api/test_secrets_v2_cli_reauthorize.py`, and `tests/api/test_secrets_v2_codex_authority.py` for prepared bootstrap, owner replacement/revoke racing a device session, containment/parser failure, stale completion, and value-free session results.
- [ ] 4.2 Refactor `src/butlers/cli_auth/persistence.py` and `src/butlers/api/routers/cli_auth.py` so Codex device auth prepares before child launch and conditionally completes the exact operation after full sandbox termination; leave other providers' persistence unchanged.
- [ ] 4.3 Add failing dashboard API tests in `tests/api/test_secrets_v2_cli_mutations.py`, `tests/api/test_secrets_v2_cli_reauthorize.py`, and `tests/api/test_secrets_v2_codex_authority.py` for direct owner replacement/revoke precedence, probe health withholding, response/audit redaction, and no exposure of operation/generation fields.
- [ ] 4.4 Refactor `src/butlers/api/routers/secrets_v2.py`, `src/butlers/api/app.py`, `src/butlers/api/routers/model_settings.py`, and `src/butlers/jobs/model_verify.py` to use the guarded owner/probe paths and safe health classifications without changing documented response envelopes.
- [ ] 4.5 Add/update startup coverage in `tests/daemon/test_startup_coverage_gaps.py` and connector restoration tests, then refactor `src/butlers/daemon.py`, `src/butlers/lifecycle.py`, and `src/butlers/cli_auth/persistence.py` restoration helpers so a restart reads only the current shared binding and never promotes local state.

## 5. Documentation, completeness checks, and integration evidence

- [ ] 5.1 Add a source-completeness regression that enumerates every Codex raw load/store/CAS, subprocess/prewarm, dashboard/device-auth, startup, model-verify, direct-dispatcher, and connector-restoration call site and fails if it bypasses the typed generation-fenced boundary.
- [ ] 5.2 Update `docs/data_and_storage/credential-store.md`, `docs/identity_and_secrets/cli-runtime-auth.md`, `docs/concepts/butler-lifecycle.md`, `docs/architecture/butler-daemon.md`, and `docs/runtime/spawner.md` with the opaque-generation, restart, private-stage, precedence, redaction, and fail-closed contract; do not include credential-shaped examples or provenance identifiers.
- [ ] 5.3 Reconcile the `core-credentials`, `core-spawner`, `core-daemon`, `dashboard-api`, and `database-security` capability specs with the implementation and add requirement-ID citations to behavior-executing tests.
- [ ] 5.4 Run focused unit, API, adapter, migration, effective-role, concurrency, restart, and source-completeness suites; then run Ruff, format, strict OpenSpec validation, `make test-qg`, and review the exact change for secret/provenance redaction before deployment authorization.
