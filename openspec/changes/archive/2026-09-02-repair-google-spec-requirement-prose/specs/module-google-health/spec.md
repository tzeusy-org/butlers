## MODIFIED Requirements

### Requirement: Scope Availability Verification at Startup

The module SHALL verify the required Google Health scopes at startup. It SHALL
register its tools either way, and when scopes are missing each tool SHALL
return an actionable not-connected message rather than blocking the butler's
startup.

#### Scenario: Successful scope verification

- **WHEN** `on_startup` is called and the primary Google account has all three required Google Health scopes
- **THEN** all tools SHALL serve queries against the fact store

#### Scenario: Scopes missing at startup

- **WHEN** `on_startup` is called and any required Google Health scope is missing
- **THEN** the module SHALL still register all tools
- **AND** each tool SHALL return `"Google Health is not connected. Visit dashboard settings to grant the Google Health scopes."`
- **AND** the module SHALL NOT block the butler's startup

### Requirement: Sleep Query Tools

The module SHALL expose sleep query tools over the ingested fact store,
returning the most recent session, a bounded history with aggregates, and an
actionable empty result when no sleep facts exist.

#### Scenario: `sleep_latest`

- **WHEN** `sleep_latest` is called
- **THEN** the module SHALL query for the most recent `sleep_session` fact for the owner entity
- **AND** SHALL return: `session_start`, `duration_minutes`, `efficiency`, `stages {deep, light, rem, wake}`, and normalized summary text

#### Scenario: `sleep_history`

- **WHEN** `sleep_history` is called with optional `days` (default 7, max 90)
- **THEN** the module SHALL return sessions in reverse chronological order with the same fields as `sleep_latest`
- **AND** SHALL include a summary: `avg_duration_minutes`, `avg_efficiency`, `avg_deep_minutes`, `avg_rem_minutes`

#### Scenario: Sleep data unavailable

- **WHEN** either sleep tool is called and no sleep facts exist
- **THEN** the tool SHALL return an empty result with explanation: `"No sleep data ingested yet. Google Health data appears after the device syncs — typically within 30 minutes of wearing the device overnight."`

### Requirement: Heart-Rate and HRV Query Tools

The module SHALL expose bounded resting heart-rate and HRV history tools, each
returning daily values alongside a summary.

#### Scenario: `hr_history`

- **WHEN** `hr_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `measurement_resting_hr` facts and return daily values plus a `summary` with `min`, `max`, `avg`, and linear trend slope

#### Scenario: `hrv_history`

- **WHEN** `hrv_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `measurement_hrv` facts and return daily RMSSD values plus a `summary` with `avg_rmssd`, `coverage`, and trend direction

### Requirement: Oxygen and Breathing Query Tools

The module SHALL expose bounded SpO2 and breathing-rate history tools over the
corresponding measurement facts.

#### Scenario: `spo2_history`

- **WHEN** `spo2_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `measurement_spo2` facts and return daily average SpO2 values

#### Scenario: `breathing_rate_history`

- **WHEN** `breathing_rate_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `measurement_breathing_rate` facts in the range

### Requirement: Activity Query Tool

The module SHALL expose a bounded activity summary tool returning per-day step
and active-minute detail alongside an aggregate summary.

#### Scenario: `activity_summary`

- **WHEN** `activity_summary` is called with optional `days` (default 7)
- **THEN** the module SHALL query `measurement_steps` and `measurement_active_minutes` facts
- **AND** SHALL return per-day: `steps`, `distance_km`, `floors`, `very_active_minutes`, `fairly_active_minutes`, `lightly_active_minutes`, `sedentary_minutes`
- **AND** aggregate summary: average steps, average active minutes, days meeting a 10,000-step threshold

### Requirement: VO2 Max Query Tool

The module SHALL expose a tool returning the most recent VO2 max measurement
with its range and measurement date.

#### Scenario: `vo2_max_latest`

- **WHEN** `vo2_max_latest` is called
- **THEN** the module SHALL return the most recent `measurement_vo2_max` fact with `range_low`, `range_high`, `midpoint`, and measurement date
