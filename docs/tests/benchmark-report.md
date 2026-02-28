# Test Suite Benchmark Report

**Date:** 2026-02-28
**Issue:** butlers-ltye.1 — Benchmark test suite and identify slowest hotspots
**Run environment:** Ubuntu Linux, CPython 3.12.4, single-process (no xdist)

---

## Executive Summary

The `tests/` suite (excluding `tests/test_db.py` and `tests/test_migrations.py`) takes
approximately **11 minutes 48 seconds** (708.58s) to run 6,089 collected tests.
The `roster/` suite adds another ~6.7 minutes on top when run in isolation.

The dominant cost categories are:

1. **API tests** — 248s / 790 timed phases — heavyweight `httpx` + mocked DB setup overhead
2. **Daemon tests** — 91s / 296 phases — complex async mocking infrastructure
3. **Real-time `asyncio.sleep` tests** — ~52s of wall-clock sleep across 81 calls that are not mocked out (heartbeat integration + scheduler loop)
4. **Testcontainers Postgres** — 3–4s per first fixture consumer per test group (session-scoped container amortizes startup, but per-test DB provisioning adds 0.7–1s each)
5. **Config / migration tests** — 51s total for 35 phases — run full Alembic migration chains against real Postgres

---

## Run Parameters

```
uv run pytest tests/ \
  --ignore=tests/test_db.py \
  --ignore=tests/test_migrations.py \
  --durations=0 -q --tb=no
```

**Result:** 5981 passed, 93 skipped, 15 failed in **708.58s (0:11:48)**

Note: 93 skipped = e2e tests that require `ANTHROPIC_API_KEY` (not set in CI/dev env).

---

## Testcontainers Overhead

The `postgres_container` fixture is **session-scoped** — the Docker container starts once and
is reused. The observed startup overhead shows up in the first fixture consumer per isolated
group.

| Observation | Time |
|---|---|
| First consumer setup time (session container + first DB provision) | 2.7 – 3.8s |
| Subsequent DB provision per test (new DB name, schema init) | 0.7 – 1.0s |
| Teardown (DB close + pool cleanup) | 0.5 – 2.4s |

**Largest setup spikes** (likely the session container first-start + first provision):

| Time | Test |
|---|---|
| 3.80s | `tests/integration/test_conversation_history_db.py::test_realtime_history_returns_messages` |
| 3.69s | `tests/modules/memory/test_embedding.py::TestEmbedIntegration::test_real_embed_dimension` |
| 3.65s | `tests/config/test_migrations.py::test_core_migrations_create_tables` |
| 3.51s | `tests/integration/test_ingest_attachments_persistence.py::test_ingest_persists_null_for_missing_attachments` |
| 3.44s | `tests/test_approvals_models.py::TestApprovalsMigration::test_migration_creates_tables` |

These large setup times each represent the session-scoped container being started for the
first time when that test group is encountered. There are approximately 14 setup events
above 2s, suggesting 14 distinct "container start" events across the run — likely because
the container is started fresh for each test **file** that uses it (or each group of tests
collected that shares a single asyncio event loop).

---

## Top-20 Slowest Individual Tests

Ranked by call-phase wall-clock time.

