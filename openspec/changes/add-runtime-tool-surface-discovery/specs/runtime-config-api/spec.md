## MODIFIED Requirements

### Requirement: GET runtime config endpoint

The dashboard API SHALL expose `GET /api/butlers/{name}/runtime-config` returning the current runtime config from the DB. This is a core API route in `src/butlers/api/routers/` (not an auto-discovered butler-specific route per RFC 0007 §Auto-Discovered Butler Routes), because it is cross-butler infrastructure that reads from any butler's schema.

ID: REQ-runtime-config-api-001
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
- **THEN** it SHALL include `field_tiers` mapping each runtime_config field to `"hot"` or `"cold"`. As built, all three managed fields are cold: `{"core_groups": "cold", "max_concurrent": "cold", "max_queued": "cold"}`
- **AND** `model` and `session_timeout_s` are NOT part of this map; migration `core_073` moved them onto `public.model_catalog`, edited via the model-settings API
- **AND** the response SHALL add `"tool_exposure_policy": "hot"` because subsequent invocations resolve it through the runtime-config accessor without restarting the daemon

### Requirement: PATCH runtime config endpoint

The dashboard API SHALL expose `PATCH /api/butlers/{name}/runtime-config` accepting a partial update of runtime config fields.

ID: REQ-runtime-config-api-002
Source: RFC 0007 §Dashboard API Surface; RFC 0027 §Exposure Planning State Machine
Scope: v1-mandatory

#### Scenario: Accepted fields
- **WHEN** a PATCH request is processed
- **THEN** only `core_groups`, `max_concurrent`, and `max_queued` are accepted; `model`/`runtime_type`/`args`/`session_timeout_s` are not runtime_config fields (they live on `public.model_catalog`)
- **AND** `tool_exposure_policy` SHALL also be accepted as `eager_filtered` or `auto`

#### Scenario: Update cold field
- **WHEN** a PATCH request updates `core_groups`
- **THEN** the DB row SHALL be updated, `updated_at` SHALL be set to now, and the response SHALL include `restart_required: ["core_groups"]`

#### Scenario: All managed fields are cold
- **WHEN** a PATCH request updates any of `core_groups`, `max_concurrent`, or `max_queued`
- **THEN** the response SHALL include `restart_required` listing exactly the changed fields, because those three cold fields require a daemon restart to take effect
- **AND** this historical scenario applies only to those three fields; hot `tool_exposure_policy` takes effect for subsequent invocations and SHALL NOT appear in `restart_required`

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

#### Scenario: Hot exposure policy applies to subsequent sessions

- **WHEN** a PATCH request changes `tool_exposure_policy` to `auto` or `eager_filtered`
- **THEN** the DB row and accessor cache are updated and the response excludes that field from `restart_required`
- **AND** sessions planned after the update use the new policy while in-flight plans remain immutable

#### Scenario: Invalid exposure policy is rejected

- **WHEN** a PATCH request supplies any `tool_exposure_policy` other than `eager_filtered` or `auto`
- **THEN** the response SHALL return HTTP 422 without changing the stored policy

## Source References

- Non-Negotiable Rule 4 (deterministic daemon and ephemeral intelligence)
- Non-Negotiable Rule 5 (operational tuning is DB-backed)
- RFC 0007 (dashboard and API surface)
- RFC 0027 (runtime tool surface discovery and exposure)
