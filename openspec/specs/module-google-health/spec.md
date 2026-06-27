# Google Health Module

## Purpose

The Google Health module provides read-only MCP tools to the Health butler for querying wellness data (sleep, heart rate, HRV, SpO2, breathing rate, steps, active minutes, VO2 max) ingested by the Google Health connector. Tools query the Health butler's SPO memory fact store — they do NOT call the Google Health API directly.

## ADDED Requirements

### Requirement: Module Identity and Configuration

The Google Health module SHALL implement the `Module` base class with name `"google_health"`.

#### Scenario: Module registration

- **WHEN** a butler's `butler.toml` includes `[modules.google_health]`
- **THEN** the module SHALL be discovered and registered during butler startup
- **AND** it SHALL have `name = "google_health"` and `dependencies = []`

#### Scenario: Default configuration

- **WHEN** `[modules.google_health]` is present with no additional keys
- **THEN** all tools SHALL be registered with default configuration

#### Scenario: No migrations

- **WHEN** the module's `migration_revisions()` is called
- **THEN** it SHALL return `None` — all facts live in the existing health-butler SPO store

### Requirement: Credential Resolution via Google Account Registry

The module SHALL resolve the primary Google account's `entity_id` via the shared Google account registry and rely on the same refresh-token pipeline used by `connector-gmail` and `connector-google-calendar`. It MUST NOT use `CredentialStore.resolve()` or read refresh tokens from the process environment.

#### Scenario: Resolve primary Google account at startup

- **WHEN** `on_startup` is called
- **THEN** the module SHALL query `public.google_accounts` (via `google_account_registry.get_primary()` or equivalent)
- **AND** SHALL cache the resolved `entity_id` for the lifetime of the module process

#### Scenario: No primary account present

- **WHEN** `on_startup` is called and no primary Google account exists
- **THEN** the module SHALL still register all tools
- **AND** each tool SHALL return `"Google Health is not connected. Link a Google account with Google Health scopes via dashboard settings."` when invoked

### Requirement: Scope Availability Verification at Startup

#### Scenario: Successful scope verification

- **WHEN** `on_startup` is called and the primary Google account has all three required Google Health scopes
- **THEN** all tools SHALL serve queries against the fact store

#### Scenario: Scopes missing at startup

- **WHEN** `on_startup` is called and any required Google Health scope is missing
- **THEN** the module SHALL still register all tools
- **AND** each tool SHALL return `"Google Health is not connected. Visit dashboard settings to grant the Google Health scopes."`
- **AND** the module SHALL NOT block the butler's startup

### Requirement: Sleep Query Tools

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

#### Scenario: `hr_history`

- **WHEN** `hr_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `measurement_resting_hr` facts and return daily values plus a `summary` with `min`, `max`, `avg`, and linear trend slope

#### Scenario: `hrv_history`

- **WHEN** `hrv_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `measurement_hrv` facts and return daily RMSSD values plus a `summary` with `avg_rmssd`, `coverage`, and trend direction

### Requirement: Oxygen and Breathing Query Tools

#### Scenario: `spo2_history`

- **WHEN** `spo2_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `measurement_spo2` facts and return daily average SpO2 values

#### Scenario: `breathing_rate_history`

- **WHEN** `breathing_rate_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `measurement_breathing_rate` facts in the range

### Requirement: Activity Query Tool

#### Scenario: `activity_summary`

- **WHEN** `activity_summary` is called with optional `days` (default 7)
- **THEN** the module SHALL query `measurement_steps` and `measurement_active_minutes` facts
- **AND** SHALL return per-day: `steps`, `distance_km`, `floors`, `very_active_minutes`, `fairly_active_minutes`, `lightly_active_minutes`, `sedentary_minutes`
- **AND** aggregate summary: average steps, average active minutes, days meeting a 10,000-step threshold

### Requirement: VO2 Max Query Tool

#### Scenario: `vo2_max_latest`

- **WHEN** `vo2_max_latest` is called
- **THEN** the module SHALL return the most recent `measurement_vo2_max` fact with `range_low`, `range_high`, `midpoint`, and measurement date

### Requirement: Tools Query Facts, Not the API

All query tools SHALL query the Health butler's SPO fact store — not the Google Health API directly.

#### Scenario: Fact store query path

- **WHEN** any query tool runs
- **THEN** it SHALL return a structured directive (query string, `scope='health'`, the appropriate predicate filter, and an `instruction` field) telling the runtime instance to call `memory_search` against the Health butler's fact store
- **AND** the tool itself SHALL NOT call `memory_search` directly (the module holds no embedding engine; `memory_search` is invoked by the LLM via the directive) and SHALL NOT issue any HTTP call to `health.googleapis.com`

#### Scenario: Connector outage resilience

- **WHEN** the Google Health connector is stopped or degraded
- **THEN** query tools SHALL continue to return data from previously-ingested facts
- **AND** responses SHALL note the most-recent observation timestamp
