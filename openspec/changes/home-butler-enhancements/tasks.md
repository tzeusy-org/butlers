## 1. Database Migration

- [ ] 1.1 Create Alembic migration `003_create_maintenance_items.py` in `roster/home/migrations/` adding `home.maintenance_items` table (id UUID PK, name TEXT UNIQUE, category TEXT, interval_days INT, last_completed_at TIMESTAMPTZ, next_due_at TIMESTAMPTZ, notes TEXT, created_at, updated_at)
- [ ] 1.2 In the same migration (or a follow-up), seed default monitoring threshold values into the state store: `home:thresholds:battery`, `home:thresholds:offline_hours`, `home:thresholds:comfort_defaults`, `home:thresholds:comfort_deviation`, `home:thresholds:energy` — using INSERT ON CONFLICT DO NOTHING to preserve any user-customized values on re-run
- [ ] 1.3 Write migration test verifying table creation, rollback, and threshold seed idempotency

## 2. Shared Entity Cache and Job Context

- [ ] 2.1 Create `src/butlers/jobs/home.py` with a `HomeJobContext` helper that resolves HA URL and token from butler config / owner contact info, constructs a short-lived `httpx.AsyncClient` (for REST-only historical queries), and provides an async context manager for job handlers
- [ ] 2.2 Add a shared `_load_thresholds(pool, key, defaults)` helper in `src/butlers/jobs/home.py` that reads a `home:thresholds:*` key from the state store, parses the JSON value, and returns the stored config or provided defaults if the key is missing (logging a WARNING on fallback)
- [ ] 2.3 Add a shared `_read_entity_snapshot(pool, domain_filter=None)` helper that queries the `ha_entity_snapshot` table for current entity states, returning typed dicts; raise an empty-snapshot error if the table has no rows
- [ ] 2.4 Add a shared `_send_notify` helper in `src/butlers/jobs/home.py` that calls the notify Python API with `channel="telegram"` and `intent="send"`
- [ ] 2.5 Write unit tests for `HomeJobContext` construction and cleanup
- [ ] 2.6 Write unit tests for `_load_thresholds` (key exists, key missing with fallback, malformed JSON handling)
- [ ] 2.7 Write unit tests for `_read_entity_snapshot` (populated table, empty table error)

## 3. Device Health Check Job

- [ ] 3.1 Implement `run_device_health_check(pool, job_args)` in `src/butlers/jobs/home.py` — reads entities from `ha_entity_snapshot` via `_read_entity_snapshot`, loads battery and offline thresholds via `_load_thresholds`, classifies battery (critical/warning/info) and offline (critical/warning) entities, stores memory facts, sends Telegram notification
- [ ] 3.2 Write unit tests for battery severity classification logic using configurable thresholds (default: ≤10% critical, 11-20% warning, 21-30% info) and verify custom thresholds are respected
- [ ] 3.3 Write unit tests for offline device classification using configurable hour thresholds (default: >24h critical, >1h warning)
- [ ] 3.4 Write integration test with mocked entity snapshot table verifying end-to-end flow (entity snapshot read → threshold load → classification → memory storage → notification)

## 4. Environment Report Job

- [ ] 4.1 Implement `run_environment_report(pool, job_args)` in `src/butlers/jobs/home.py` — discovers areas and sensors from `ha_entity_snapshot`, reads current values from snapshot, retrieves comfort preferences from memory, loads deviation thresholds from state store (`home:thresholds:comfort_defaults`, `home:thresholds:comfort_deviation`), classifies deviations, sends room-by-room report
- [ ] 4.2 Write unit tests for deviation classification logic using configurable thresholds (ok/minor/moderate/critical) and verify custom thresholds are respected
- [ ] 4.3 Write unit tests for default healthy range fallback when no stored preferences exist (falls back to state store comfort_defaults, then hardcoded defaults)
- [ ] 4.4 Write integration test with mocked entity snapshot table and memory storage verifying report composition and notification

## 5. Energy Digest Job

- [ ] 5.1 Implement `run_energy_digest(pool, job_args)` in `src/butlers/jobs/home.py` — discovers energy sensors from `ha_entity_snapshot`, fetches weekly historical statistics via HA REST API (`recorder/get_statistics_during_period` — REST-only, not in connector cache), loads energy thresholds from state store (`home:thresholds:energy`), computes top consumers, compares vs. baselines, detects anomalies, sends structured digest
- [ ] 5.2 Write unit tests for anomaly detection logic using configurable thresholds (default: ≥20% above baseline = anomaly, ≥100% = high severity) and verify custom thresholds are respected
- [ ] 5.3 Write unit tests for top consumer ranking and percentage computation
- [ ] 5.4 Write integration test with mocked entity snapshot table (sensor discovery) and mocked HA REST API (historical statistics) verifying digest composition and notification
- [ ] 5.5 Write unit test for no-energy-sensors fallback path (empty entity snapshot)
- [ ] 5.6 Write unit test for HA REST unreachable during statistics fetch (job completes with partial data from snapshot only)

