## Why

The Home butler's three core monitoring tasks — device health check, environment report, and weekly energy digest — all use `dispatch_mode="prompt"`, spawning an LLM session for work that is largely deterministic: read entity state, compare values against thresholds or stored preferences, format a message, and send via `notify()`. Each prompt-dispatched session costs tokens and adds latency for logic that can be expressed as a Python function reading from the connector-populated entity cache with configurable thresholds. Converting these to `dispatch_mode="job"` eliminates unnecessary LLM spend while keeping the same user-facing output. A secondary goal is to add maintenance scheduling (filter replacement reminders, seasonal HVAC service) as a new deterministic job, rounding out the butler's proactive home-management surface.

## What Changes

- Convert `device-health-check` scheduled task from `dispatch_mode="prompt"` to `dispatch_mode="job"` with a new deterministic job handler that reads entity states from the connector-populated `ha_entity_snapshot` cache, classifies battery/offline issues using configurable thresholds from the state store, stores memory facts, and sends a Telegram notification.
- Convert `environment-report` scheduled task from `dispatch_mode="prompt"` to `dispatch_mode="job"` with a new deterministic job handler that reads sensor entities per area from the entity snapshot cache, compares against stored comfort preferences with configurable deviation thresholds, and sends a formatted room-by-room environment report.
- Convert `weekly-energy-digest` scheduled task from `dispatch_mode="prompt"` to `dispatch_mode="job"` with a new deterministic job handler that discovers energy sensors from the entity snapshot, fetches historical statistics via HA REST API, computes top consumers and trends vs. baselines with configurable anomaly thresholds, and sends a structured weekly digest.
- Add a new `maintenance-schedule-check` job-based scheduled task that tracks recurring maintenance items (filter replacements, seasonal HVAC service, appliance warranties) and sends reminders when items are due.
- Register all four job handlers in the deterministic schedule job registry for the `home` butler.
- Add dashboard API endpoints for device inventory, energy consumption charts, and maintenance calendar to surface the data these jobs produce.

## Capabilities

### New Capabilities
- `home-deterministic-jobs`: Job handlers for device-health, environment-report, energy-digest, and maintenance-schedule — deterministic Python functions replacing prompt-based LLM dispatch
- `home-maintenance-scheduling`: Maintenance item tracking, due-date computation, and proactive reminders for recurring home maintenance tasks
- `home-dashboard-extensions`: Dashboard API endpoints for device inventory listing, energy consumption time-series, and maintenance calendar

### Modified Capabilities
- `butler-home`: Schedule definitions change from `dispatch_mode="prompt"` to `dispatch_mode="job"` for three existing tasks; one new scheduled task added; updated cron times to match job-appropriate cadences

## Impact

- **`roster/home/butler.toml`**: Three `[[butler.schedule]]` entries change `dispatch_mode` from `"prompt"` to `"job"` and gain `job_name` fields; one new `[[butler.schedule]]` entry for `maintenance-schedule-check`.
- **`src/butlers/daemon.py`**: The `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]` dict gains four new handler entries alongside the existing memory maintenance handlers.
- **`src/butlers/jobs/home.py`** (new): Module containing the four deterministic job handler functions (`run_device_health_check`, `run_environment_report`, `run_energy_digest`, `run_maintenance_schedule_check`). Each reads entity state from the connector-populated `ha_entity_snapshot` cache, loads configurable thresholds from the state store, and uses the memory storage API and notify helper. The HA REST client is used only for historical statistics queries.
- **`roster/home/api/router.py`** and **`roster/home/api/models.py`**: New dashboard endpoints and Pydantic models for device inventory, energy charts, and maintenance calendar.
- **Database**: New `home.maintenance_items` table for tracking recurring maintenance entries (item name, interval, last completed, next due, category).
- **`roster/home/migrations/`**: New Alembic migration for `maintenance_items` table.
- **Existing skills**: The skill SKILL.md files for `device-health-check`, `environment-report`, and `weekly-energy-digest` remain valid as documentation for interactive (user-triggered) invocations but are no longer the scheduled-task execution path.
- **`openspec/specs/butler-home/spec.md`**: Schedule requirement scenarios updated to reflect job-based dispatch and new maintenance task.
