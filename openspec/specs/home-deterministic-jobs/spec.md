# Home Deterministic Jobs

## Purpose

Deterministic Python job handlers for the Home butler's scheduled monitoring tasks. These handlers replace prompt-based LLM dispatch with threshold-based classification, memory storage, and Telegram notifications — eliminating LLM costs for formulaic monitoring work. Jobs read current entity state from the connector-populated `ha_entity_snapshot` table and load monitoring thresholds from the state store (`home:thresholds:*`), falling back to direct HA WebSocket API calls only for historical statistics queries.

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

### Requirement: Notification Message Formatting

All home job notifications SHALL be composed in Markdown, because the delivery chain
(`_send_notify` -> `deliver()` -> `telegram_send_message`) HTML-escapes the message
and then converts Markdown to Telegram HTML.

#### Scenario: Markdown, not HTML

- **WHEN** any home job composes an owner-facing notification
- **THEN** it SHALL emit Markdown (`**bold**`, `*italic*`, `` `code` ``)
- **AND** it SHALL NOT emit raw HTML tags, which reach the owner as literal `&lt;b&gt;`
- **AND** it SHALL NOT HTML-escape interpolated values itself, since the delivery chain escapes them and pre-escaping double-escapes `&` and `<`

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
  - Re-alert: `{"hours": 24}`
  - Comfort defaults: `{"temp_min_c": 20, "temp_max_c": 27.5, "humidity_min": 30, "humidity_max": 78, "co2_max_ppm": 1000}`
  - Comfort deviation: `{"minor_temp_c": 1, "moderate_temp_c": 3, "minor_humidity": 10, "moderate_humidity": 20, "critical_temp_low_c": 15.5, "critical_temp_high_c": 32, "critical_co2_ppm": 1500, "critical_humidity_low": 15, "critical_humidity_high": 88}`
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
- **AND** it SHALL identify entities with state `"unavailable"` or `"unknown"` as offline, EXCLUDING entities whose HA domain (`button`, `conversation`, `infrared`, `radio_frequency`, `tts`) or entity_id pattern (a Zigbee2MQTT dimmer "action" sensor, e.g. `sensor.*_action_brightness_delta`) normally rests at `"unknown"` between interactions and so is never a real fault
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

#### Scenario: Repeat-alert suppression for unresolved issues

- **WHEN** the job classifies the current set of battery/offline issues
- **THEN** it SHALL load a per-issue last-alerted map from state store key `home:health_check:last_alerted` and a re-alert window from `home:thresholds:realert` (default 24 hours)
- **AND** an issue (keyed by `"{issue_type}:{entity_id}"`) SHALL be included in this run's memory-fact writes and notification only if it has never been alerted, or its last alert is at least the re-alert window ago
- **AND** issues suppressed this way SHALL still count toward the job's returned `issues_found`/`critical_count`/`warning_count`, which always reflect the true current state
- **AND** after the run, the state store key SHALL be updated to retain only issues present in the current run — a resolved issue's timer is dropped so it alerts immediately if it recurs
- **AND** if every current issue is suppressed this run (nothing new to report), the job SHALL skip sending a notification and skip writing memory facts for that run

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
- **AND** it SHALL group sensors by area, classifying each sensor by its Home Assistant `device_class` where present and by entity-id keywords only as a fallback, so that a sensor is only ever reported under the quantity it actually measures (a `pm25` or `volatile_organic_compounds` sensor SHALL NOT be reported as CO2)
- **AND** it SHALL exclude sensors that measure hardware rather than room comfort (disk, CPU, battery, appliance temperatures)
- **AND** since `/api/states` carries no area registry, it SHALL resolve an area from the entity `area_id`/`area`/`room` attributes where present and otherwise from a room name in the entity id or friendly name
- **AND** it SHALL skip any sensor whose area cannot be resolved rather than pooling such sensors into a single "unknown" area, since one arbitrary sensor would then be reported as if it spoke for the whole house

#### Scenario: Sensor reading collection

- **WHEN** sensors are grouped by area
- **THEN** the job SHALL read current state values for each sensor from the `ha_entity_snapshot` table
- **AND** it SHALL normalise every temperature reading to Celsius using the entity's `unit_of_measurement`, skipping readings whose unit is present but is not a temperature unit
- **AND** it SHALL build a room-by-room map of readings (temperature, humidity, CO2, illuminance)

#### Scenario: Comfort preference retrieval

- **WHEN** readings are collected per area
- **THEN** the job SHALL query memory facts with `predicate="comfort_preference"` for each area name
- **AND** if no stored preference exists for an area, it SHALL load default healthy ranges from state store key `home:thresholds:comfort_defaults` (default: temperature 20-27.5 degC, humidity 30-78%, CO2 <1000 ppm, tuned for a tropical climate rather than the US-centric range these were ported from)

#### Scenario: Deviation classification

- **WHEN** a reading is compared against its preference range
- **THEN** it SHALL load deviation thresholds from state store key `home:thresholds:comfort_deviation`
- **AND** it SHALL be classified as:
  - `ok` if within range
  - `minor` if within the `minor_temp_c` (default 1 degC) or `minor_humidity` (default 10% relative humidity) of boundary
  - `moderate` if within the `moderate_temp_c` (default 3 degC) or `moderate_humidity` (default 20% relative humidity) of boundary
  - `critical` if temperature below `critical_temp_low_c` (default 15.5 degC) or above `critical_temp_high_c` (default 32 degC), CO2 above `critical_co2_ppm` (default 1500 ppm), or humidity below `critical_humidity_low` (default 15%) or above `critical_humidity_high` (default 88%)

