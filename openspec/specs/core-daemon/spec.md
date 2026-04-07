## ADDED Requirements

### Requirement: Agent Type Awareness
The daemon SHALL read the `type` field from `butler.toml` config and apply type-specific behaviors at well-defined decision points. The daemon engine remains unified — there is no separate `StafferDaemon` class.

#### Scenario: Config parsing includes type
- **WHEN** the daemon loads `butler.toml`
- **THEN** it parses `[butler].type` as a `ButlerType` enum (`BUTLER` or `STAFFER`)
- **AND** defaults to `ButlerType.BUTLER` if the field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.type` for downstream decision points

#### Scenario: Config parsing includes permissions
- **WHEN** the daemon loads `butler.toml` with a `[butler.permissions]` section
- **THEN** it parses `cross_butler_access` as a list of strings
- **AND** defaults to an empty list if the section or field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.permissions.cross_butler_access`

#### Scenario: Staffer-specific startup behaviors
- **WHEN** the daemon starts with `config.type == ButlerType.STAFFER`
- **THEN** it proceeds through the same lifecycle phases as a butler
- **AND** during schedule sync, it skips registration of any `daily_briefing_contribution` schedule entries
- **AND** during switchboard registration, it includes `type = "staffer"` in the registration payload so the switchboard can exclude it from user-message routing

#### Scenario: Butler-specific startup behaviors unchanged
- **WHEN** the daemon starts with `config.type == ButlerType.BUTLER`
- **THEN** startup proceeds exactly as before this change — no behavioral differences from the pre-staffer codebase

## MODIFIED Requirements

### Requirement: Core Tool Surface
Every butler daemon registers core MCP tools conditionally based on `butler_type` (from `butler.toml`) and `butler_name`. The goal is 30-50 tools per butler to stay within agent context budgets.

#### Contract: Tool tiers

Core tools are partitioned into four tiers:

| Tier | Constant | Gating rule |
|---|---|---|
| Universal | `UNIVERSAL_CORE_TOOL_NAMES` | Registered for ALL butlers regardless of type or name |
| Domain | `DOMAIN_CORE_TOOL_NAMES` | Registered only when `butler_type != ButlerType.STAFFER` (i.e., domain butlers) |
| Messenger | `MESSENGER_CORE_TOOL_NAMES` | Registered only when `butler_name == "messenger"` |
| Switchboard | (inline in `_register_core_tools`) | Registered only when `butler_name == "switchboard"` |

The backwards-compatible union `CORE_TOOL_NAMES = UNIVERSAL | MESSENGER | DOMAIN` exists for test assertions but is not used for registration.

#### Scenario: Universal tools are always registered
- **WHEN** any butler daemon completes startup
- **THEN** the following tools are available: `status`, `trigger`, `route.execute`, `tick`, `state_get`, `state_set`, `state_delete`, `state_list`, `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`, `sessions_list`, `sessions_get`, `sessions_summary`, `sessions_daily`, `top_sessions`, `schedule_costs`, `notify`, `remind`, `get_attachment`, `module.states`, `module.set_enabled`, `correct`

#### Scenario: route.execute is universal
- **WHEN** any butler (including staffers) completes startup
- **THEN** `route.execute` is registered — all butlers can receive routed requests from the Switchboard

#### Scenario: Domain tools excluded from staffers
- **WHEN** the daemon starts with `config.type == ButlerType.STAFFER`
- **THEN** the following tools are NOT registered: `deadline_create`, `deadline_update`, `deadline_list`, `deadline_delete`, `event_chain_create`, `event_chain_list`, `event_chain_delete`, `seasonal_period_list`, `seasonal_period_create`, `seasonal_period_create_preset`

#### Scenario: Messenger-only tools
- **WHEN** the daemon starts with `butler_name == "messenger"`
- **THEN** the following additional tools are registered: `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel`
- **AND** these tools are NOT registered for any other butler

#### Scenario: Switchboard-only tools
- **WHEN** the daemon starts with `butler_name == "switchboard"`
- **THEN** the following additional tools are registered: `ingest`, `route_to_butler`, `connector.heartbeat`, `backfill.poll`, `backfill.progress`
- **AND** these tools are NOT registered for any other butler
