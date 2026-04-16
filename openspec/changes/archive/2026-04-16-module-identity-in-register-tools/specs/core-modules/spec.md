## MODIFIED Requirements

### Requirement: Module Abstract Base Class
Every pluggable module must subclass `Module` (from `butlers.modules.base`) and implement all abstract members: `name` (property), `config_schema` (property returning Pydantic BaseModel class), `dependencies` (property returning list of module names), `register_tools(mcp, config, db, butler_name)`, `migration_revisions()` (returns Alembic branch label or None), `on_startup(config, db, credential_store)`, and `on_shutdown()`.

The `butler_name` parameter is the canonical butler identity string, passed by the daemon from its loaded configuration. Modules MUST NOT derive butler identity from database attributes (`db.schema`, `db.db_name`, or similar). Modules that need identity for tool logic MUST store it from this parameter.

#### Scenario: Concrete module implements all abstract members
- **WHEN** a module subclass implements all required abstract properties and methods including the `butler_name` parameter on `register_tools`
- **THEN** it can be instantiated and registered in the module registry

#### Scenario: Incomplete module implementation fails
- **WHEN** a module subclass omits one or more abstract members
- **THEN** instantiation raises `TypeError` (Python ABC enforcement)

#### Scenario: Daemon passes butler identity to register_tools
- **WHEN** the daemon calls `register_tools` on each active module during startup phase 13
- **THEN** it passes `self.config.name` as the `butler_name` parameter
- **AND** the value matches the butler's configured identity from `butler.toml`

#### Scenario: Module receives correct identity in one-db topology
- **WHEN** multiple butlers share a single database with per-butler schemas
- **AND** the daemon calls `register_tools` on the finance butler's calendar module
- **THEN** `butler_name` is `"finance"`, not the shared database name `"butlers"`

#### Scenario: Module must not derive identity from database
- **WHEN** a module needs to know its butler's name during tool registration
- **THEN** it MUST use the `butler_name` parameter
- **AND** it MUST NOT read `db.schema`, `db.db_name`, or any other database attribute for identity resolution

## ADDED Requirements

### Requirement: wire_runtime excludes butler identity
The optional `wire_runtime()` method on modules that need runtime dependencies (spawner, repo_root, switchboard_client) MUST NOT include `butler_name` as a parameter. Butler identity is provided exclusively through `register_tools()`.

#### Scenario: wire_runtime signature excludes butler_name
- **WHEN** a module defines `wire_runtime()`
- **THEN** its signature accepts `spawner`, `repo_root`, and optional keyword arguments (e.g., `switchboard_client`, `notify_fn`)
- **AND** it does not accept `butler_name`

#### Scenario: Module identity available before wire_runtime
- **WHEN** a module stores `butler_name` from `register_tools()` (phase 13)
- **AND** `wire_runtime()` is called later (phase 13d)
- **THEN** the module already has its identity and can use it in runtime wiring logic
