## ADDED Requirements

### Requirement: Atmosphere Current Conditions Endpoint

`GET /api/home/atmosphere/current` SHALL surface the latest weather/AQI/
pollen reading produced by the `atmosphere_feed_refresh` job, and SHALL be
honest about configuration and staleness per the CLAUDE.md "Degraded-Mode
Response Envelope" convention.

#### Scenario: Not configured

- **WHEN** `GET /api/home/atmosphere/current` is called and
  `public.atmosphere_feed_status.configured` is `false` (or no status row
  exists yet)
- **THEN** the response SHALL have `configured = false`
- **AND** all weather/AQI/pollen fields SHALL be `null`
- **AND** `stale` and `source_error` SHALL both be `false` — an unconfigured
  feed is a legitimate absence, not a degraded state

#### Scenario: Healthy current conditions

- **WHEN** `GET /api/home/atmosphere/current` is called, the feed is
  configured, and the latest reading is fresh (within the staleness
  threshold)
- **THEN** the response SHALL have `configured = true`, `stale = false`,
  `source_error = false`
- **AND** it SHALL include the latest reading's temperature, apparent
  temperature, humidity, precipitation, weather code, wind speed, AQI
  (US and European), PM2.5/PM10, pollen fields, and `pollen_available`

#### Scenario: Degraded — stale or fetch failing

- **WHEN** the feed is configured but either no successful fetch has
  occurred within the staleness threshold, or the last fetch attempt failed
- **THEN** the response SHALL set `stale = true` and/or `source_error =
  true` as applicable
- **AND** the last-known-good reading values (if any) SHALL still be
  returned alongside the flags rather than zeroed out — never render a
  degraded source as a truthful all-clear or fabricated-zero result
- **AND** `last_error` SHALL surface the most recent failure description
  when `source_error` is `true`

### Requirement: Atmosphere Location Provisioning Endpoint

`PATCH /api/home/atmosphere/location` SHALL be the owner-provisioning
endpoint for the home location the atmosphere feed polls.

#### Scenario: Set home location

- **WHEN** `PATCH /api/home/atmosphere/location` is called with a valid
  `latitude` (-90..90) and `longitude` (-180..180)
- **THEN** the value SHALL be stored as `"lat,lon"` in the owner's
  `entity_info` under type `home_coordinates` via `upsert_owner_entity_info`
- **AND** the response SHALL echo the stored `latitude`/`longitude`
- **AND** the next scheduled `atmosphere_feed_refresh` run SHALL pick up the
  new location — this endpoint does not trigger a synchronous fetch

#### Scenario: Out-of-range coordinates rejected

- **WHEN** `PATCH /api/home/atmosphere/location` is called with a latitude
  or longitude outside its valid range
- **THEN** the request SHALL be rejected with `422`

#### Scenario: No owner entity

- **WHEN** `PATCH /api/home/atmosphere/location` is called but no owner
  entity exists to attach the `entity_info` row to
- **THEN** the response SHALL be `503`

### Requirement: Owner Atmosphere Location Panel

The dashboard SHALL make Home atmosphere location configuration discoverable
in the Home butler's existing Devices tab, using only the current-conditions
and location-provisioning endpoints.

#### Scenario: Unconfigured or configured location

- **WHEN** the panel loads `GET /api/home/atmosphere/current`
- **THEN** it SHALL render an explicit configured or unconfigured state
- **AND** it SHALL provide labeled, keyboard-operable native numeric inputs
  for latitude (-90..90) and longitude (-180..180)
- **AND** it SHALL hydrate the controlled inputs from configured coordinates
  without overwriting an owner's in-progress edit

#### Scenario: Save location without claiming a synchronous refresh

- **WHEN** the owner submits valid coordinates
- **THEN** the panel SHALL send exactly those values to
  `PATCH /api/home/atmosphere/location`
- **AND** it SHALL invalidate and refetch the current-conditions query
- **AND** success copy SHALL state that the next scheduled refresh picks up
  the change, without claiming that a refresh has completed

#### Scenario: Honest degraded and error states

- **WHEN** the current-conditions response is loading, stale, source-failing,
  or unavailable, or the save request is pending or fails
- **THEN** the panel SHALL present a semantically announced, actionable state
- **AND** client-side range errors SHALL prevent a request
- **AND** 422, 503, and network failures SHALL retain entered coordinates and
  identify a useful recovery path
