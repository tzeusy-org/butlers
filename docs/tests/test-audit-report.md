# Test Suite Audit Report

**Date:** 2026-02-28
**Issue:** butlers-ltye.2 — Identify unnecessary, duplicate, and mis-categorized tests
**Based on:** `docs/tests/benchmark-report.md` hotspot analysis

---

## Executive Summary

The audit focused on the six hotspot files/directories identified in the benchmark
(`tests/connectors/`, `tests/daemon/`, `tests/api/`, `tests/modules/test_calendar_sync.py`,
`tests/config/test_schema_matrix_migrations.py`, `tests/integration/test_mailbox_module.py`)
plus root-level test files that contribute most to overall test time.

**Four categories of findings are reported below.** Highest-impact items are listed first.

---

## 1. Tests That Can Be Re-marked as `@pytest.mark.unit` (No Docker Needed)

These files use only `unittest.mock`, `AsyncMock`, `MagicMock`, `tmp_path`, or similar
in-process stubs. None of them invoke `postgres_container`, real `asyncpg.create_pool`,
or any testcontainers machinery. Adding `pytestmark = pytest.mark.unit` lets these be
selected via `-m unit` for fast feedback.

### 1a. Connector tests (none have any mark)

All tests in `tests/connectors/` are pure unit tests using mocks only. None require
Docker or external services. The entire directory is missing `pytest.mark.unit`.

| File | Tests | Est. time | Notes |
|---|---|---|---|
| `tests/connectors/test_heartbeat.py` | 20 | 16.45s total | All mocked, but uses real `asyncio.sleep` (see Section 2) |
| `tests/connectors/test_heartbeat_integration.py` | 7 | 18.33s total | All mocked, but uses real `asyncio.sleep` (see Section 2) |
| `tests/connectors/test_connector_metrics.py` | ~15 | ~1s | Pure mock, should be `unit` |
| `tests/connectors/test_connector_health.py` | ~12 | ~1s | Pure mock, should be `unit` |
| `tests/connectors/test_mcp_client.py` | ~10 | ~0.5s | Pure mock, should be `unit` |
| `tests/connectors/test_telegram_bot_connector.py` | ~80 | varies | Mocks asyncpg; backoff tests mock `asyncio.sleep` |

**Action:** Add `pytestmark = pytest.mark.unit` to all six files.

### 1b. Root-level test files (no marks, no Docker)

| File | Tests | Notes |
|---|---|---|
| `tests/test_blob_storage.py` | ~15 | Pure unit: `LocalBlobStore` with `tmp_path` |
| `tests/test_daemon_get_attachment_tool.py` | 1 | Just asserts `"get_attachment" in CORE_TOOL_NAMES` |
| `tests/test_gmail_policy.py` | ~40 | All mocked; no asyncpg or real connections |
| `tests/test_testcontainer_startup.py` | 3 | Tests conftest helpers with `monkeypatch` only |
| `tests/test_testcontainer_teardown.py` | 6 | Tests conftest helpers with `monkeypatch` only |
| `tests/test_testcontainer_teardown_retry.py` | 8 | Tests conftest helpers with `monkeypatch` only |

**Action:** Add `pytestmark = pytest.mark.unit` to all six files.

### 1c. API tests missing unit markers

| File | Notes |
|---|---|
| `tests/api/test_router_discovery.py` | Pure mock; creates temp `router.py` files, no DB |
| `tests/api/test_app_integration.py` | Uses `TestClient` + `create_app()`; no real DB — integration-level but no Docker |

`test_router_discovery.py` is clearly unit. `test_app_integration.py` might warrant an
`integration` mark if `create_app()` lifespan fires real DB connections; confirm at
implementation time.

### 1d. Daemon tests missing unit markers

| File | Tests | Notes |
|---|---|---|
| `tests/daemon/test_notify_react.py` | ~25 | All mocked; no docker usage |
| `tests/daemon/test_db_topology.py` | ~5 | Instantiates `Database` objects but never connects |

