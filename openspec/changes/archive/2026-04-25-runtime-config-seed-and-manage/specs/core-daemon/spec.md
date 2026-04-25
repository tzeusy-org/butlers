## MODIFIED Requirements

### Requirement: Core Tool Surface
Every butler daemon registers core MCP tools based on the `core_groups` allowlist from `runtime_config` (DB) and the butler's type/name. When `core_groups` is NULL, all groups are enabled (backward compat). When set, only tools in the listed groups are registered.

This requirement **supersedes** the tier-based system (UNIVERSAL/DOMAIN/MESSENGER/SWITCHBOARD constants and the `_tools_to_remove` post-registration pruning) documented in RFC 0002 §Tool Budget Discipline. The tier constants (`UNIVERSAL_CORE_TOOL_NAMES`, `DOMAIN_CORE_TOOL_NAMES`, `MESSENGER_CORE_TOOL_NAMES`) are removed. RFC 0002 §Tool Budget Discipline requires amendment to reflect the `core_groups` mechanism.

Source: RFC 0002 §Core Tools, §Tool Budget Discipline (superseded by this change), Doctrine Rule #5 (operational tuning is DB-persisted)
Scope: v1-mandatory

Tool groups:
- `infra`: status, trigger, tick, correct
- `state`: state_get, state_set, state_delete, state_list
- `scheduling`: schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs
- `sessions`: sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions
- `notifications`: notify, remind
- `media`: get_attachment
- `temporal`: deadline_*, event_chain_*, seasonal_period_*
- `module_mgmt`: module.states, module.set_enabled
- `switchboard_routing`: ingest, route_to_butler, connector.heartbeat (name-gated: switchboard only)
- `switchboard_backfill`: backfill.poll, backfill.progress (name-gated: switchboard only)

Name-gated tools (messenger-only, switchboard-only) are gated by butler name as an additional check — `core_groups` controls which groups are *eligible*, but `switchboard_routing` and `switchboard_backfill` tools are ONLY registered when `butler_name == "switchboard"`, regardless of core_groups. Similarly, `delivery_preferences_*` and `deferred_notification_*` tools are ONLY registered when `butler_name == "messenger"`. This prevents a domain butler from accidentally gaining switchboard routing powers by adding `switchboard_routing` to its core_groups.

**`route.execute` special handling:** `route.execute` is registered on the MCP server for all butlers regardless of `core_groups` because the Switchboard calls it server-to-server. Per RFC 0002, `route.execute` is an infrastructure endpoint, not an LLM-facing tool. LLM-visibility filtering (hiding `route.execute` from the LLM's tool list while keeping the MCP handler callable) is deferred to a future change — the current `core_groups` mechanism is single-tier (registered or not) and does not support "registered but hidden from LLM."

#### Scenario: core_groups filters tool registration
- **WHEN** a butler daemon starts with `core_groups = ['infra', 'notifications']` in runtime_config
- **THEN** only tools in the `infra` and `notifications` groups SHALL be registered on the MCP server (plus `route.execute` which is always registered)
- **AND** tools in other groups (state, scheduling, sessions, media, temporal) SHALL NOT be registered

#### Scenario: NULL core_groups enables all tools
- **WHEN** a butler daemon starts with `core_groups = NULL` in runtime_config
- **THEN** all core tool groups SHALL be registered (backward compatibility)

#### Scenario: route.execute always registered
- **WHEN** any butler daemon starts, regardless of core_groups value
- **THEN** `route.execute` SHALL be registered on the MCP server
- **AND** it SHALL be callable by the Switchboard for routed message delivery

#### Scenario: Switchboard-only tools name-gated
- **WHEN** a non-switchboard butler has `switchboard_routing` in its core_groups
- **THEN** `ingest`, `route_to_butler`, and `connector.heartbeat` SHALL NOT be registered
- **AND** the daemon SHALL log a warning about the ineffective group

#### Scenario: Messenger-only tools name-gated
- **WHEN** a non-messenger butler has core_groups that would include messenger tools
- **THEN** `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel` SHALL NOT be registered

#### Scenario: Domain tools excluded from staffers via core_groups
- **WHEN** a staffer starts and its core_groups does not include `temporal`
- **THEN** deadline, event_chain, and seasonal_period tools SHALL NOT be registered

### Requirement: Config loading parses runtime_seed section
The daemon config loader SHALL parse `[butler.runtime_seed]` from the toml and return a `RuntimeSeedConfig` dataclass. The old `[butler.runtime]` and `[butler.seed_configs]` sections SHALL be rejected with a clear error.

Source: RFC 0001 §Startup Phases (phase 1 — config load), Doctrine Rule #5
Scope: v1-mandatory

#### Scenario: Parse runtime_seed section
- **WHEN** `load_config()` reads a toml with `[butler.runtime_seed]`
- **THEN** a `RuntimeSeedConfig` SHALL be returned with fields: core_groups (tuple[str,...] | None), model (str | None), runtime_type (str, default "codex"), args (tuple[str,...], default ()), max_concurrent_sessions (int, default 3), max_queued_sessions (int, default 10), session_timeout_s (int, default 900), liveness_ttl_seconds (int, default 300), route_contract_min (int, default 1), route_contract_max (int, default 1)

#### Scenario: Reject old [butler.runtime] section
- **WHEN** `load_config()` reads a toml with `[butler.runtime]`
- **THEN** a `ConfigError` SHALL be raised with message directing the user to rename to `[butler.runtime_seed]`

#### Scenario: Reject old [butler.seed_configs] section
- **WHEN** `load_config()` reads a toml with `[butler.seed_configs]`
- **THEN** a `ConfigError` SHALL be raised with message directing the user to merge into `[butler.runtime_seed]`

#### Scenario: Missing runtime_seed section uses defaults
- **WHEN** `load_config()` reads a toml with no `[butler.runtime_seed]` section
- **THEN** a `RuntimeSeedConfig` with all default values SHALL be returned (backward compat for minimal tomls)

### Requirement: Boot sequence seeds and reads runtime config from DB
The daemon boot sequence SHALL create a `RuntimeConfigAccessor`, seed the DB from toml on first boot, and use the DB-backed config for tool registration and spawner construction. This inserts a new phase between RFC 0001 phases 8b (credential store) and 10 (spawner creation).

New phase: **9b — Resolve runtime config from DB (seed if first boot).**
Failure mode: Fatal — cannot operate without runtime config.

Source: RFC 0001 §Startup Phases (new phase 9b, between 8b and 10)
Scope: v1-mandatory

#### Scenario: First boot seeds from toml
- **WHEN** the daemon starts and `runtime_config` table is empty
- **THEN** the daemon SHALL insert a row from `RuntimeSeedConfig` values
- **AND** log "Seeded runtime config from butler.toml for {name}"

#### Scenario: Subsequent boot reads from DB
- **WHEN** the daemon starts and `runtime_config` table has a row
- **THEN** the daemon SHALL use the DB values (ignoring toml seed)
- **AND** log "Using runtime config from DB for {name} (seeded {date}, updated {date})"

#### Scenario: Accessor passed to spawner
- **WHEN** the daemon constructs the Spawner (phase 10)
- **THEN** it SHALL pass the `RuntimeConfigAccessor` instance so the spawner can read hot fields per-spawn

#### Scenario: core_groups read at tool registration time
- **WHEN** the daemon calls `_register_core_tools()` (phase 12)
- **THEN** it SHALL read `core_groups` from the effective RuntimeConfig (from accessor), not from the toml seed
