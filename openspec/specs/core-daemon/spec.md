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

### Requirement: Blob storage initialization at startup phase 8c
The daemon SHALL initialize the S3-compatible blob store at startup phase 8c, immediately after the layered `CredentialStore` is built (phase 8b) and before CLI auth restoration (phase 8c2). All S3 connection parameters SHALL be resolved from the credential store with `env_fallback=False`; there is no `[butler.storage]` TOML section and no environment-variable resolution path.

Source: RFC 0001 §Startup Phases (phase 8c, between 8b credential store and 8c2 CLI auth restore)
Scope: v1-mandatory

#### Scenario: Phase ordering
- **WHEN** the daemon starts
- **THEN** phase 8c (blob store init) SHALL execute after phase 8b (credential store build) and before phase 8c2 (CLI auth token restore)
- **AND** the blob store SHALL be available to module `on_startup` hooks (phase 9) as `daemon.blob_store`

#### Scenario: Credential resolution is DB-only
- **WHEN** the daemon initializes the blob store
- **THEN** it SHALL resolve `BLOB_S3_ENDPOINT_URL`, `BLOB_S3_BUCKET`, `BLOB_S3_REGION`, `BLOB_S3_ACCESS_KEY_ID`, and `BLOB_S3_SECRET_ACCESS_KEY` via `credential_store.resolve(key, env_fallback=False)`
- **AND** values SHALL NOT be read from `os.environ` or `butler.toml`

#### Scenario: head_bucket startup check
- **WHEN** an `S3BlobStore` is constructed from resolved credentials
- **THEN** the daemon SHALL invoke `S3BlobStore.startup_check()` (which performs a `head_bucket` call) before proceeding
- **AND** an unreachable endpoint or missing bucket SHALL fail startup with a clear error

#### Scenario: Missing endpoint or bucket is non-fatal
- **WHEN** `BLOB_S3_ENDPOINT_URL` or `BLOB_S3_BUCKET` is absent from the credential store
- **THEN** the daemon SHALL log a warning pointing operators at the dashboard secrets UI (`/secrets`)
- **AND** SHALL set `daemon.blob_store = None` and continue startup (blob operations will fail at runtime)

### Requirement: Removal of blob_storage_dir config
The legacy `blob_storage_dir` field and any `[butler.storage]` TOML section SHALL NOT be parsed by the config loader. Local filesystem blob storage is no longer supported.

Source: RFC 0001 §Startup Phases (phase 1 — config load)
Scope: v1-mandatory

#### Scenario: ButlerConfig has no blob_storage_dir
- **WHEN** the daemon loads `butler.toml`
- **THEN** `ButlerConfig` SHALL NOT expose a `blob_storage_dir` attribute
- **AND** keys named `blob_dir` or `blob_storage_dir` SHALL be ignored (not surfaced as config)

#### Scenario: No [butler.storage] TOML parsing
- **WHEN** the daemon loads `butler.toml`
- **THEN** it SHALL NOT parse a `[butler.storage]` section into config
- **AND** S3 settings SHALL be sourced from the credential store only (see "Blob storage initialization at startup phase 8c")