#### Scenario: Deviation memory storage

- **WHEN** one or more deviations of severity `moderate` or `critical` are detected
- **THEN** the job SHALL call `store_fact` for each with `predicate="comfort_deviation"`, `permanence="volatile"`, `importance=6.0` (moderate) or `importance=8.0` (critical)

#### Scenario: Report notification

- **WHEN** the report is composed
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL express temperatures in Celsius and SHALL spell out units in plain language rather than abbreviations such as "RH"
- **AND** the message SHALL include a room-by-room summary showing readings and status (ok/deviation)
- **AND** deviations SHALL include actionable recommendations (e.g., "humidity low — consider running humidifier")
- **AND** at most 3 recommendations SHALL be included to avoid overwhelming the user
- **AND** a recommendation SHALL be emitted only for a deviation that is new or has changed severity since the previous run, compared against state store key `home:environment:last_deviations`, so a standing condition (persistent tropical humidity, say) is stated once rather than every day
- **AND** readings and their status icons SHALL still be shown for suppressed deviations, and the report SHALL state how many standing conditions were suppressed, so silence reads as "unchanged" rather than "not checked"
- **AND** every current deviation SHALL be carried into `home:environment:last_deviations`, including suppressed ones, so a persistent condition stays suppressed rather than re-firing on alternate runs

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
- **THEN** the job SHALL call the HA WebSocket API command `recorder/statistics_during_period` with `period="day"` for the past 7 days (historical statistics are not available in the connector cache)
- **AND** it SHALL also call over an hour-aligned 168-hour window with `period="hour"` and sum the returned per-period `change` values for aggregate consumption per device
- **AND** a device series SHALL be supported only when every hourly bucket contains a finite numeric `change`; an explicit `change=0` SHALL remain valid zero consumption
- **AND** the job SHALL NOT substitute missing, non-numeric, or non-finite `change` values with zero or integrate `mean` power values as energy

#### Scenario: No cumulative-energy statistics available

- **WHEN** every discovered sensor lacks a complete finite hourly `change` series
- **THEN** the job SHALL notify the owner that cumulative-energy statistics are unavailable and recommend configuring a Home Assistant energy helper for power-only sensors
- **AND** it SHALL return `{"error": "no_cumulative_energy_statistics", "unsupported_sensors": [...]}` with the omitted entity IDs
- **AND** it SHALL NOT compute or store an energy baseline

#### Scenario: Partial cumulative-energy statistics

- **WHEN** at least one discovered sensor has a complete finite hourly `change` series and at least one does not
- **THEN** the job SHALL report only the supported device series and identify the omitted sensors visibly
- **AND** it SHALL NOT present a whole-home total, whole-home trend, savings claim, or percentage share
- **AND** it SHALL NOT read or store an overall energy baseline, though supported per-device baselines and anomalies MAY still be processed

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

- **WHEN** a complete digest is composed with cumulative-energy statistics for every discovered sensor
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

- **WHEN** the job completes successfully with cumulative-energy statistics for every discovered sensor
- **THEN** it SHALL return a dict with keys `total_kwh` (float), `devices_ranked` (int), `anomalies_found` (int), `baseline_updated` (bool)
- **WHEN** the job completes with both supported and unsupported sensor series
- **THEN** it SHALL return a dict with `partial=true`, `omitted_sensors` (list of entity IDs), `devices_ranked` (int), `anomalies_found` (int), and `baseline_updated=false`, without a `total_kwh` key

### Requirement: Entity State Access and HA Statistics Fallback for Jobs

Job handlers read current entity state from the connector-populated `ha_entity_snapshot` table. A short-lived HA WebSocket client is available for historical statistics queries that the connector does not provide.

#### Scenario: Entity state from connector cache

- **WHEN** a home job handler needs current entity states (device health check, environment report, energy sensor discovery)
- **THEN** it SHALL query the `ha_entity_snapshot` table via the `asyncpg.Pool`
- **AND** it SHALL NOT call `GET /api/states` on the HA REST API for current state data

#### Scenario: WebSocket client for historical statistics

- **WHEN** a home job handler needs historical data (e.g., `recorder/statistics_during_period` for the energy digest)
- **THEN** it SHALL resolve the HA URL and access token from the home butler's configuration and owner contact info
- **AND** it SHALL create a short-lived WebSocket connection to `/api/websocket` and authenticate with the access token
- **AND** the client SHALL be closed after the job completes

#### Scenario: Statistics command error handling

- **WHEN** an HA statistics command returns an unsuccessful result
- **THEN** the job SHALL log an allowlisted error code and SHALL omit the server-controlled error message
- **AND** it SHALL continue processing remaining work without the rejected statistics

#### Scenario: HA unreachable for historical queries

- **WHEN** the HA WebSocket API is unreachable, rejects authentication, or times out and the job requires historical data
- **THEN** the job SHALL skip the historical data portion and note the omission in the notification
- **AND** it SHALL still process any work that can be completed from the entity snapshot cache alone

#### Scenario: Entity snapshot empty or stale

- **WHEN** the `ha_entity_snapshot` table is empty (connector never ran or was recently reset)
- **THEN** the job SHALL send a Telegram notification alerting the owner that Home Assistant entity data is unavailable
- **AND** it SHALL return `{"error": "no_entity_snapshot"}`