`tests/daemon/test_notify_contact_id.py` imports `asyncpg` but only to create a mock
`UndefinedTableError` — no real connection. It should also be `unit`.

---

## 2. Real `asyncio.sleep` Tests — Candidates for Time Elimination

These tests use real wall-clock `asyncio.sleep` to verify timing-based behavior.
Combined, they consume ~40 seconds of real time (plus benchmark overhead). The
pattern is well-established in the codebase (see `tests/daemon/test_liveness_reporter.py`
which already uses a `fast_sleep` mock). All can be refactored to use the
`fast_sleep`/mock-sleep pattern.

### 2a. `tests/connectors/test_heartbeat.py` — 9 real sleeps, ~15.5s total

Every async test in `TestConnectorHeartbeat` starts a heartbeat loop with `interval_s=1`
and then calls `await asyncio.sleep(1.5)` or `await asyncio.sleep(2.5)` to let the loop
run. The pattern should be replaced with a clock-advancement mock.

Specific tests with real sleeps (line numbers reference the current file):

| Test | Real sleep | Current cost |
|---|---|---|
| `test_heartbeat_sends_periodically` | `asyncio.sleep(2.5)` | ~2.5s |
| `test_heartbeat_envelope_structure` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_heartbeat_includes_health_state` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_heartbeat_graceful_shutdown` | none | <0.1s |
| `test_heartbeat_failure_does_not_crash` | `asyncio.sleep(2.5)` | ~2.5s |
| `test_heartbeat_without_checkpoint` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_collect_counters_from_prometheus` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_heartbeat_includes_capabilities_when_provided` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_heartbeat_omits_capabilities_when_not_provided` | `asyncio.sleep(1.5)` | ~1.5s |
| `test_heartbeat_omits_capabilities_when_empty_dict_returned` | `asyncio.sleep(1.5)` | ~1.5s |

**Refactor pattern:** Replace `await asyncio.sleep(N)` with `await _real_sleep(0)` repeated
times using a patched `asyncio.sleep` that immediately calls back (like `test_liveness_reporter.py`
does), or use a fake-clock approach where a patched `asyncio.sleep` increments a virtual
clock and then returns immediately.

### 2b. `tests/connectors/test_heartbeat_integration.py` — 7 real sleeps, ~17.5s total

| Test | Real sleep | Current cost |
|---|---|---|
| `test_heartbeat_disabled_via_env_no_task_created` | `asyncio.sleep(0.5)` | ~0.5s |
| `test_concurrent_heartbeats_from_multiple_connectors` | `asyncio.sleep(2.5)` | ~2.5s |
| `test_heartbeat_resilience_switchboard_connection_errors` | `asyncio.sleep(3.5)` | ~3.5s |
| `test_heartbeat_resilience_all_calls_fail` | `asyncio.sleep(2.5)` | ~2.5s |
| `test_heartbeat_metrics_collection_across_multiple_cycles` | `asyncio.sleep(3.5)` | ~3.5s |
| `test_heartbeat_instance_id_stability_across_cycles` | `asyncio.sleep(2.5)` | ~2.5s |
| `test_heartbeat_uptime_counter_increases` | `asyncio.sleep(2.5)` | ~2.5s |

**Note:** Several integration tests overlap significantly with unit tests in `test_heartbeat.py`.
See Section 3 for consolidation recommendations.

### 2c. `tests/daemon/test_scheduler_loop.py` — 4 real sleeps, ~7s total

| Test | Real sleep | Current cost |
|---|---|---|
| `TestSchedulerLoopBehavior::test_tick_called_after_interval` | `asyncio.sleep(2.5)` | ~2.5s |
| `TestSchedulerLoopBehavior::test_tick_uses_butler_name_stagger_key` | `asyncio.sleep(1.5)` | ~1.5s |
| `TestSchedulerLoopBehavior::test_custom_interval_used_in_loop` | `asyncio.sleep(1.5)` | ~1.5s |
| `TestSchedulerLoopBehavior::test_shutdown_waits_for_tick_completion` | `asyncio.sleep(0.3)` | ~0.3s |

These tests directly call `daemon._scheduler_loop()` with a 1-second `tick_interval_seconds`.
Patching `asyncio.sleep` in `butlers.daemon` to fast-forward time would reduce all to <0.01s.

### 2d. `tests/test_gmail_connector.py::TestGmailPubSubIngestion` — 2 real-time waits

| Test | Mechanism | Current cost |
|---|---|---|
| `test_pubsub_notification_triggers_history_fetch` | `asyncio.wait_for(..., timeout=2.0)` | ~2.0s |
| `test_pubsub_fallback_poll_when_no_notifications` | `asyncio.wait_for(..., timeout=3.0)` | ~3.0s |

These tests run `runtime._run_pubsub_ingestion_loop()` with a real 2–3 second timeout.
The loop internally calls `asyncio.Queue.get(timeout=...)`. Patching the queue-wait to
short-circuit would eliminate the real-time cost.

**Total estimated saving from Section 2:** ~45s (if all four groups are fixed).

---

## 3. Duplicate and Near-Duplicate Tests

### 3a. `test_pricing.py` vs `test_cost_comprehensive.py` — HIGH overlap

Both files test the same `butlers.api.pricing` module functions:
`load_pricing()`, `PricingConfig.get_model_pricing()`, `PricingConfig.estimate_cost()`.

**Overlapping test behaviors (not identical implementations, but same assertions):**

| Behavior | `test_pricing.py` class | `test_cost_comprehensive.py` class |
|---|---|---|
| Model IDs are sorted | `TestGetModelPricing::test_model_ids_sorted` | `TestPricingConfig::test_model_ids_sorted` |
| Unknown model returns None/zero | `TestGetModelPricing::test_unknown_model_returns_none` | `TestPricingConfig::test_get_model_pricing_returns_none_for_unknown` |
| Missing file raises PricingError | `TestLoadPricing::test_missing_file_raises` | `TestLoadPricing::test_missing_file_raises_error` |
| Invalid TOML raises PricingError | `TestLoadPricing::test_corrupt_toml_raises` | `TestLoadPricing::test_invalid_toml_raises_error` |
| Missing models section raises | `TestLoadPricing::test_missing_models_section_raises` | `TestLoadPricing::test_missing_models_section_raises_error` |
| Missing price field raises | `TestLoadPricing::test_missing_price_field_raises` | `TestLoadPricing::test_missing_price_field_raises_error` |
| Loads default pricing.toml | `TestDefaultPath::test_loads_repo_pricing_toml` | `TestLoadPricing::test_loads_default_pricing_toml` |
| Zero tokens → zero cost | `TestEstimateCost::test_zero_tokens` | `TestPricingConfig::test_estimate_cost_zero_tokens` |
| Large token count | `TestEstimateCost::test_large_token_counts` | `TestPricingConfig::test_estimate_cost_large_tokens` |

**Also,** `test_cost_estimation.py` (7 tests) provides a third overlapping coverage of
`estimate_session_cost()` and `load_pricing()`. The three files together have ~48 tests
covering the same `butlers.api.pricing` module where ~20 would suffice.

**Recommendation:** Consolidate into a single `tests/api/test_pricing.py` and delete
`test_cost_estimation.py` and `test_cost_comprehensive.py`. The existing `test_pricing.py`
already covers all structural error cases with proper names. `TestCostModels` from
`test_cost_comprehensive.py` (testing `CostSummary`, `DailyCost`, `TopSession`) is
distinct and should be extracted to `tests/api/test_cost_models.py`.

### 3b. `test_secrets_credentials.py` vs `test_google_credentials_credential_store.py` — Functional overlap

Both test `store_app_credentials()`, `load_app_credentials()`, and `delete_google_credentials()`
from `butlers.google_credentials`:

| Function | `test_secrets_credentials.py` | `test_google_credentials_credential_store.py` |
|---|---|---|
| `store_app_credentials()` | `TestStoreAppCredentials` (5 tests) | `TestStoreAppCredentialsWithCredentialStore` (4 tests) |
| `load_app_credentials()` | `TestLoadAppCredentials` (5 tests) | `TestLoadAppCredentialsWithCredentialStore` (3 tests) |
| `delete_google_credentials()` | `TestDeleteGoogleCredentials` (2 tests) | `TestDeleteGoogleCredentialsWithCredentialStore` (2 tests) |

The key difference: `test_secrets_credentials.py` uses a raw `asyncpg` connection mock
(the old calling convention), while `test_google_credentials_credential_store.py` uses
`CredentialStore` (the current calling convention). If the raw-connection code path is
removed from the module under test, `TestStoreAppCredentials`, `TestLoadAppCredentials`,
and `TestDeleteGoogleCredentials` in `test_secrets_credentials.py` become dead tests.

**Note:** 9 tests from `test_secrets_credentials.py` are already failing in the baseline
(see benchmark report pre-existing failures). This suggests the old calling convention
may be partially broken or superseded.

**Recommendation:** After the credential-store migration stabilizes, audit whether the
raw-connection path is still live. If not, the three failing test classes in
`test_secrets_credentials.py` should be removed (keeping the `TestUpsertCredentialsEndpoint`,
`TestDeleteCredentialsEndpoint`, and `TestGetCredentialStatusEndpoint` classes which test
the API layer exclusively).

### 3c. `test_heartbeat.py` vs `test_heartbeat_integration.py` — Near-duplicate resilience tests

Several integration tests in `test_heartbeat_integration.py` test the same behaviors
as unit tests in `test_heartbeat.py` at a coarser granularity:

| Integration test | Near-duplicate unit test |
|---|---|
| `test_heartbeat_resilience_all_calls_fail` | `test_heartbeat_failure_does_not_crash` |
| `test_heartbeat_resilience_switchboard_connection_errors` | `test_heartbeat_failure_does_not_crash` |
| `test_heartbeat_instance_id_stability_across_cycles` | `test_instance_id_stable` + `test_init_generates_instance_id` |
| `test_heartbeat_uptime_counter_increases` | Partially covered by `test_heartbeat_envelope_structure` |

Both `test_heartbeat_resilience_*` tests use real sleeps and verify the same loop-continues
behavior that `test_heartbeat_failure_does_not_crash` verifies (also with a real sleep).

**Recommendation:** After mock-sleep refactoring, consolidate the two resilience integration
tests into a single parameterized test in `test_heartbeat.py`. The `test_heartbeat_integration.py`
file should be reduced to cover only multi-connector concurrent behavior
(`test_concurrent_heartbeats_from_multiple_connectors`) and the disabled-via-env case
(`test_heartbeat_disabled_via_env_no_task_created`).

---

## 4. Obsolete and Meta Tests

### 4a. `tests/test_smoke.py` — Tests test infrastructure (meta-testing)

```python
# tests/test_smoke.py — 4 tests
def test_version():           # asserts butlers.__version__ == "0.1.0" — likely outdated
def test_spawner_result_defaults():     # tests MockSpawner fixture, not production code
async def test_mock_spawner_records_invocations():  # tests MockSpawner fixture
async def test_mock_spawner_enqueued_results():     # tests MockSpawner fixture
```

`test_version()` asserts `"0.1.0"` which is a hardcoded constant unlikely to be valid
as the project matures. The three `MockSpawner` tests test the test fixture itself, not
production behavior. These 4 tests add collection time and maintenance cost with
near-zero value.

**Recommendation:** Delete `test_spawner_result_defaults`, `test_mock_spawner_records_invocations`,
and `test_mock_spawner_enqueued_results`. Update `test_version` to check that the version
is a non-empty string rather than asserting a specific version literal.

### 4b. `tests/config/test_schema_matrix_migrations.py` — 8.49s monster, low marginal value

The single test `test_one_db_schema_table_matrix_for_core_and_enabled_modules` runs
Alembic migrations for every butler schema in the roster (all 12+ chains) against a real
Postgres container, consuming 8.49s. This test is valuable as a schema regression guard
but is the single-most-expensive test in the suite.

**This test is not obsolete** — it guards against schema drift. However, it runs on every
PR. Options:

1. Move to a nightly CI job instead of per-PR.
2. Cache the migration result and only re-run if migration files change (using a content hash
   check as a skip condition).
3. Reduce scope to run only the chains that changed (detect via `git diff --name-only`).

**Recommendation:** Schedule for nightly CI only (via `pytest -m nightly` or similar).
Tag with `@pytest.mark.nightly` and exclude from the default `make test` run.

### 4c. `tests/test_gmail_connector.py::TestWebhookAuthentication` — Tests config, not logic

Two tests in `TestWebhookAuthentication` are effectively no-ops:

```python
async def test_webhook_accepts_valid_token(...):
    # Sets up runtime, then only asserts:
    assert runtime._config.gmail_pubsub_webhook_token == "secret-token-123"

