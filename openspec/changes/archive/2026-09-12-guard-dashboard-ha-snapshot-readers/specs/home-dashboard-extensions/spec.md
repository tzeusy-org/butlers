## MODIFIED Requirements

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

## ADDED Requirements

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