| Rank | Time (s) | Test |
|---|---|---|
| 1 | 8.49 | `tests/config/test_schema_matrix_migrations.py::test_one_db_schema_table_matrix_for_core_and_enabled_modules` |
| 2 | 7.01 | `tests/modules/test_calendar_sync.py::TestGoogleProviderSyncIncremental::test_non_410_error_raises_request_error` |
| 3 | 4.34 | `tests/core/test_spawner_memory_context.py::TestFetchMemoryContext::test_returns_none_when_tool_raises` |
| 4 | 3.51 | `tests/connectors/test_heartbeat_integration.py::test_heartbeat_resilience_switchboard_connection_errors` |
| 5 | 3.51 | `tests/connectors/test_heartbeat_integration.py::test_heartbeat_metrics_collection_across_multiple_cycles` |
| 6 | 3.36 | `tests/connectors/test_heartbeat.py::TestConnectorHeartbeat::test_heartbeat_failure_does_not_crash` |
| 7 | 3.27 | `tests/connectors/test_heartbeat_integration.py::test_heartbeat_resilience_all_calls_fail` |
| 8 | 3.00 | `tests/test_gmail_connector.py::TestGmailPubSubIngestion::test_pubsub_fallback_poll_when_no_notifications` |
| 9 | 2.77 | `tests/connectors/test_telegram_bot_connector.py::test_polling_backoff_increases_on_consecutive_failures` |
| 10 | 2.58 | `tests/daemon/test_scheduler_loop.py::TestSchedulerLoopBehavior::test_tick_exception_does_not_break_loop` |
| 11 | 2.51 | `tests/daemon/test_scheduler_loop.py::TestSchedulerLoopBehavior::test_tick_called_after_interval` |
| 12 | 2.51 | `tests/daemon/test_daemon.py::TestNotifyTool::test_notify_connection_error` |
| 13 | 2.51 | `tests/connectors/test_heartbeat.py::TestConnectorHeartbeat::test_heartbeat_sends_periodically` |
| 14 | 2.51 | `tests/connectors/test_heartbeat_integration.py::test_heartbeat_uptime_counter_increases` |
| 15 | 2.51 | `tests/connectors/test_heartbeat_integration.py::test_heartbeat_instance_id_stability_across_cycles` |
| 16 | 2.51 | `tests/connectors/test_heartbeat_integration.py::test_concurrent_heartbeats_from_multiple_connectors` |
| 17 | 2.18 | `tests/modules/test_calendar_sync.py::TestCalendarModuleInternalProjectionPoller::test_run_internal_projection_poller_error_does_not_stop_loop` |
| 18 | 2.08 | `tests/config/test_switchboard_notifications_migration.py::test_switchboard_notifications_migration_creates_table` |
| 19 | 2.03 | `tests/config/test_switchboard_message_inbox_partition_migration.py::test_partition_maintenance_and_downgrade_round_trip` |
| 20 | 2.00 | `tests/test_gmail_connector.py::TestGmailPubSubIngestion::test_pubsub_notification_triggers_history_fetch` |

**Root causes:**
- Ranks 1: Full schema migration across all butler schemas (3 in 1 test)
- Ranks 2, 17: Calendar sync poller uses `asyncio.sleep(3600)` which is cancelled after a timeout
- Rank 3: Timeout-based test (spawner raises after a wait period)
- Ranks 4–7, 11–16: Real `asyncio.sleep(2.5–3.5)` calls in heartbeat integration tests
- Rank 8: Gmail PubSub polling falls back and waits for real timer
- Rank 9: Telegram polling backoff test uses real time progression

---

## Top-10 Slowest Test Files

Ranked by total wall-clock time (setup + call + teardown).

| Rank | Total (s) | Tests | File |
|---|---|---|---|
| 1 | 31.86 | 103 | `tests/daemon/test_daemon.py` |
| 2 | 19.50 | 52 | `tests/api/test_switchboard_triage_rules.py` |
| 3 | 18.33 | 7 | `tests/connectors/test_heartbeat_integration.py` |
| 4 | 16.45 | 10 | `tests/connectors/test_heartbeat.py` |
| 5 | 14.74 | 120 | `tests/core/test_core_scheduler.py` |
| 6 | 14.06 | 40 | `tests/api/test_switchboard_backfill.py` |
| 7 | 12.99 | 22 | `tests/modules/test_calendar_sync.py` |
| 8 | 12.97 | 13 | `tests/config/test_migrations.py` |
| 9 | 12.91 | 50 | `tests/integration/test_mailbox_module.py` |
| 10 | 12.07 | 3 | `tests/config/test_schema_matrix_migrations.py` |

