## MODIFIED Requirements

### Requirement: PATCH runtime config endpoint

The dashboard API SHALL expose `PATCH /api/butlers/{name}/runtime-config` accepting a partial update of runtime config fields. The `delegation` core group SHALL remain a known group so stored rows keep validating, but it SHALL register no tools, because the `delegate_*` tools it carried are deleted at cutover.

Source: RFC 0007 §Dashboard API Surface; RFC 0038 D7
Scope: v1-mandatory

#### Scenario: Accepted fields
- **WHEN** a PATCH request is processed
- **THEN** only `core_groups`, `core_groups_narrowing_reason`, `catalog_read_sensitivity`, `max_concurrent`, and `max_queued` are accepted; `model`/`runtime_type`/`args`/`session_timeout_s` are not runtime_config fields (they live on `public.model_catalog`)

#### Scenario: Catalog read sensitivity updates hot

- **WHEN** a PATCH sets `catalog_read_sensitivity` to `normal`, `internal`, or `confidential`
- **THEN** the DB row SHALL be updated and `restart_required` SHALL remain empty for that field
- **AND** any other value SHALL return HTTP 422

#### Scenario: Update cold field
- **WHEN** a PATCH request updates `core_groups`
- **THEN** the DB row SHALL be updated, `updated_at` SHALL be set to now, and the response SHALL include `restart_required: ["core_groups"]`

#### Scenario: Narrowing requires an explicit reason
- **WHEN** a PATCH proposes a `core_groups` subset of the Git declaration
- **THEN** it SHALL require a non-blank `core_groups_narrowing_reason`
- **AND** both fields SHALL require a daemon restart

#### Scenario: Clearing the reason restores Git authority
- **WHEN** a PATCH clears `core_groups_narrowing_reason`
- **THEN** the response SHALL show Git as the effective source and the Git-declared groups as effective
- **AND** startup reconciliation SHALL persist that Git group set idempotently

#### Scenario: All managed fields are cold
- **WHEN** a PATCH request updates any of `core_groups`, `max_concurrent`, or `max_queued`
- **THEN** the response SHALL include `restart_required` listing exactly the changed fields, because all three require a daemon restart to take effect (there are no hot fields on this surface)
- **AND** the historical no-hot-fields clause is superseded for new fields only: `catalog_read_sensitivity` is hot and MUST NOT appear in `restart_required`

#### Scenario: Invalid field value — negative concurrency
- **WHEN** a PATCH request sets `max_concurrent` to a negative number or zero
- **THEN** the response SHALL return HTTP 422 with a validation error

#### Scenario: Invalid core_groups — unknown group name
- **WHEN** a PATCH request sets `core_groups` to `["infra", "foo"]`
- **THEN** the response SHALL return HTTP 422 with a validation error listing `"foo"` as an unknown group
- **AND** the known groups are: `infra`, `state`, `scheduling`, `sessions`, `notifications`, `media`, `graph`, `temporal`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`, `delegation`, `domain_events`, `fleet_cases`

#### Scenario: delegation is a known core group
- **WHEN** a PATCH request sets `core_groups` to a list including `delegation`
- **THEN** validation SHALL accept it like any other known group and the DB row SHALL be updated accordingly
- **AND** enabling `delegation` SHALL register no tool on the butler, because the `delegate_*` tools no longer exist

#### Scenario: Empty PATCH body
- **WHEN** a PATCH request has an empty body or no changed fields
- **THEN** the response SHALL return HTTP 200 with the current config unchanged and `restart_required: []`

#### Scenario: Butler not found
- **WHEN** a PATCH request targets a non-existent butler
- **THEN** the response SHALL return HTTP 404
