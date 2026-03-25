# Home Deterministic Jobs

## Purpose

Deterministic Python job handlers for the Home butler's scheduled monitoring tasks. These handlers replace prompt-based LLM dispatch with threshold-based classification, memory storage, and Telegram notifications — eliminating LLM costs for formulaic monitoring work. Jobs read current entity state from the connector-populated `ha_entity_snapshot` table and load monitoring thresholds from the state store (`home:thresholds:*`), falling back to direct HA REST API calls only for historical statistics queries.

## Requirements

### Requirement: Job Handler Signature and Registration

All home deterministic job handlers follow the standard `_DeterministicScheduleJobHandler` signature and are registered in the daemon's job registry.

#### Scenario: Job handler signature

- **WHEN** a home deterministic job handler is invoked by the scheduler
- **THEN** it SHALL accept `pool: asyncpg.Pool` and `job_args: dict[str, Any] | None` as parameters
- **AND** it SHALL return `dict[str, Any]` containing a summary of work performed

#### Scenario: Job registry registration

- **WHEN** the daemon initializes `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
- **THEN** the `"home"` entry SHALL include handlers for `device_health_check`, `environment_report`, `energy_digest`, and `maintenance_schedule_check` alongside the existing memory maintenance handlers

#### Scenario: Job handler module location

- **WHEN** the daemon imports home job handlers
- **THEN** they SHALL be imported from `butlers.jobs.home`

### Requirement: Threshold Loading from State Store

All monitoring thresholds are loaded from the state store at job invocation time, with hardcoded defaults used only if no stored value exists. This enables user-configurable monitoring sensitivity.

#### Scenario: Load threshold from state store

- **WHEN** a home deterministic job handler starts execution
- **THEN** it SHALL query the state store for the relevant `home:thresholds:*` key(s) needed by that job
- **AND** it SHALL parse the stored JSON value into a typed threshold configuration
- **AND** the loaded thresholds SHALL be used for all classification decisions during that job run

#### Scenario: Use default if no stored threshold

- **WHEN** a `home:thresholds:*` key is not found in the state store (e.g., state store was cleared, migration not yet run)
- **THEN** the job SHALL fall back to hardcoded default values identical to the seeded defaults:
  - Battery: `{"critical": 10, "warning": 20, "info": 30}`
  - Offline hours: `{"critical": 24, "warning": 1}`
  - Comfort defaults: `{"temp_min_f": 68, "temp_max_f": 76, "humidity_min": 30, "humidity_max": 60, "co2_max_ppm": 1000}`
  - Comfort deviation: `{"minor_temp_f": 2, "moderate_temp_f": 5, "minor_humidity": 10, "moderate_humidity": 20, "critical_temp_low_f": 60, "critical_temp_high_f": 85, "critical_co2_ppm": 1500, "critical_humidity_low": 15, "critical_humidity_high": 80}`
  - Energy: `{"anomaly_pct": 20, "high_severity_pct": 100}`
- **AND** the job SHALL log a WARNING indicating that default thresholds are being used

#### Scenario: Threshold update takes effect on next job run

- **WHEN** a user updates a threshold value via the dashboard or conversation (e.g., "set battery critical threshold to 15%")
- **THEN** the updated value SHALL be persisted to the state store under the appropriate `home:thresholds:*` key
- **AND** the next scheduled job run SHALL pick up the new threshold value (no daemon restart required)

### Requirement: Device Health Check Job

The `device_health_check` job reads all HA entity states, classifies battery and connectivity issues by severity, stores findings in memory, and sends a Telegram notification.

#### Scenario: Entity survey from connector cache

- **WHEN** the `device_health_check` job runs
- **THEN** it SHALL query the `ha_entity_snapshot` table to retrieve all current entity states
- **AND** it SHALL identify entities with state `"unavailable"` or `"unknown"` as offline
- **AND** it SHALL identify entities whose `entity_id` or `friendly_name` contains `battery` and whose numeric state value is at or below the configured `info` threshold (default 30%) as battery-related

#### Scenario: Battery severity classification

- **WHEN** a battery sensor entity is found with a numeric state value
- **THEN** it SHALL load thresholds from state store key `home:thresholds:battery` (default: `{"critical": 10, "warning": 20, "info": 30}`)
- **AND** it SHALL be classified as:
  - `critical` if value is at or below the `critical` threshold (default 10%)
  - `warning` if value is between `critical` + 1 and the `warning` threshold (default 11-20%)
  - `info` if value is between `warning` + 1 and the `info` threshold (default 21-30%)

#### Scenario: Offline device classification

- **WHEN** an entity has state `"unavailable"` or `"unknown"`
- **THEN** it SHALL load thresholds from state store key `home:thresholds:offline_hours` (default: `{"critical": 24, "warning": 1}`)
- **AND** it SHALL be classified as:
  - `critical` if `last_changed` is more than the `critical` threshold hours ago (default 24)
  - `warning` if `last_changed` is more than the `warning` threshold hours ago but less than `critical` (default 1-24h)

#### Scenario: Memory fact storage for issues

- **WHEN** one or more device issues are found
- **THEN** the job SHALL call `store_fact` for each issue with `predicate="device_issue"`, `permanence="volatile"`, and `importance` scaled by severity (critical=8.0, warning=6.5, info=5.0)
- **AND** tags SHALL include `"maintenance"` and the issue type (`"battery"`, `"offline"`)

#### Scenario: Memory fact storage for healthy fleet

- **WHEN** no device issues are found
- **THEN** the job SHALL call `store_fact` with `subject="device-fleet"`, `predicate="device_issue"`, `content` describing all-clear status with device count, `permanence="volatile"`, and `importance=3.0`

#### Scenario: Notification with issues

- **WHEN** one or more critical or warning issues are found
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL list issues grouped by severity (critical first, then warning)
- **AND** the message SHALL include device name, issue type, and value (e.g., "battery at 8%")

#### Scenario: Notification all-clear

- **WHEN** no critical or warning issues are found
- **THEN** the job SHALL send a brief all-clear Telegram notification with the total device count

#### Scenario: Job return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict with keys `devices_checked` (int), `issues_found` (int), `critical_count` (int), `warning_count` (int)

### Requirement: Environment Report Job

The `environment_report` job reads environmental sensors per area, compares against stored comfort preferences, and sends a room-by-room report.

#### Scenario: Area and sensor discovery

- **WHEN** the `environment_report` job runs
- **THEN** it SHALL query the `ha_entity_snapshot` table to discover all areas and their associated sensor entities
- **AND** it SHALL group sensors by area, filtering for temperature, humidity, CO2/air quality, and illuminance sensors
- **AND** if the snapshot table lacks area-registry data, it SHALL fall back to the HA REST API area registry endpoint

#### Scenario: Sensor reading collection

- **WHEN** sensors are grouped by area
- **THEN** the job SHALL read current state values for each sensor from the `ha_entity_snapshot` table
- **AND** it SHALL build a room-by-room map of readings (temperature, humidity, CO2, illuminance)

#### Scenario: Comfort preference retrieval

- **WHEN** readings are collected per area
- **THEN** the job SHALL query memory facts with `predicate="comfort_preference"` for each area name
- **AND** if no stored preference exists for an area, it SHALL load default healthy ranges from state store key `home:thresholds:comfort_defaults` (default: temperature 68-76 degF, humidity 30-60%, CO2 <1000 ppm)

#### Scenario: Deviation classification

- **WHEN** a reading is compared against its preference range
- **THEN** it SHALL load deviation thresholds from state store key `home:thresholds:comfort_deviation`
- **AND** it SHALL be classified as:
  - `ok` if within range
  - `minor` if within the `minor_temp_f` (default 2 degF) or `minor_humidity` (default 10% RH) of boundary
  - `moderate` if within the `moderate_temp_f` (default 5 degF) or `moderate_humidity` (default 20% RH) of boundary
  - `critical` if temperature below `critical_temp_low_f` (default 60 degF) or above `critical_temp_high_f` (default 85 degF), CO2 above `critical_co2_ppm` (default 1500 ppm), or humidity below `critical_humidity_low` (default 15%) or above `critical_humidity_high` (default 80%)

#### Scenario: Deviation memory storage

- **WHEN** one or more deviations of severity `moderate` or `critical` are detected
- **THEN** the job SHALL call `store_fact` for each with `predicate="comfort_deviation"`, `permanence="volatile"`, `importance=6.0` (moderate) or `importance=8.0` (critical)

#### Scenario: Report notification

- **WHEN** the report is composed
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL include a room-by-room summary showing readings and status (ok/deviation)
- **AND** deviations SHALL include actionable recommendations (e.g., "humidity low — consider running humidifier")
- **AND** at most 3 recommendations SHALL be included to avoid overwhelming the user

#### Scenario: Job return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict with keys `areas_checked` (int), `sensors_read` (int), `deviations_found` (int)

### Requirement: Energy Digest Job

The `energy_digest` job fetches weekly energy statistics, computes top consumers and trends vs. baselines, and sends a structured weekly digest.

#### Scenario: Energy sensor discovery

- **WHEN** the `energy_digest` job runs
- **THEN** it SHALL discover energy-related sensor entities by querying the `ha_entity_snapshot` table and filtering for entity IDs or friendly names containing `energy`, `power`, `kwh`, `consumption`, or `watt`

#### Scenario: Weekly statistics retrieval

- **WHEN** energy sensors are discovered
- **THEN** the job SHALL call the HA REST API `recorder/get_statistics_during_period` with `period="day"` for the past 7 days (this is a REST-only fallback — historical statistics are not available in the connector cache)
- **AND** it SHALL also call with `period="week"` for aggregate totals per device

#### Scenario: Baseline comparison

- **WHEN** weekly statistics are retrieved
- **THEN** the job SHALL query memory facts with `predicate="energy_baseline"` for overall and per-device baselines
- **AND** it SHALL compute percentage deviation from baseline for total consumption and top individual devices

#### Scenario: Anomaly detection

- **WHEN** energy thresholds are loaded from state store key `home:thresholds:energy` (default: `{"anomaly_pct": 20, "high_severity_pct": 100}`)
- **AND** a device's weekly consumption exceeds its baseline by the `anomaly_pct` threshold (default 20%) or more
- **THEN** it SHALL be flagged as an anomaly
- **AND** if consumption exceeds baseline by the `high_severity_pct` threshold (default 100%, i.e., 2x) or more, it SHALL be classified as high severity

#### Scenario: Top consumer ranking

- **WHEN** per-device weekly totals are computed
- **THEN** the job SHALL rank devices by total consumption and identify the top 5 consumers with their percentage share of total consumption

#### Scenario: Baseline memory update

- **WHEN** the digest is composed
- **THEN** the job SHALL call `store_fact` with `predicate="energy_baseline"`, `permanence="standard"`, containing the current week's total and top consumer breakdown
- **AND** if anomalies were detected, it SHALL call `store_fact` with `predicate="energy_spike"`, `permanence="volatile"`, for each anomalous device

#### Scenario: Digest notification

- **WHEN** the digest is composed
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL include: weekly total with trend vs. baseline, top 5 consumers with percentages, anomaly alerts (if any), and 2-3 actionable recommendations

#### Scenario: No energy sensors available

- **WHEN** no energy-related sensors are discovered
- **THEN** the job SHALL send a Telegram notification stating energy monitoring is not configured
- **AND** it SHALL return `{"error": "no_energy_sensors"}`

#### Scenario: Job return value

- **WHEN** the job completes successfully
- **THEN** it SHALL return a dict with keys `total_kwh` (float), `devices_ranked` (int), `anomalies_found` (int), `baseline_updated` (bool)

### Requirement: Entity State Access and HA REST Fallback for Jobs

Job handlers read current entity state from the connector-populated `ha_entity_snapshot` table. A short-lived HA REST client is available for historical statistics queries that the connector does not provide.

#### Scenario: Entity state from connector cache

- **WHEN** a home job handler needs current entity states (device health check, environment report, energy sensor discovery)
- **THEN** it SHALL query the `ha_entity_snapshot` table via the `asyncpg.Pool`
- **AND** it SHALL NOT call `GET /api/states` on the HA REST API for current state data

#### Scenario: REST client for historical statistics

- **WHEN** a home job handler needs historical data (e.g., `recorder/get_statistics_during_period` for the energy digest)
- **THEN** it SHALL resolve the HA URL and access token from the home butler's configuration and owner contact info
- **AND** it SHALL create a short-lived `httpx.AsyncClient` with `Authorization: Bearer <token>` header
- **AND** the client SHALL be closed after the job completes

#### Scenario: API error handling

- **WHEN** an HA REST API call returns a non-2xx status
- **THEN** the job SHALL log the error with status code and response body
- **AND** it SHALL continue processing remaining work (non-fatal for individual API calls)
- **AND** the error SHALL be reflected in the job return value

#### Scenario: HA unreachable for REST-only queries

- **WHEN** the HA REST API is unreachable (connection refused, timeout) and the job requires historical data
- **THEN** the job SHALL skip the historical data portion and note the omission in the notification
- **AND** it SHALL still process any work that can be completed from the entity snapshot cache alone

#### Scenario: Entity snapshot empty or stale

- **WHEN** the `ha_entity_snapshot` table is empty (connector never ran or was recently reset)
- **THEN** the job SHALL send a Telegram notification alerting the owner that Home Assistant entity data is unavailable
- **AND** it SHALL return `{"error": "no_entity_snapshot"}`