## 6. Maintenance Schedule Check Job

- [ ] 6.1 Implement `run_maintenance_schedule_check(pool, job_args)` in `src/butlers/jobs/home.py` — queries maintenance_items for due/overdue/upcoming items, classifies by overdue severity, sends reminder notification
- [ ] 6.2 Write unit tests for overdue classification (0-7 days = due, 8-30 days = overdue, >30 days = critical)
- [ ] 6.3 Write integration test with seeded maintenance_items verifying reminder notification content

## 7. Job Registry Registration

- [ ] 7.1 Add `device_health_check`, `environment_report`, `energy_digest`, and `maintenance_schedule_check` handlers to `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]` in `src/butlers/daemon.py`
- [ ] 7.2 Write test verifying all four handlers are registered and callable

## 8. Butler TOML Schedule Updates

- [ ] 8.1 Update `roster/home/butler.toml`: change `device-health-check` from `dispatch_mode="prompt"` to `dispatch_mode="job"` with `job_name="device_health_check"`
- [ ] 8.2 Update `roster/home/butler.toml`: change `environment-report` from `dispatch_mode="prompt"` to `dispatch_mode="job"` with `job_name="environment_report"`
- [ ] 8.3 Update `roster/home/butler.toml`: change `weekly-energy-digest` from `dispatch_mode="prompt"` to `dispatch_mode="job"` with `job_name="energy_digest"`
- [ ] 8.4 Add `maintenance-schedule-check` schedule entry to `roster/home/butler.toml` with `cron="0 10 * * 1"`, `dispatch_mode="job"`, `job_name="maintenance_schedule_check"`

## 9. Maintenance MCP Tools

- [ ] 9.1 Add `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_list`, `ha_maintenance_remove` MCP tools to the home_assistant module in `roster/home/modules/__init__.py`
- [ ] 9.2 Write unit tests for each maintenance MCP tool (create, complete with next_due_at recomputation, list with filters, remove)
- [ ] 9.3 Write test for duplicate name rejection on create

## 10. Dashboard API Endpoints

- [ ] 10.1 Add `GET /api/home/devices` endpoint to `roster/home/api/router.py` with domain, area, and health filters plus pagination
- [ ] 10.2 Add `DeviceInventoryEntry` Pydantic model to `roster/home/api/models.py`
- [ ] 10.3 Add `GET /api/home/energy` endpoint with period and date range parameters, proxying to HA statistics API
- [ ] 10.4 Add `GET /api/home/energy/top-consumers` endpoint returning ranked device consumption
- [ ] 10.5 Add `EnergyDataPoint` and `TopConsumerEntry` Pydantic models to `roster/home/api/models.py`
- [ ] 10.6 Add `GET /api/home/maintenance` endpoint with category and status filters
- [ ] 10.7 Add `POST /api/home/maintenance` and `POST /api/home/maintenance/{item_id}/complete` and `DELETE /api/home/maintenance/{item_id}` endpoints
- [ ] 10.8 Add `MaintenanceItemResponse` Pydantic model to `roster/home/api/models.py`
- [ ] 10.9 Add `GET /api/home/settings/thresholds` endpoint returning all `home:thresholds:*` state store values as a structured JSON response
- [ ] 10.10 Add `PATCH /api/home/settings/thresholds` endpoint accepting partial threshold updates (e.g., `{"battery": {"critical": 15}}` merges into existing battery thresholds); validate numeric ranges before persisting
- [ ] 10.11 Add `ThresholdConfig` and `ThresholdUpdateRequest` Pydantic models to `roster/home/api/models.py`
- [ ] 10.12 Write integration tests for all new dashboard endpoints including threshold CRUD

## 11. Spec Updates

- [ ] 11.1 Archive current `butler-home` spec and apply the modified schedule requirement (prompt→job dispatch, new maintenance task)
- [ ] 11.2 Archive new specs (`home-deterministic-jobs`, `home-maintenance-scheduling`, `home-dashboard-extensions`) into `openspec/specs/`
