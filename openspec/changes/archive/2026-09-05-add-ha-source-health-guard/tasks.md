## 1. Schema

- [x] 1.1 `roster/home/migrations/003_ha_source_health.py`: create
      `ha_source_health` (source TEXT PK, status TEXT CHECK ('healthy'|'error'),
      last_success_at, last_error_at, last_error, updated_at), with a working
      downgrade.

## 2. Write-side instrumentation

- [x] 2.1 `_record_ha_source_success()` / `_record_ha_source_error(error)` on
      `HomeAssistantModule` — idempotent keyed upsert (`ON CONFLICT (source)
      DO UPDATE`).
- [x] 2.2 Wire success recording into `_ws_connect()` (WS auth completing),
      `_dispatch_ws_message()` (WebSocket pong), and
      `_seed_entity_cache_from_rest()` (REST fetch completing).
- [x] 2.3 Wire error recording into connect, post-authentication setup,
      disconnect, keepalive, reconnect, and REST poll failure branches.

## 3. Read-side guard

- [x] 3.1 `HASourceUnmeasurableError` + `_require_ha_source_healthy(pool)` in
      `src/butlers/jobs/home.py` — fails closed on a missing/error row, a
      missing successful-contact timestamp, or an expired five-minute lease.
- [x] 3.2 Wire into `_read_entity_snapshot`.
- [x] 3.3 Wire into `run_energy_digest`, `run_device_health_check`,
      `run_environment_report` — each returns
      `{"error": "ha_source_unmeasurable", "last_good_at": ...}` and sends a
      distinct owner notification before the existing empty-snapshot check.

## 4. Tests

- [x] 4.1 Module tests: success/error upsert idempotence (keyed upsert,
      repeated calls converge on one row), liveness renewal on pong, and
      immediate revocation on transport/setup failure (mocked pool).
- [x] 4.2 Job-handler tests: happy path (healthy row present), simulated
      outage (`status='error'`) returns `ha_source_unmeasurable` before
      querying `ha_entity_snapshot`, missing-row and expired-lease fail-closed
      paths.

## 5. Contract and verification

- [x] 5.1 Add spec deltas to `module-home-assistant` and
      `home-deterministic-jobs`.
- [x] 5.2 `openspec validate --strict` on both changed specs.
- [x] 5.3 Run `make lint` and the affected `roster/home`/`jobs/home` test
      files.