async def test_webhook_rejects_invalid_token(...):
    # Same setup, only asserts the config attribute
```

Both tests assert a config field value, not actual webhook authentication behavior. They
were likely placeholders written before the webhook handler was fully implemented.

**Recommendation:** Delete or replace with tests that actually invoke the webhook HTTP handler.

### 4d. `tests/test_daemon_get_attachment_tool.py` — Trivial assertion

The entire file contains one test:
```python
def test_get_attachment_in_core_tools():
    assert "get_attachment" in CORE_TOOL_NAMES
```

This tests a constant, not behavior. The same coverage is implicitly provided by
integration tests that use the tool. Worth keeping for documentation but has ~zero
protection value on its own.

**Recommendation:** Keep as a quick smoke test (it runs in <1ms) but add `pytestmark = pytest.mark.unit`.

---

## 5. Over-tested Modules

### 5a. `butlers.api.pricing` — 3 test files, ~48 tests

The pricing module (`butlers/api/pricing.py`) is covered by:
- `tests/api/test_pricing.py` — 16 tests
- `tests/api/test_cost_comprehensive.py` — 25 tests (heavy overlap)
- `tests/api/test_cost_estimation.py` — 7 tests (partial overlap)

Approximate 35–40% of the combined 48 tests assert the same behaviors.

### 5b. `butlers.google_credentials` — 4 test files

The Google credentials module is covered by:
- `tests/test_google_credentials.py` (model validation only — 10 tests)
- `tests/test_google_credentials_credential_store.py` (CredentialStore interface — ~30 tests)
- `tests/test_secrets_credentials.py` (raw-connection interface + API endpoints — ~30 tests)
- `tests/test_oauth_integration.py` (integration/end-to-end OAuth flows — ~20 tests)

The first three files test the same underlying functions via different calling conventions.
Consolidation into two files (unit tests + integration tests) would reduce coverage
redundancy without losing behavioral coverage.

### 5c. `tests/daemon/` — Route-execute coverage is split across 6 files

The `route.execute` tool implementation is covered by:
- `tests/daemon/test_daemon.py` (`TestRouteExecuteTool` — 1 class, partial coverage)
- `tests/daemon/test_route_execute_async_dispatch.py` (6 classes, 49 combined tests)
- `tests/daemon/test_route_execute_authz.py`
- `tests/daemon/test_route_execute_request_context_injection.py`
- `tests/daemon/test_route_execute_trace_decoupling.py`
- `tests/daemon/test_route_execute_trace_propagation.py`

This split is well-structured and the files have distinct concerns. However,
`test_daemon.py::TestRouteExecuteTool` overlaps with the specialized files.

**Recommendation:** Move `TestRouteExecuteTool` from `test_daemon.py` into the appropriate
specialized file and remove the class from `test_daemon.py`.

---

## 6. Summary Table

### Tests to Remove

| Test(s) | File | Rationale |
|---|---|---|
| `test_spawner_result_defaults`, `test_mock_spawner_records_invocations`, `test_mock_spawner_enqueued_results` | `tests/test_smoke.py` | Test fixture internals, not production code |
| `test_webhook_accepts_valid_token`, `test_webhook_rejects_invalid_token` | `tests/test_gmail_connector.py` | Only assert config attributes, not authentication logic |
| `TestStoreAppCredentials`, `TestLoadAppCredentials`, `TestDeleteGoogleCredentials` | `tests/test_secrets_credentials.py` | Overlaps with `test_google_credentials_credential_store.py`; 9 of 12 tests already failing |
| `TestPricingConfig`, `TestEstimateSessionCost`, `TestLoadPricing` | `tests/api/test_cost_comprehensive.py` | Duplicates coverage in `test_pricing.py`; move `TestCostModels` + `TestModelPricingDataclass` to new `test_cost_models.py` |
| All classes | `tests/api/test_cost_estimation.py` | Duplicates `test_pricing.py` (`TestEstimateSessionCost`) and `test_cost_comprehensive.py` (`TestPricingDependency`); latter should move to `test_deps.py` |

### Tests to Consolidate

| Source | Target | Notes |
|---|---|---|
| `test_heartbeat_integration.py` resilience tests (2 tests) | `test_heartbeat.py::TestConnectorHeartbeat` | Same behavior, duplicate verification |
| `test_daemon.py::TestRouteExecuteTool` | `test_route_execute_async_dispatch.py` or `test_route_execute_authz.py` | Consolidate into specialized file |

### Tests to Re-mark as `@pytest.mark.unit`

All files in `tests/connectors/` plus:
`tests/test_blob_storage.py`, `tests/test_daemon_get_attachment_tool.py`,
`tests/test_gmail_policy.py`, `tests/test_testcontainer_startup.py`,
`tests/test_testcontainer_teardown.py`, `tests/test_testcontainer_teardown_retry.py`,
`tests/api/test_router_discovery.py`, `tests/daemon/test_notify_react.py`,
`tests/daemon/test_db_topology.py`, `tests/daemon/test_notify_contact_id.py`.

### Tests to Optimize (Mock the Real Sleeps)

| File | Real sleep total | Priority |
|---|---|---|
| `tests/connectors/test_heartbeat_integration.py` | ~17.5s | P1 |
| `tests/connectors/test_heartbeat.py` | ~15.5s | P1 |
| `tests/daemon/test_scheduler_loop.py` | ~7s | P2 |
| `tests/test_gmail_connector.py::TestGmailPubSubIngestion` | ~5s | P2 |

**Total potential time saving from mock-sleep refactoring:** ~45s per full suite run.

---

## Notes for the Cleanup Phase

1. When removing `test_secrets_credentials.py` test classes, verify that
   `TestUpsertCredentialsEndpoint`, `TestDeleteCredentialsEndpoint`, and
   `TestGetCredentialStatusEndpoint` (which test the API layer) have no overlap
   before removal — these three classes should be **kept** as they test different
   behavior (HTTP endpoint contract, not storage functions).

2. The 15 pre-existing test failures identified in the benchmark report
   (`tests/test_secrets_credentials.py` — 9 failures, `tests/test_gmail_connector.py` — 4,
   `tests/modules/test_module_contacts.py` — 2) are caused by changes to
   `google_credentials.py` and should be fixed as a prerequisite to credential test cleanup.

3. The `test_schema_matrix_migrations.py` monster test should not be deleted — it provides
   valuable regression protection. Moving it to a nightly tag is the correct approach.

4. The `tests/connectors/test_heartbeat_integration.py` file name suggests integration tests
   but it uses only mocks. After consolidating duplicate resilience tests, consider renaming
   to `test_heartbeat_scenarios.py` to better reflect that it tests multi-connector scenarios
   with fast mocks.
