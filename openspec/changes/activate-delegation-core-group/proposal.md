## Why

The `activate-delegation-wake-loop` change (bu-27dxl.5.1, merged) reserved a
`delegation` core-tool group at the daemon level and implemented the four
delegation tools (bu-27dxl.5.2, merged), but deliberately deferred "runtime-config
validation, live activation, roster guidance, and seed configuration" as
follow-on work. Today `PATCH /api/butlers/{name}/runtime-config` rejects
`delegation` as an unknown core group with HTTP 422, so no operator can enable
the already-implemented delegation tools for any butler through the supported
config surface, and no runtime seed requests it either.

## What Changes

- Add `delegation` to the runtime-config API's known core-group allowlist
  (`KNOWN_CORE_GROUPS`) so `PATCH /api/butlers/{name}/runtime-config` accepts
  it instead of rejecting it as unknown.
- Add `delegation` to Finance's and Relationship's `[butler.runtime_seed]`
  `core_groups` in `butler.toml`, preserving their existing configured groups,
  so freshly-provisioned instances of these two butlers seed with delegation
  enabled.
- Document the release path for already-provisioned Finance/Relationship
  deployments: an operator `PATCH`es `core_groups` (existing groups plus
  `delegation`) via the dashboard runtime-config API and restarts the daemon,
  since `core_groups` is a cold field and DB-backed runtime config does not
  change merely because the toml seed changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `runtime-config-api`: `PATCH /api/butlers/{name}/runtime-config` accepts
  `delegation` as a known `core_groups` value instead of rejecting it as
  unknown.

## Impact

- `src/butlers/api/routers/runtime_config.py`: `KNOWN_CORE_GROUPS` gains
  `delegation`.
- `roster/finance/butler.toml`, `roster/relationship/butler.toml`:
  `runtime_seed.core_groups` gains `delegation`.
- No ledger/callback semantics, migrations, dashboard UI, or user-facing
  egress change. Live effect on already-running Finance/Relationship
  deployments requires the documented operator PATCH-plus-restart described
  above; this change does not perform that PATCH against production.
