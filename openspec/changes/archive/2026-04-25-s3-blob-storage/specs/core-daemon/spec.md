# Core Daemon — S3 Blob Storage Delta

## MODIFIED Requirements

### Requirement: Configuration Loading and Validation
The daemon loads `butler.toml` from a git-backed config directory, resolves `${VAR_NAME}` environment variable references recursively across strings/dicts/lists, and produces a validated `ButlerConfig` dataclass. Unresolved required env vars are startup-blocking `ConfigError` exceptions. The config includes required fields (`butler.name`, `butler.port`), optional sub-sections (`butler.db`, `butler.env`, `butler.runtime`, `butler.scheduler`, `butler.shutdown`, `butler.logging`, `butler.switchboard`, `butler.security`, `butler.storage`, `buffer`), schedule entries (`[[butler.schedule]]`), and module declarations (`[modules.*]`). The `[butler.storage]` section defines S3-compatible blob storage connection parameters (`endpoint_url`, `bucket`, `access_key_id`, `secret_access_key`, `region`), replacing the former `blob_storage_dir` filesystem path.

#### Scenario: Valid config loads successfully
- **WHEN** a valid `butler.toml` exists in the config directory with required fields `butler.name` and `butler.port`
- **THEN** `load_config()` returns a `ButlerConfig` with all parsed fields
- **AND** environment variable references like `${VAR_NAME}` are resolved from `os.environ`

#### Scenario: Missing required field blocks startup
- **WHEN** `butler.toml` is missing `butler.name` or `butler.port`
- **THEN** `load_config()` raises `ConfigError` with a descriptive message

#### Scenario: Unresolved env var blocks startup
- **WHEN** a config string contains `${VAR_NAME}` and `VAR_NAME` is not set in the environment
- **THEN** `load_config()` raises `ConfigError` listing all missing variable names

#### Scenario: Schema-based DB isolation requires schema field
- **WHEN** `butler.db.name` is set to `"butlers"` (consolidated DB) but `butler.db.schema` is absent
- **THEN** `load_config()` raises `ConfigError` requiring the schema field

#### Scenario: Runtime type validation at config load time
- **WHEN** `[runtime].type` is set to an unknown adapter name
- **THEN** `load_config()` raises `ConfigError` via `get_adapter()` failing with `ValueError`

#### Scenario: Storage config requires endpoint_url and bucket
- **WHEN** `butler.toml` is loaded and `[butler.storage]` is present but missing `endpoint_url` or `bucket`
- **THEN** `load_config()` SHALL raise `ConfigError` indicating the missing required storage field

#### Scenario: Storage config env var overrides
- **WHEN** `BLOB_S3_ENDPOINT_URL` or `BLOB_S3_BUCKET` environment variables are set
- **THEN** they SHALL override the corresponding `[butler.storage]` TOML values

### Requirement: Startup Phase Sequence
The daemon implements a phased startup with 17 steps (as documented in the daemon module docstring). Steps are executed sequentially and a failure at any step triggers partial cleanup of already-initialized modules. The blob storage initialization step constructs an `S3BlobStore` using the `[butler.storage]` configuration and validates S3 connectivity (HEAD bucket) before proceeding.

#### Scenario: Full startup succeeds
- **WHEN** all configuration, credentials, database, S3 endpoint, modules, and runtime are valid
- **THEN** the daemon completes all 17 startup phases in order
- **AND** the MCP SSE server is listening on the configured port

#### Scenario: Startup step ordering
- **WHEN** the daemon starts
- **THEN** the phases execute in order: config load, telemetry init, module loading (topo sort), module config validation, env credential validation, DB provisioning, core migrations, module migrations, credential store creation + module credential validation (non-fatal) + core credential validation (fatal), S3 blob store initialization + connectivity check, module on_startup hooks, spawner creation with runtime adapter, schedule sync, MCP tool registration (core then modules then approval gates), SSE server start, switchboard heartbeat, scheduler loop, liveness reporter

#### Scenario: Startup failure triggers module cleanup
- **WHEN** a startup phase fails after some modules have been initialized
- **THEN** all already-initialized modules receive `on_shutdown()` calls in reverse topological order

#### Scenario: S3 unreachable blocks startup
- **WHEN** the S3 endpoint is unreachable during the blob store initialization step
- **THEN** startup SHALL fail with a clear error before proceeding to module initialization

## REMOVED Requirements

### Requirement: blob_storage_dir configuration
**Reason**: Replaced by S3-compatible `[butler.storage]` section. Local filesystem blob storage is no longer supported.
**Migration**: Replace `[butler.storage] blob_dir = "data/blobs"` with `endpoint_url`, `bucket`, `access_key_id`, `secret_access_key`, `region` fields. Run `scripts/migrate_blobs_to_s3.py` to migrate existing blobs.
