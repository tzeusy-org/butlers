# Google Health Module

## Purpose

The Google Health module provides read-only MCP tools to the Health butler for querying wellness data (sleep, heart rate, HRV, SpO2, breathing rate, steps, active minutes, VO2 max) ingested by the Google Health connector. Tools query the Health butler's SPO memory fact store — they do NOT call the Google Health API directly. This separation of concerns keeps rate-limited external fetches in the connector and makes LLM tool calls deterministic and cheap.

The module is read-only in v1: there is no `log_sleep`, `log_weight`, or any mutation tool. The wearable device is authoritative for its own metrics.

## ADDED Requirements

### Requirement: Module Identity and Configuration

The Google Health module SHALL implement the `Module` base class with name `"google_health"`.

#### Scenario: Module registration

- **WHEN** a butler's `butler.toml` includes `[modules.google_health]`
- **THEN** the module SHALL be discovered and registered during butler startup
- **AND** it SHALL have `name = "google_health"` and `dependencies = []` at the `Module` class level (matching `module-spotify` precedent — no existing module spec uses a non-empty `dependencies` list, and the butler's module topological-init ordering, combined with the handler's runtime use of memory-module MCP tools, makes a declared dependency unnecessary and unverifiable against `src/butlers/modules/base.py` until that base class is extended)

#### Scenario: Default configuration

- **WHEN** `[modules.google_health]` is present with no additional keys
- **THEN** the module SHALL use default configuration
- **AND** all tools SHALL be registered

#### Scenario: No migrations

- **WHEN** the module's `migration_revisions()` is called
- **THEN** it SHALL return `None`
- **AND** the module SHALL contribute no new schema — all facts live in the existing health-butler SPO store

### Requirement: Credential Resolution via Google Account Registry

The module SHALL resolve the primary Google account's `entity_id` via the shared Google account registry and SHALL rely on the same refresh-token pipeline used by `connector-gmail` and `connector-google-calendar`. Per `about/heart-and-soul/security.md`, it MUST NOT use `CredentialStore.resolve()` or read refresh tokens from the process environment.

#### Scenario: Resolve primary Google account at startup

- **WHEN** `on_startup` is called
- **THEN** the module SHALL query `public.google_accounts` (via `google_account_registry.get_primary()` or the equivalent primitive used by existing Google-family connectors today — exact function name to be confirmed against `src/butlers/google_account_registry.py` at implementation time)
- **AND** SHALL cache the resolved `entity_id` for the lifetime of the module process so subsequent refresh-token lookups go through the shared `resolve_owner_entity_info()` pathway

#### Scenario: No primary account present

- **WHEN** `on_startup` is called and no primary Google account exists
- **THEN** the module SHALL still register all tools
- **AND** each tool SHALL return `"Google Health is not connected. Link a Google account with Google Health scopes via dashboard settings."` when invoked

### Requirement: Scope Availability Verification at Startup

The module SHALL verify that the primary Google account has the required Google Health scopes and SHALL operate in a degraded mode if not.

#### Scenario: Successful scope verification

- **WHEN** `on_startup` is called and the primary Google account has all three required Google Health scopes
- **THEN** the module SHALL register all tools normally
- **AND** the tools SHALL serve queries against the fact store

#### Scenario: Scopes missing at startup

- **WHEN** `on_startup` is called and any required Google Health scope is missing
- **THEN** the module SHALL still register all tools
- **AND** each tool SHALL return an actionable error when invoked: `"Google Health is not connected. Visit dashboard settings to grant the Google Health scopes."`
- **AND** the module SHALL NOT block the butler's startup

### Requirement: Sleep Query Tools

The module SHALL register tools for querying sleep data.

#### Scenario: `sleep_latest`

- **WHEN** `sleep_latest` is called with no parameters
- **THEN** the module SHALL query the Health butler's fact store for the most recent `sleep_session` fact for the owner entity
- **AND** SHALL return: `session_start`, `duration_minutes`, `efficiency`, `stages {deep, light, rem, wake}`, and the normalized summary text

