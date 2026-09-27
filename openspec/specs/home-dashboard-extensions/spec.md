# Home Dashboard Extensions

## Purpose

Dashboard API endpoints for device inventory, energy consumption time-series, and maintenance calendar, surfacing data produced by the Home butler's deterministic monitoring jobs.

## Requirements

### Requirement: Device Inventory Endpoint

The implementation SHALL provide the behavior described by this requirement.
A paginated endpoint listing all known HA devices with their current state, area, and health status.

#### Scenario: List all devices

- **WHEN** `GET /api/home/devices` is called with no filters
- **THEN** it SHALL return a paginated list of device entries from `ha_entity_snapshot` table
- **AND** each entry SHALL include `entity_id`, `state`, `friendly_name` (from attributes), `area_name` (from entity registry cache or attributes), `domain` (extracted from entity_id prefix), `last_updated`, and `health_status` (computed: `"healthy"` if state is not `unavailable`/`unknown`, `"offline"` otherwise)

#### Scenario: Filter by domain

- **WHEN** `GET /api/home/devices?domain=light` is called
- **THEN** only entities with `entity_id` starting with `light.` SHALL be returned

#### Scenario: Filter by area

- **WHEN** `GET /api/home/devices?area=kitchen` is called
- **THEN** only entities whose area matches the given area name SHALL be returned

#### Scenario: Filter by health status

- **WHEN** `GET /api/home/devices?health=offline` is called
- **THEN** only entities with state `unavailable` or `unknown` SHALL be returned

#### Scenario: Pagination

- **WHEN** `GET /api/home/devices?page=2&page_size=50` is called
- **THEN** the response SHALL be a page-based paginated wrapper (`DeviceInventoryResponse`) with `meta` (`DevicePaginationMeta`) containing `page`, `page_size`, `total_count`, `total_pages`, and `ha_source_available`

#### Scenario: HA source unmeasurable during an outage

- **WHEN** `GET /api/home/devices` is called while `ha_source_health` shows the
  `home_assistant` source is not `'healthy'` (an active outage) or has no
  recent recorded contact
- **THEN** the endpoint SHALL still return its (possibly stale) device rows
  rather than failing the request
- **AND** `meta.ha_source_available` SHALL be `false`, so a caller cannot
  treat the returned devices as a truthful current-state read

### Requirement: Energy Consumption Endpoint

The implementation SHALL provide the behavior described by this requirement.
An endpoint returning energy consumption time-series data for dashboard charts.

#### Scenario: Daily energy consumption

- **WHEN** `GET /api/home/energy?period=day&start=2026-03-01&end=2026-03-25` is called
- **THEN** it SHALL use the shared Home Assistant statistics client to send the WebSocket command `recorder/statistics_during_period` with `period="day"` and `types=["change"]`
- **AND** it SHALL return a list of daily data points with `timestamp`, `total_kwh`, and per-device breakdown (`devices`) computed from per-period `change` values rather than cumulative `sum` values

#### Scenario: Hourly energy consumption

- **WHEN** `GET /api/home/energy?period=hour&start=2026-03-24&end=2026-03-25` is called
- **THEN** it SHALL return hourly data points for the requested range

#### Scenario: Default period

- **WHEN** `GET /api/home/energy` is called with no `period` parameter
- **THEN** the period SHALL default to `day`

#### Scenario: Default date range

- **WHEN** `GET /api/home/energy` is called with no `start` or `end` parameters
- **THEN** `start` SHALL default to 7 days ago and `end` SHALL default to now

#### Scenario: Top consumers summary

- **WHEN** `GET /api/home/energy/top-consumers?start=2026-03-18&end=2026-03-25` is called
- **THEN** it SHALL return the top 10 energy-consuming devices for the period
- **AND** each entry SHALL include `entity_id`, `friendly_name`, `total_kwh`, and `percentage` of total consumption
- **AND** totals SHALL be computed by summing per-period `change` values rather than cumulative `sum` values

#### Scenario: Partial cumulative-energy statistics

- **WHEN** at least one discovered sensor has a non-empty series whose every bucket contains a finite numeric `change` and at least one discovered sensor does not
- **THEN** both energy endpoints SHALL omit each unsupported sensor instead of substituting zero
- **AND** an explicit numeric `change=0` SHALL remain valid zero consumption
- **AND** the response list body SHALL retain its existing schema
- **AND** the response SHALL include `X-Butlers-Energy-Data-Status: partial` and `X-Butlers-Omitted-Sensors: <count>` headers

#### Scenario: No cumulative-energy statistics

- **WHEN** every discovered sensor lacks a complete finite numeric `change` series
- **THEN** both energy endpoints SHALL return HTTP 503 indicating that cumulative-energy change data is unavailable
- **AND** they SHALL NOT fabricate zero consumption

#### Scenario: HA unavailable fallback

- **WHEN** the HA WebSocket API is unreachable during an energy endpoint call
- **THEN** the endpoint SHALL return HTTP 503 with a message indicating Home Assistant is unavailable

### Requirement: Maintenance Calendar Endpoint