**Notes:**
- `test_daemon.py` is slow due to sheer size (102 tests) with per-test async setup overhead
- `test_heartbeat_integration.py` has 7 tests but each includes 2.5–3.5s real sleeps (7 tests × ~2.6s avg = 18.2s)
- `test_heartbeat.py` has 10 tests with 1.5–2.5s real sleeps each
- `test_schema_matrix_migrations.py` has only 3 tests but one is the 8.49s monster

---

## Time Distribution by Test Directory

| Directory | Total Time (s) | Test Phases | Avg/Phase (s) |
|---|---|---|---|
| `tests/api/` | 248.37 | 790 | 0.31 |
| `tests/daemon/` | 91.23 | 296 | 0.31 |
| `tests/integration/` | 55.75 | 215 | 0.26 |
| `tests/config/` | 50.56 | 35 | 1.44 |
| `tests/core/` | 46.88 | 305 | 0.15 |
| `tests/connectors/` | 42.97 | 76 | 0.57 |
| `tests/modules/` | 38.99 | 169 | 0.23 |
| `tests/tools/` | 32.21 | 160 | 0.20 |
| `tests/` (root) | 23.70 | 65 | 0.36 |
| `tests/features/` | 11.39 | 58 | 0.20 |
| `tests/modules/memory/` | 8.90 | 28 | 0.32 |
| `tests/telemetry/` | 0.22 | 1 | 0.22 |
| `tests/cli/` | 0.04 | 4 | 0.01 |
| `tests/e2e/` | 0.01 | 1 | 0.01 |

**Key insight:** `tests/api/` accounts for **248s (35%)** of total test time with the highest
per-phase average of any large group. All API tests use `httpx.AsyncClient` against
`create_app()` — the FastAPI app is created per test class or per test, and `lifespan`
events run on each `AsyncClient` instantiation.

---

## Hotspot Analysis

### Hotspot 1: Real asyncio.sleep in Time-Based Tests (~52s aggregate)

**Affected files:**
- `tests/connectors/test_heartbeat_integration.py` — 7 calls totaling ~21s of sleep
- `tests/connectors/test_heartbeat.py` — many calls totaling ~16s
- `tests/daemon/test_scheduler_loop.py` — 4 calls totaling ~8s
- `tests/connectors/test_telegram_bot_connector.py` — 1 call at 2.77s

These tests verify timing behavior (heartbeat interval, retry backoff, scheduler ticks)
using real wall-clock sleeps. The fix is to mock `asyncio.sleep` and use a `fake_clock`
pattern, or use `pytest-anyio` time advancement. Some tests in `test_liveness_reporter.py`
already demonstrate the pattern with `fast_sleep`.

### Hotspot 2: tests/api/ App Setup Overhead (~248s total)

Every API test creates a `create_app()` instance. The FastAPI lifespan runs on each
`httpx.AsyncClient` instantiation. This includes route discovery, middleware setup, and
DB dependency wiring. Across 790+ timed phases at ~0.31s average, the cumulative cost is
significant.

**Potential speedups:**
- Use a module-scoped or session-scoped `app` fixture instead of per-test instantiation
- Cache `create_app()` result across tests that don't need DB mutations

### Hotspot 3: Config/Migration Tests (~51s total for 35 phases)

Each migration test provisions a real Postgres DB and runs Alembic. Average is 1.44s/phase.
These are inherently slow but the count is low (35 phases). The main bottleneck is
`test_schema_matrix_migrations.py` at 8.49s which runs migrations for all butler schemas.

### Hotspot 4: Test Collection Time (11.15s)

Collection of 6,089 tests takes 11.15s before any test runs. This is significant for
incremental test runs. The `--import-mode=importlib` flag is already in use (good).

