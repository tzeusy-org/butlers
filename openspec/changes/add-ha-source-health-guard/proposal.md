## Why

`HomeAssistantModule` has no memory of whether its Home Assistant connection
is actually healthy. `_persist_entity_snapshot` (`roster/home/modules/__init__.py`)
upserts one row per entity into `ha_entity_snapshot` every snapshot cycle and
stamps `captured_at = now()` unconditionally — regardless of whether the
underlying `_entity_cache` was refreshed from a live HA contact or is stale
from before an outage started. REST poll failures during the fallback loop
were logged and swallowed with no durable record. Meanwhile every deterministic
job handler in `src/butlers/jobs/home.py` (`run_energy_digest`,
`run_device_health_check`, `run_environment_report`) and the generic
`_read_entity_snapshot` reader trust `ha_entity_snapshot` at face value: an HA
outage silently reads as "healthy house" everywhere downstream, because a
stale snapshot looks identical to a fresh one once `captured_at` has been
re-stamped by a cycle that never actually talked to HA.

This is Slice 1 of a 4-slice Jarvis-pursuit move (bu-8cdl1.12, run 11 #12).
Slices 2-4 (durable history views over `connectors.home_assistant_history`,
digest integration surfacing the gap in digest narrative text, and anomaly
baselines) are separately scoped and NOT part of this change. This slice is
the trust-fix defect only: failure must never impersonate health.

## What Changes

- **New `ha_source_health` table** (`roster/home/migrations/003_ha_source_health.py`):
  a single keyed row per HA source (`source` TEXT PK, currently always
  `'home_assistant'`) tracking `status` (`'healthy'` | `'error'`),
  `last_success_at`, `last_error_at`, `last_error`, `updated_at`. Idempotent
  keyed upsert — never grows past one row per source.
- **Write-side instrumentation** in `HomeAssistantModule`
  (`roster/home/modules/__init__.py`): `_record_ha_source_success()` /
  `_record_ha_source_error(error)` upsert the row. Wired into the two
  "successful contact" points (WS auth completing, REST `/api/states` fetch
  completing) and the two swallow points that previously only logged a
  warning (WS connect failure at startup, REST poll failure in the polling
  fallback loop).
- **Read-side guard** in `src/butlers/jobs/home.py`: new
  `HASourceUnmeasurableError` and `_require_ha_source_healthy(pool)`. Fails
  closed — a missing `ha_source_health` row (no successful contact ever
  recorded) is treated the same as an explicit `'error'` status. Wired into
  the generic `_read_entity_snapshot` reader and the three job entry points
  that read `ha_entity_snapshot` directly (`run_energy_digest`,
  `run_device_health_check`, `run_environment_report`): each now checks HA
  source health before trusting the snapshot and, on an unhealthy/unknown
  source, sends an owner notification distinct from the existing
  "snapshot empty" alert and returns `{"error": "ha_source_unmeasurable",
  "last_good_at": <timestamp-or-None>}` instead of proceeding.
- **Out of scope (deferred to later slices/beads):** the dashboard API
  (`roster/home/api/router.py`), `src/butlers/jobs/context_producers.py`,
  `src/butlers/jobs/briefing.py`, and `src/butlers/jobs/health_ha_reader.py`
  also read `ha_entity_snapshot` directly but are not guarded by this slice
  — flagged as a discovered follow-up rather than folded into this PR to
  keep it reviewable. No new polling infrastructure is introduced (the
  history table this move's later slices build on already exists); no ML
  anomaly detection; no history views; no digest-narrative integration.

## Impact

- Affected specs: `module-home-assistant` (new "HA Source Health Recording"
  requirement), `home-deterministic-jobs` (new "HA Source Health Guard for
  Snapshot Readers" requirement).
- Affected code: `roster/home/migrations/003_ha_source_health.py` (new),
  `roster/home/modules/__init__.py`, `src/butlers/jobs/home.py`.
- Affected tests: `tests/modules/test_module_home_assistant.py` (extended —
  success/error upsert idempotence, wiring at the four instrumentation
  points), `tests/jobs/test_home_shared_helpers.py` (extended —
  `_require_ha_source_healthy` happy/outage/missing-row paths,
  `_read_entity_snapshot` guard), `tests/jobs/test_home_energy_digest.py`,
  `tests/jobs/test_home.py`, `tests/jobs/test_home_environment_report.py`
  (each extended — outage path returns `ha_source_unmeasurable` before
  querying the snapshot).