#### Scenario: `sleep_history`

- **WHEN** `sleep_history` is called with optional `days` (default 7, max 90)
- **THEN** the module SHALL query sleep facts with `valid_at` within the last `days` days
- **AND** SHALL return a list of sessions in reverse chronological order with the same fields as `sleep_latest`
- **AND** SHALL include a summary: `avg_duration_minutes`, `avg_efficiency`, `avg_deep_minutes`, `avg_rem_minutes`

#### Scenario: Sleep data unavailable

- **WHEN** either sleep tool is called and no sleep facts exist in the queried range
- **THEN** the tool SHALL return an empty result with an explanation: `"No sleep data ingested yet. Google Health data appears after the device syncs — typically within 30 minutes of wearing the device overnight."`

### Requirement: Heart-Rate and HRV Query Tools

The module SHALL register tools for querying heart-rate data.

#### Scenario: `hr_history`

- **WHEN** `hr_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `resting_hr_daily` facts with `valid_at` within the range
- **AND** SHALL return daily resting HR values plus a `summary` with `min`, `max`, `avg`, and a linear trend slope

#### Scenario: `hrv_history`

- **WHEN** `hrv_history` is called with optional `days` (default 30, max 365)
- **THEN** the module SHALL query `hrv_daily` facts in the range
- **AND** SHALL return daily RMSSD values plus a `summary` with `avg_rmssd`, `coverage`, and trend direction

### Requirement: Oxygen and Breathing Query Tools

The module SHALL register tools for querying SpO2 and breathing rate data.

#### Scenario: `spo2_history`

- **WHEN** `spo2_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `spo2_daily` facts in the range
- **AND** SHALL return daily average SpO2 values

#### Scenario: `breathing_rate_history`

- **WHEN** `breathing_rate_history` is called with optional `days` (default 30)
- **THEN** the module SHALL query `breathing_rate_daily` facts in the range

### Requirement: Activity Query Tool

The module SHALL register a single activity summary tool that combines multiple daily metrics.

#### Scenario: `activity_summary`

- **WHEN** `activity_summary` is called with optional `days` (default 7)
- **THEN** the module SHALL query `steps_daily` and `active_minutes_daily` facts in the range
- **AND** SHALL return per-day: `steps`, `distance_km`, `floors`, `very_active_minutes`, `fairly_active_minutes`, `lightly_active_minutes`, `sedentary_minutes`
- **AND** SHALL return an aggregate summary: average steps, average active minutes, days meeting a 10,000-step threshold

### Requirement: VO2 Max Query Tool

The module SHALL register a tool for retrieving the latest VO2 max measurement.

#### Scenario: `vo2_max_latest`

- **WHEN** `vo2_max_latest` is called
- **THEN** the module SHALL return the most recent `vo2_max` fact with `range_low`, `range_high`, `midpoint`, and measurement date

### Requirement: Tools Query Facts, Not the API

All query tools SHALL query the Health butler's SPO fact store — not the Google Health API directly.

#### Scenario: Fact store query path

- **WHEN** any query tool runs
- **THEN** it SHALL use the memory module's `memory_search` primitives with `scope='health'` and the appropriate predicate filter
- **AND** it SHALL NOT issue any HTTP call to `health.googleapis.com`
- **AND** this separation ensures LLM tool calls are deterministic, offline-safe, and decoupled from Google Health rate limits

#### Scenario: Connector outage resilience

- **WHEN** the Google Health connector is stopped or degraded
- **THEN** query tools SHALL continue to return data from previously-ingested facts
- **AND** responses SHALL note the most-recent observation timestamp so the caller can reason about staleness

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- RFC 0002 (MCP tool surface and modules)
- `module-memory` (fact store primitives)
- `butler-health` (module host and SPO taxonomy)
- Sibling: `module-spotify` (read-only query-tool pattern against ingested data)