The implementation SHALL provide the behavior described by this requirement.
An endpoint returning maintenance items for calendar display and management.

#### Scenario: List all maintenance items

- **WHEN** `GET /api/home/maintenance` is called
- **THEN** it SHALL return all rows from `home.maintenance_items` sorted by `next_due_at` ascending (NULLs first)
- **AND** each entry SHALL include `id`, `name`, `category`, `interval_days`, `last_completed_at`, `next_due_at`, `status` (computed: `overdue`, `due`, `upcoming`, `ok`), and `notes`

#### Scenario: Filter by category

- **WHEN** `GET /api/home/maintenance?category=hvac` is called
- **THEN** only items with `category='hvac'` SHALL be returned

#### Scenario: Filter by status

- **WHEN** `GET /api/home/maintenance?status=overdue` is called
- **THEN** only items where `next_due_at < now()` SHALL be returned

#### Scenario: Complete maintenance item via API

- **WHEN** `POST /api/home/maintenance/{item_id}/complete` is called
- **THEN** the item's `last_completed_at` SHALL be set to the current time
- **AND** `next_due_at` SHALL be recomputed as `last_completed_at + interval_days * interval '1 day'`
- **AND** the response SHALL return the updated item

#### Scenario: Create maintenance item via API

- **WHEN** `POST /api/home/maintenance` is called with `name`, `category`, `interval_days`, and optional `notes`
- **THEN** a new row SHALL be inserted into `home.maintenance_items`
- **AND** the response SHALL return the created item with HTTP 201

#### Scenario: Delete maintenance item via API

- **WHEN** `DELETE /api/home/maintenance/{item_id}` is called
- **THEN** the row SHALL be deleted from `home.maintenance_items`
- **AND** if the item does not exist, HTTP 404 SHALL be returned

### Requirement: Dashboard Response Models

The implementation SHALL provide the behavior described by this requirement.
Pydantic models for all new dashboard endpoints.

#### Scenario: DeviceInventoryEntry model

- **WHEN** a device inventory response is serialized
- **THEN** the `DeviceInventoryEntry` model SHALL include fields: `entity_id` (str), `state` (str), `friendly_name` (str | None), `area_name` (str | None), `domain` (str), `last_updated` (datetime | None), `health_status` (Literal["healthy", "offline"])

#### Scenario: EnergyDataPoint model

- **WHEN** an energy consumption response is serialized
- **THEN** the `EnergyDataPoint` model SHALL include fields: `timestamp` (datetime), `total_kwh` (float), `devices` (dict[str, float] — entity_id to kWh mapping)

#### Scenario: TopConsumerEntry model

- **WHEN** a top consumers response is serialized
- **THEN** the `TopConsumerEntry` model SHALL include fields: `entity_id` (str), `friendly_name` (str | None), `total_kwh` (float), `percentage` (float)

#### Scenario: MaintenanceItemResponse model

- **WHEN** a maintenance item response is serialized
- **THEN** the `MaintenanceItemResponse` model SHALL include fields: `id` (UUID), `name` (str), `category` (str), `interval_days` (int), `last_completed_at` (datetime | None), `next_due_at` (datetime | None), `status` (Literal["overdue", "due", "upcoming", "ok"]), `notes` (str | None)

### Requirement: HA Source Health on Dashboard Snapshot Reads

Every dashboard endpoint that uses `ha_entity_snapshot` SHALL check
`ha_source_health` for the `home_assistant` source before treating cached rows
or their absence as current Home Assistant state.

#### Scenario: Degraded list and statistics envelopes retain stale data honestly

- **WHEN** the source is not `healthy` or has no recent recorded contact
- **THEN** `GET /api/home/entities`, `GET /api/home/snapshot-status`, and
  `GET /api/home/devices` SHALL still return their cached rows and counts with
  `ha_source_available=false` in their existing response envelope
- **AND** a non-empty `GET /api/home/areas` response SHALL set
  `ha_source_available=false` on each returned area row

#### Scenario: Degraded shapes without an envelope fail closed

- **WHEN** the source is not `healthy` or has no recent recorded contact
- **AND** `GET /api/home/entities/{entity_id}` finds no cached row, or
  `GET /api/home/areas` has no cached rows
- **THEN** the endpoint SHALL return HTTP 503 instead of an authoritative 404
  or truthful-looking empty list

#### Scenario: Energy discovery cannot bypass the source-health gate

- **WHEN** either energy endpoint is called while the source is not `healthy`
  or has no recent recorded contact
- **THEN** it SHALL return HTTP 503 before discovering sensors from
  `ha_entity_snapshot`
- **AND** an empty cached sensor list SHALL NOT bypass the guard and return a
  truthful-looking empty result

#### Scenario: Healthy source preserves existing behavior

- **WHEN** `ha_source_health` records a recent `status='healthy'` contact for
  `home_assistant`
- **THEN** the snapshot-backed dashboard endpoints SHALL preserve their prior
  query, filtering, pagination, not-found, and energy-statistics behavior
- **AND** every emitted `ha_source_available` field SHALL be `true`

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
