## MODIFIED Requirements

### Requirement: Energy Digest Job

The `energy_digest` job SHALL fetch weekly energy statistics, compute top consumers, judge each device and the house total against the owner's own week-bucket personal baselines (`butlers.core.baselines`, `bucket = week`, `min_n = 4`), and send a structured weekly digest.

ID: REQ-home-deterministic-jobs-006

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
- **AND** it SHALL NOT score the week or write any baseline, and SHALL NOT judge any device as unusual

#### Scenario: Baseline comparison

- **WHEN** weekly statistics are retrieved for a complete (non-partial) run
- **THEN** the job SHALL score the house total (`energy.total`) and each device (`energy.device:<entity_id>`) against the median and MAD band of its preceding complete weeks, taken from Home Assistant's hourly long-term statistics summed over trailing 7-day windows, and persist each band in `metric_baselines`
- **AND** a week whose hourly buckets are materially incomplete SHALL be left out of the history rather than counted as a quiet week
- **AND** a rerun for the same week SHALL leave every stored band unchanged

#### Scenario: Anomaly detection

- **WHEN** a device's weekly consumption is above its own usual range (a ready band, scored above)
- **THEN** it SHALL be flagged as an anomaly carrying the usual range and the weeks it rests on (for example "31 kWh, usual 18-24")
- **AND** consumption of at least twice the band's centre SHALL be classified as high severity
- **AND** one anomalous week SHALL NOT become the next week's yardstick: a week inside an open deviation episode is excluded from later bands

#### Scenario: Thin history is not an anomaly

- **WHEN** a device or the house total has fewer than four complete weeks of history
- **THEN** it SHALL be listed as "not enough weeks yet" and SHALL NOT be flagged as an anomaly
- **AND** a device seen for the first time SHALL start with no band and fill over four weeks

#### Scenario: Top consumer ranking

- **WHEN** per-device weekly totals are computed
- **THEN** the job SHALL rank devices by total consumption and identify the top 5 consumers with their percentage share of total consumption

#### Scenario: Baseline memory update

- **WHEN** the job runs
- **THEN** it SHALL NOT write or read any `energy_baseline` memory fact
- **AND** it SHALL retract (`validity = 'retracted'`, rows kept) the `energy_baseline` facts it wrote earlier, identified by `system` authority, `home` source and its own tags, leaving any owner-authored `energy_baseline` fact untouched
- **AND** if anomalies were detected, it SHALL call `store_fact` with `predicate="energy_spike"`, `permanence="volatile"`, for each anomalous device

#### Scenario: Digest notification

- **WHEN** the digest is composed
- **THEN** the job SHALL send a Telegram notification via the notify helper with `intent="send"`
- **AND** the message SHALL include: weekly total with trend vs. baseline, top 5 consumers with percentages, anomaly alerts (if any), and 2-3 actionable recommendations
- **AND** the message SHALL describe the week as within the normal range only when the house total was scored `within` a ready baseline band, and SHALL NOT when the house total has thin history, sits above or below its band, or any device is flagged

#### Scenario: No energy sensors available

- **WHEN** no energy-related sensors are discovered
- **THEN** the job SHALL send a Telegram notification stating energy monitoring is not configured
- **AND** it SHALL return `{"error": "no_energy_sensors"}`

#### Scenario: Job return value

- **WHEN** the job completes successfully with cumulative-energy statistics for every discovered sensor
- **THEN** it SHALL return a dict with keys `total_kwh` (float), `devices_ranked` (int), `anomalies_found` (int), `baseline_updated` (bool, true when a stored band changed)
- **WHEN** the job completes with both supported and unsupported sensor series
- **THEN** it SHALL return a dict with `partial=true`, `omitted_sensors` (list of entity IDs), `devices_ranked` (int), `anomalies_found` (int), and `baseline_updated=false`, without a `total_kwh` key
