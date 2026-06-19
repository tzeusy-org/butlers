## 1. Smoke Tier Scaffolding

- [ ] 1.1 Register the `smoke` marker in `pyproject.toml` `[tool.pytest.ini_options]` markers alongside `unit`, `integration`, `nightly`, `e2e`
- [ ] 1.2 Decide and document smoke-test home (a `tests/smoke/` subdirectory and/or `@pytest.mark.smoke` on existing standalone files) consistent with the Test Directory Structure requirement
- [ ] 1.3 Add a shared smoke fixture/helper layer that wires `MockSpawner` and the session-scoped `postgres_container` so no smoke test reaches a real LLM

## 2. Clean-Start Smoke Test

- [ ] 2.1 Smoke test: importing `butlers` and `butlers.cli` succeeds with no external-service side effects
- [ ] 2.2 Smoke test: `butlers --help` (entrypoint `butlers.cli:cli`) exits 0 and exposes the `run` subcommand used by the Docker CMD
- [ ] 2.3 Smoke test: exercise the `uv run --frozen --no-dev butlers --help` deployment form resolves the same entrypoint (skip-with-reason if frozen env unavailable)

## 3. Migration Smoke Test

- [ ] 3.1 Smoke test: `run_migrations(chain="core")` from empty DB to head succeeds and `alembic_version` records core head (reuse `create_migration_db` + `bootstrap_extensions`)
- [ ] 3.2 Smoke test: latest-revision downgrade/upgrade round-trip yields a schema equivalent to direct empty-to-head
- [ ] 3.3 Verify no duplication with `tests/config/test_migrations.py`; cross-reference rather than re-assert table/schema presence already covered there

## 4. Daemon Lifecycle Smoke Test

- [ ] 4.1 Smoke test: `ButlerDaemon.start()` reaches `_accepting_connections is True`, `_started_at` set, DB pool connected (mock spawner)
- [ ] 4.2 Smoke test: `shutdown()` completes cleanly — `_accepting_connections is False`, background tasks cancelled/awaited, pools closed
- [ ] 4.3 Smoke test: a non-fatal module startup failure is isolated — daemon still reaches accepting-connections and the module is recorded `failed`/`cascade_failed` in `_module_statuses`

## 5. Route-Inbox Recovery Smoke Test

- [ ] 5.1 Smoke test: `route_inbox_scan_unprocessed` returns both `accepted` and `processing` rows older than the grace period with id/received_at/route_envelope
- [ ] 5.2 Smoke test: `route_inbox_recovery_sweep` invokes the dispatch fn once per stuck row and returns the recovered count
- [ ] 5.3 Smoke test: a recovered row reaches a terminal state (`processed` or `errored`), never stuck in `processing`

## 6. Dashboard Health Smoke Test

- [ ] 6.1 Smoke test: GET `/api/health` and `/health` return 200 with `{"status": "ok"}`
- [ ] 6.2 Smoke test: health endpoints succeed without an API key (in `_PUBLIC_PATHS`, bypassing API-key and audit middleware)
- [ ] 6.3 Smoke test: health does not report healthy before lifespan startup completes (proves real liveness, not a static string)

## 7. CI Gate and Release Evidence

- [ ] 7.1 Add a fast smoke step to `.github/workflows/ci.yml` `check` job selecting `-m smoke` and excluding `tests/e2e` / real-LLM deps; a smoke failure fails CI
- [ ] 7.2 Emit a release-evidence record (command, commit SHA, duration, pass/fail, skipped classes) as a CI artifact/log line referenceable from release notes
- [ ] 7.3 Confirm the smoke gate runs without `ANTHROPIC_API_KEY` / `claude` CLI and completes within the fast-gate time budget

## 8. Verification

- [ ] 8.1 Run `uv run pytest -m smoke` locally and confirm green and fast
- [ ] 8.2 Run lint/format gates on new test files
- [ ] 8.3 Confirm CI `check` job exercises the smoke gate on a PR
