## MODIFIED Requirements

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

#### Scenario: Butler-specific startup behaviors
- **WHEN** the daemon starts with `config.type == ButlerType.BUTLER`
- **THEN** it proceeds through the full lifecycle with none of the staffer-specific skips
- **AND** it registers the core tools that are withheld from staffers (for example session queries, schedule mutation, delegation, and temporal tools) when their groups are enabled
- **AND** it reports `type = "butler"` to the switchboard so it remains eligible for user-message routing

### Requirement: Boot sequence seeds and reads runtime config from DB
The daemon boot sequence SHALL create a `RuntimeConfigAccessor`, seed the DB from toml on first boot, and use the DB-backed config for tool registration and spawner construction. During that read it SHALL reconcile Git-owned core groups and use the resolved split-authority config. This is RFC 0001 phase 9, after phase 8 module dependency/bootstrap work and before phase 10 TOML schedule synchronization.

Phase: **9 — Resolve runtime config from DB (seed if first boot).**
Failure mode: Fatal — cannot operate without runtime config.

Source: RFC 0001 §Startup Phases (phase 9, between phases 8 and 10)

#### Scenario: First boot seeds from toml
- **WHEN** the daemon starts and `runtime_config` table is empty
- **THEN** the daemon SHALL insert a row from `RuntimeSeedConfig` values
- **AND** log "Seeded runtime config from butler.toml for {name}"

#### Scenario: Subsequent boot reads from DB
- **WHEN** the daemon starts and `runtime_config` table has a row
- **THEN** the daemon SHALL use the DB values (ignoring toml seed) for DB-owned operational fields
- **AND** it SHALL resolve core groups from Git unless the row carries a valid explicit narrowing reason
- **AND** log "Using runtime config from DB for {name} (seeded {date}, updated {date})" plus the effective core-group source without logging the reason text or other sensitive payloads

#### Scenario: Accessor passed to spawner
- **WHEN** the daemon constructs the Spawner (phase 12)
- **THEN** it SHALL pass the `RuntimeConfigAccessor` instance so the spawner can read hot fields per-spawn

#### Scenario: core_groups read at tool registration time
- **WHEN** the daemon calls `_register_core_tools()` (phase 13)
- **THEN** it SHALL read `core_groups` from the effective RuntimeConfig (from accessor), not from the toml seed; the accessor SHALL already have reconciled Git authority or retained a reasoned narrowing

### Requirement: Blob storage initialization at startup phase 8c
The daemon SHALL initialize the S3-compatible blob store at startup phase 8c, immediately after the layered `CredentialStore` is built (phase 8b) and before CLI auth restoration (phase 8c2). All S3 connection parameters SHALL be resolved from the credential store with `env_fallback=False`; there is no `[butler.storage]` TOML section and no environment-variable resolution path.

Source: RFC 0001 §Startup Phases (phase 8c, between 8b credential store and 8c2 CLI auth restore)

#### Scenario: Phase ordering
- **WHEN** the daemon starts
- **THEN** phase 8c (blob store init) SHALL execute after phase 8b (credential store build) and before phase 8c2 (CLI auth token restore)
- **AND** the blob store SHALL be available to module `on_startup` hooks (phase 11) as `daemon.blob_store`

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

#### Scenario: ButlerConfig has no blob_storage_dir
- **WHEN** the daemon loads `butler.toml`
- **THEN** `ButlerConfig` SHALL NOT expose a `blob_storage_dir` attribute
- **AND** keys named `blob_dir` or `blob_storage_dir` SHALL be ignored (not surfaced as config)

#### Scenario: No [butler.storage] TOML parsing
- **WHEN** the daemon loads `butler.toml`
- **THEN** it SHALL NOT parse a `[butler.storage]` section into config
- **AND** S3 settings SHALL be sourced from the credential store only (see "Blob storage initialization at startup phase 8c")
