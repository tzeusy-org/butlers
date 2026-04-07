## ADDED Requirements

### Requirement: GET runtime config endpoint

The dashboard API SHALL expose `GET /api/butlers/{name}/runtime-config` returning the current runtime config from the DB. This is a core API route in `src/butlers/api/routers/` (not an auto-discovered butler-specific route per RFC 0007 §Auto-Discovered Butler Routes), because it is cross-butler infrastructure that reads from any butler's schema.

Source: RFC 0007 §Dashboard API Surface, §Response Envelope
Scope: v1-mandatory

#### Scenario: Successful read
- **WHEN** a GET request is made for an existing butler
- **THEN** the response SHALL contain all runtime_config fields with their current values, `updated_at` timestamp, and `field_tiers` map

#### Scenario: Butler not found
- **WHEN** a GET request is made for a non-existent butler
- **THEN** the response SHALL return HTTP 404

#### Scenario: Field tiers included in response
- **WHEN** a GET response is returned
- **THEN** it SHALL include `field_tiers` mapping each config field to `"hot"` or `"cold"` (e.g., `{"core_groups": "cold", "model": "hot", "session_timeout_s": "hot", "max_concurrent": "cold", ...}`)

### Requirement: PATCH runtime config endpoint

The dashboard API SHALL expose `PATCH /api/butlers/{name}/runtime-config` accepting a partial update of runtime config fields.

Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Update hot field
- **WHEN** a PATCH request updates `session_timeout_s`
- **THEN** the DB row SHALL be updated, `updated_at` SHALL be set to now, and the response SHALL include `restart_required: []`

#### Scenario: Update cold field
- **WHEN** a PATCH request updates `core_groups`
- **THEN** the DB row SHALL be updated, `updated_at` SHALL be set to now, and the response SHALL include `restart_required: ["core_groups"]`

#### Scenario: Update mixed hot and cold fields
- **WHEN** a PATCH request updates both `model` and `max_concurrent`
- **THEN** the response SHALL include `restart_required: ["max_concurrent"]` listing only the cold fields that changed

#### Scenario: Invalid field value — negative concurrency
- **WHEN** a PATCH request sets `max_concurrent` to a negative number or zero
- **THEN** the response SHALL return HTTP 422 with a validation error

#### Scenario: Invalid core_groups — unknown group name
- **WHEN** a PATCH request sets `core_groups` to `["infra", "foo"]`
- **THEN** the response SHALL return HTTP 422 with a validation error listing `"foo"` as an unknown group
- **AND** the known groups are: `infra`, `state`, `scheduling`, `sessions`, `notifications`, `media`, `temporal`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`

#### Scenario: Empty PATCH body
- **WHEN** a PATCH request has an empty body or no changed fields
- **THEN** the response SHALL return HTTP 200 with the current config unchanged and `restart_required: []`

#### Scenario: Butler not found
- **WHEN** a PATCH request targets a non-existent butler
- **THEN** the response SHALL return HTTP 404
