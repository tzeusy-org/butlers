## ADDED Requirements

### Requirement: Atmosphere Feed Refresh Job

The `atmosphere_feed_refresh` job SHALL be a zero-LLM, deterministic context
producer that keeps the shared weather/AQI/pollen feed warm for the owner's
configured home location. Unlike a message-ingestion connector, there is no
external "message" to classify or route — it SHALL be scheduled directly on
the Home butler (`dispatch_mode = "job"`, cron `*/30 * * * *`).

#### Scenario: Job handler signature

- **WHEN** the `atmosphere_feed_refresh` job handler is invoked by the
  scheduler
- **THEN** it SHALL accept `pool: asyncpg.Pool` and
  `job_args: dict[str, Any] | None` as parameters
- **AND** it SHALL return `dict[str, Any]` describing the outcome

#### Scenario: Job registry registration

- **WHEN** the daemon initializes `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
- **THEN** the `"home"` entry SHALL include `atmosphere_feed_refresh`
  alongside the existing deterministic handlers

#### Scenario: Home location resolution

- **WHEN** the job runs
- **THEN** it SHALL resolve the home location from
  `ATMOSPHERE_HOME_LAT`/`ATMOSPHERE_HOME_LON` environment variables first
- **AND** fall back to the owner's `entity_info` row of type
  `home_coordinates` (stored as `"lat,lon"`) when the env vars are absent or
  unparseable

#### Scenario: Not configured — honest skip, not an error

- **WHEN** neither the env vars nor `entity_info` resolve a usable home
  location
- **THEN** the job SHALL NOT attempt an upstream fetch
- **AND** it SHALL upsert `public.atmosphere_feed_status` with
  `configured = false`
- **AND** it SHALL return `{"skipped": true, "reason": "not_configured"}`
  rather than raising or logging an error

#### Scenario: Successful fetch stores a reading and resets failure state

- **WHEN** the home location is configured and the Open-Meteo forecast and
  air-quality requests both succeed
- **THEN** the job SHALL insert one row into `public.atmosphere_readings`
  with the parsed temperature, humidity, precipitation, weather code, wind
  speed, AQI (US and European), PM2.5/PM10, and pollen fields
- **AND** it SHALL upsert `public.atmosphere_feed_status` with
  `configured = true`, `last_success_at` set to now, `last_error` cleared,
  and `consecutive_failures` reset to `0`

#### Scenario: Fetch failure degrades honestly without crashing

- **WHEN** the home location is configured but the upstream request fails
  (transport error or non-2xx status)
- **THEN** the job SHALL NOT insert a row into `atmosphere_readings`
- **AND** it SHALL upsert `atmosphere_feed_status` with `last_error` set to
  the failure description and `consecutive_failures` incremented
- **AND** the job function SHALL NOT raise — it returns a result dict
  describing the failure so the scheduler records a normal (non-crashed) run

#### Scenario: Pollen absence is classified, not conflated with failure

- **WHEN** Open-Meteo's air-quality response returns `null` for every pollen
  field (the location is outside Europe, where Open-Meteo does not forecast
  pollen)
- **THEN** the stored reading SHALL have `pollen_available = false` and all
  pollen columns `NULL`
- **AND** this SHALL NOT increment `consecutive_failures` or set
  `last_error` — it is a legitimately-absent field for that location, not a
  fetch failure