The 4.11s **teardown** in `tests/tools/test_tools_loader.py::test_extraction_can_import_switchboard_route`
suggests that importing `butlers.tools.switchboard` triggers a heavy module initialization
that is torn down after the test. This import chain may also be slowing collection.

### Hotspot 5: Roster Test Suite Pre-existing Failures

When running `roster/` directly, 465 of 1,711 tests fail. This is a separate concern
from performance but indicates the roster tests are not part of the standard CI baseline
(the CLAUDE.md quality gate command only runs `tests/`).

---

## Pre-Existing Test Failures (tests/ only)

The following 15 tests fail in the current codebase and are **not related to this benchmark
work**. They appear to be pre-existing failures likely caused by recent changes to
`google_credentials.py` and `secrets_credentials` (see git status: both modified):

**`tests/modules/test_module_contacts.py`** (2 failures):
- `TestModuleStartup::test_startup_missing_credentials_raises_actionable_error`
- `TestModuleStartup::test_startup_no_credential_store_raises_actionable_error`

**`tests/test_gmail_connector.py`** (4 failures):
- `TestResolveGmailCredentialsFromDb::test_returns_credentials_when_db_has_stored_credentials`
- `TestResolveGmailCredentialsFromDb::test_resolves_pubsub_webhook_token_from_db`
- `TestResolveGmailCredentialsFromDb::test_result_has_no_pubsub_token_when_not_stored`
- `TestResolveGmailCredentialsFromDb::test_uses_shared_schema_fallback_with_schema_scoped_search_path`

**`tests/test_secrets_credentials.py`** (9 failures):
- `TestStoreAppCredentials::test_stores_client_id_and_secret_without_existing_row`
- `TestStoreAppCredentials::test_preserves_refresh_token_from_existing_row`
- `TestStoreAppCredentials::test_strips_whitespace`
- `TestLoadAppCredentials::test_returns_none_when_no_row`
- `TestLoadAppCredentials::test_returns_full_credentials`
- `TestLoadAppCredentials::test_returns_partial_credentials_without_refresh_token`
- `TestLoadAppCredentials::test_returns_none_when_client_id_missing`
- `TestLoadAppCredentials::test_parses_json_string_credentials`
- `TestDeleteGoogleCredentials::test_returns_false_when_no_row`

---

## Optimization Priority List

Ordered by estimated impact:

| Priority | Area | Estimated Saving | Effort |
|---|---|---|---|
| 1 | Mock `asyncio.sleep` in heartbeat/scheduler tests | 34s+ | Medium |
| 2 | Session-scoped `app` fixture for API tests | 50–100s | Medium |
| 3 | Parallelize with `pytest-xdist -n auto` | 50–70% of total | Medium |
| 4 | Reduce collection time (lazy imports in tools modules) | 5–10s | Medium |
| 5 | Investigate calendar sync 7s test (long-running mock loop) | 7s | Low |

### Note on pytest-xdist

`pytest-xdist` is already installed (in dev deps). Default `addopts` does not enable it.
The session-scoped `postgres_container` fixture would need to become worker-scoped or use
a shared container registry. The existing testcontainer teardown resilience code in
`conftest.py` already handles some race conditions, but xdist requires more careful
fixture scoping. Enabling `-n auto` without DB fixture changes would likely cause failures.

---

## Data Appendix

### Raw Run Stats

- **Command:** `pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py --durations=0 -q --tb=no`
- **Python:** CPython 3.12.4
- **Total tests collected:** 6,089
- **Collection time:** 11.15s
- **Total run time:** 708.58s (0:11:48)
- **Passed:** 5,981
- **Skipped:** 93 (all e2e tests — require `ANTHROPIC_API_KEY`)
- **Failed:** 15 (pre-existing, unrelated to this work)
- **Hidden durations:** 15,972 items < 0.005s

### Durations log

The raw `--durations=0` output is stored at:
`.tmp/test-logs/benchmark-durations-20260228-131102.log` (in the worktree, not committed).
