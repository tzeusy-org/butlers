## MODIFIED Requirements

### Requirement: PATCH runtime config endpoint

The dashboard API SHALL expose `PATCH /api/butlers/{name}/runtime-config` accepting a partial update of runtime config fields.

Source: RFC 0007 §Dashboard API Surface
Scope: v1-mandatory

#### Scenario: Accepted fields
- **WHEN** a PATCH request is processed
- **THEN** only `core_groups`, `max_concurrent`, and `max_queued` are accepted; `model`/`runtime_type`/`args`/`session_timeout_s` are not runtime_config fields (they live on `public.model_catalog`)

#### Scenario: Update cold field
- **WHEN** a PATCH request updates `core_groups`
- **THEN** the DB row SHALL be updated, `updated_at` SHALL be set to now, and the response SHALL include `restart_required: ["core_groups"]`

#### Scenario: All managed fields are cold
- **WHEN** a PATCH request updates any of `core_groups`, `max_concurrent`, or `max_queued`
- **THEN** the response SHALL include `restart_required` listing exactly the changed fields, because all three require a daemon restart to take effect (there are no hot fields on this surface)

#### Scenario: Invalid field value — negative concurrency
- **WHEN** a PATCH request sets `max_concurrent` to a negative number or zero
- **THEN** the response SHALL return HTTP 422 with a validation error

#### Scenario: Invalid core_groups — unknown group name
- **WHEN** a PATCH request sets `core_groups` to `["infra", "foo"]`
- **THEN** the response SHALL return HTTP 422 with a validation error listing `"foo"` as an unknown group
- **AND** the known groups are: `infra`, `state`, `scheduling`, `sessions`, `notifications`, `media`, `temporal`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`, `delegation`

#### Scenario: delegation is a known core group
- **WHEN** a PATCH request sets `core_groups` to a list including `delegation`
- **THEN** validation SHALL accept it like any other known group and the DB row SHALL be updated accordingly

#### Scenario: Empty PATCH body
- **WHEN** a PATCH request has an empty body or no changed fields
- **THEN** the response SHALL return HTTP 200 with the current config unchanged and `restart_required: []`

#### Scenario: Butler not found
- **WHEN** a PATCH request targets a non-existent butler
- **THEN** the response SHALL return HTTP 404
