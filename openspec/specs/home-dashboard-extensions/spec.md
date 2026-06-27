# Home Dashboard Extensions

## Purpose

Dashboard API endpoints for device inventory, energy consumption time-series, and maintenance calendar, surfacing data produced by the Home butler's deterministic monitoring jobs.

## Requirements

### Requirement: Device Inventory Endpoint

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
- **THEN** the response SHALL be a page-based paginated wrapper (`DeviceInventoryResponse`) with `meta` (`DevicePaginationMeta`) containing `page`, `page_size`, `total_count`, and `total_pages`

### Requirement: Energy Consumption Endpoint

An endpoint returning energy consumption time-series data for dashboard charts.

#### Scenario: Daily energy consumption

- **WHEN** `GET /api/home/energy?period=day&start=2026-03-01&end=2026-03-25` is called
- **THEN** it SHALL proxy the request to the HA REST API `recorder/get_statistics_during_period` with `period="day"`
- **AND** it SHALL return a list of daily data points with `timestamp`, `total_kwh`, and per-device breakdown (`devices`)

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

#### Scenario: HA unavailable fallback

- **WHEN** the HA REST API is unreachable during an energy endpoint call
- **THEN** the endpoint SHALL return HTTP 503 with a message indicating Home Assistant is unavailable

### Requirement: Maintenance Calendar Endpoint

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
