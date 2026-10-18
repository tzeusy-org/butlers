## MODIFIED Requirements

### Requirement: Core Tool Surface
Every butler daemon registers core MCP tools based on the `core_groups` allowlist from `runtime_config` (DB) and the butler's type/name. The daemon SHALL treat that stored allowlist as a projection, not independent capability authority: Git-owned `[butler.runtime_seed].core_groups` defines the declared surface, and a DB value may narrow it only when `core_groups_narrowing_reason` is non-empty. An unreasoned stale value is reconciled to Git at startup. When the effective `core_groups` is NULL, all groups are enabled (backward compat). When set, only tools in the listed groups are registered.

This requirement **supersedes** the tier-based system (UNIVERSAL/DOMAIN/MESSENGER/SWITCHBOARD constants and the `_tools_to_remove` post-registration pruning) documented in RFC 0002 §Tool Budget Discipline. The tier constants (`UNIVERSAL_CORE_TOOL_NAMES`, `DOMAIN_CORE_TOOL_NAMES`, `MESSENGER_CORE_TOOL_NAMES`) are removed. RFC 0002 §Tool Budget Discipline requires amendment to reflect the `core_groups` mechanism.

ID: REQ-core-daemon-002
Source: RFC 0002 §Core Tools, §Tool Budget Discipline (superseded by this change), Doctrine Rule #5 (operational tuning is DB-persisted); RFC 0027 §Tool Metadata Contract (bu-ondtw.3, PR #4244)
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

**`route.execute` special handling:** `route.execute` is registered on the MCP server for all butlers regardless of `core_groups` because the Switchboard calls it server-to-server. Per RFC 0002, `route.execute` is an infrastructure endpoint, not an LLM-facing tool. The checked-in tool presentation inventory (`src/butlers/core/tool_presentation_inventory.py`) classifies `route.execute` and `cancel_session` as not LLM-presentable. Classification is representation metadata only: it never removes a handler, changes canonical `tools/list`, or replaces handler-level call authority.

#### Scenario: core_groups filters tool registration
- **WHEN** a butler daemon starts with `core_groups = ['infra', 'notifications']` in runtime_config
- **AND** that set is a strict subset of its Git declaration carrying a non-empty narrowing reason
- **THEN** only tools in the `infra` and `notifications` groups SHALL be registered on the MCP server (plus `route.execute` which is always registered)
- **AND** tools in other groups (state, scheduling, sessions, media, temporal) SHALL NOT be registered

#### Scenario: Unreasoned runtime row cannot hide a Git capability
- **WHEN** a butler daemon's runtime_config row omits one or more Git-declared groups and has no narrowing reason
- **THEN** the Git declaration SHALL become the effective group set before tool registration
- **AND** the stored row and audit log SHALL be reconciled idempotently

#### Scenario: NULL core_groups enables all tools
- **WHEN** a butler daemon starts with `core_groups = NULL` in runtime_config
- **THEN** all core tool groups SHALL be registered (backward compatibility)

#### Scenario: route.execute always registered
- **WHEN** any butler daemon starts, regardless of core_groups value
- **THEN** `route.execute` SHALL be registered on the MCP server
- **AND** it SHALL be callable by the Switchboard for routed message delivery
- **AND** the checked-in presentation inventory SHALL classify it as not LLM-presentable while it stays in canonical `tools/list`

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
The daemon config loader SHALL parse `[butler.runtime_seed]` from the toml and return a `RuntimeSeedConfig` dataclass. The old `[butler.runtime]` and `[butler.seed_configs]` sections SHALL NOT be rejected with a clear error; they SHALL be accepted and ignored. The dataclass is operational-only: retired `model`, `runtime_type`, `args`, and `session_timeout_s` keys inside `[butler.runtime_seed]` SHALL be rejected, while the obsolete top-level `[runtime]` section SHALL be rejected with deletion guidance. The dataclass SHALL carry `tool_exposure_policy` as one further operational field without restoring any retired runtime-selection field.

ID: REQ-core-daemon-003
Source: RFC 0001 §Startup Phases (phase 1 — config load), Doctrine Rule #5; RFC 0027 §Exposure Planning State Machine (bu-ondtw.2, PR #4091)
Scope: v1-mandatory

#### Scenario: Parse runtime_seed section
- **WHEN** `load_config()` reads a toml with `[butler.runtime_seed]`
- **THEN** a `RuntimeSeedConfig` SHALL NOT be returned with fields: core_groups (tuple[str,...] | None), model (str | None), runtime_type (str, default "codex"), args (tuple[str,...], default ()), max_concurrent_sessions (int, default 3), max_queued_sessions (int, default 10), session_timeout_s (int, default 900), liveness_ttl_seconds (int, default 300), route_contract_min (int, default 1), route_contract_max (int, default 1)
- **AND** it SHALL instead contain exactly these fields: `core_groups` (`tuple[str, ...] | None`, default `None`), `catalog_read_sensitivity` (`normal | internal | confidential`, default `normal`), `max_concurrent_sessions` (`int`, default `3`), `max_queued_sessions` (`int`, default `10`), `liveness_ttl_seconds` (`int`, default `300`), `route_contract_min` (`int`, default `1`), `route_contract_max` (`int`, default `1`), and `tool_exposure_policy` (`eager_filtered | auto`, default `eager_filtered`)
- **AND** `model`, `runtime_type`, `args`, and `session_timeout_s` SHALL NOT be dataclass fields and SHALL raise `ConfigError` when supplied inside `[butler.runtime_seed]`

#### Scenario: Reject old [butler.runtime] section
- **WHEN** `load_config()` reads a toml with `[butler.runtime]`
- **THEN** a `ConfigError` SHALL NOT be raised with message directing the user to rename to `[butler.runtime_seed]`; the obsolete nested section SHALL be accepted and ignored
- **AND** the nested section SHALL NOT alter the returned `RuntimeSeedConfig` or runtime selection

#### Scenario: Reject old [butler.seed_configs] section
- **WHEN** `load_config()` reads a toml with `[butler.seed_configs]`
- **THEN** a `ConfigError` SHALL NOT be raised with message directing the user to merge into `[butler.runtime_seed]`; the obsolete nested section SHALL be accepted and ignored
- **AND** the nested section SHALL NOT alter the returned `RuntimeSeedConfig`

#### Scenario: Obsolete top-level [runtime] section is rejected
- **WHEN** `load_config()` reads a toml with a top-level `[runtime]` section
- **THEN** a `ConfigError` SHALL state that the section is no longer supported and direct the operator to delete it
- **AND** the error SHALL NOT direct the operator to move runtime selection into `[butler.runtime_seed]`

#### Scenario: Missing runtime_seed section uses defaults
- **WHEN** `load_config()` reads a toml with no `[butler.runtime_seed]` section
- **THEN** a `RuntimeSeedConfig` with all default values SHALL be returned (backward compat for minimal tomls)
- **AND** the default tool exposure policy SHALL be `eager_filtered`
