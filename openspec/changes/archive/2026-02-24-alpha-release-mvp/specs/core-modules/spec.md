# Module System

## Purpose
Defines the pluggable module architecture for butlers: the Module abstract base class, automatic discovery and registration, topological dependency resolution, Pydantic config schema validation, tool registration, migration chains, and runtime enable/disable state management.

## ADDED Requirements

### Requirement: Module Abstract Base Class
Every pluggable module must subclass `Module` (from `butlers.modules.base`) and implement all abstract members: `name` (property), `config_schema` (property returning Pydantic BaseModel class), `dependencies` (property returning list of module names), `register_tools(mcp, config, db)`, `migration_revisions()` (returns Alembic branch label or None), `on_startup(config, db, credential_store)`, and `on_shutdown()`.

#### Scenario: Concrete module implements all abstract members
- **WHEN** a module subclass implements all required abstract properties and methods
- **THEN** it can be instantiated and registered in the module registry

#### Scenario: Incomplete module implementation fails
- **WHEN** a module subclass omits one or more abstract members
- **THEN** instantiation raises `TypeError` (Python ABC enforcement)

### Requirement: Tool Metadata for Approvals
Modules may override `tool_metadata()` to return `dict[str, ToolMeta]` mapping tool names to `ToolMeta(arg_sensitivities)` instances. This declares which tool arguments are safety-critical for the approvals subsystem.

#### Scenario: Module declares sensitive arguments
- **WHEN** a module returns `{"my_tool": ToolMeta(arg_sensitivities={"password": True})}` from `tool_metadata()`
- **THEN** the approvals subsystem uses these declarations for sensitivity classification

#### Scenario: No metadata declared
- **WHEN** a module does not override `tool_metadata()`
- **THEN** an empty dict is returned and the approvals subsystem falls back to heuristic classification

### Requirement: Module Registry with Auto-Discovery
The `ModuleRegistry` discovers all concrete `Module` subclasses by walking the `butlers.modules` package tree via `pkgutil.walk_packages()`. Modules are registered by class, then instantiated when a butler's configuration is loaded.

#### Scenario: Built-in modules discovered
- **WHEN** `default_registry()` is called
- **THEN** all concrete `Module` subclasses in `butlers.modules.*` are registered
- **AND** `available_modules` returns a sorted list of their names

#### Scenario: Duplicate module name rejected
- **WHEN** `register()` is called with a module class whose `name` property matches an already-registered module
- **THEN** a `ValueError` is raised

### Requirement: Dependency Resolution via Topological Sort
Module loading orders modules by their declared `dependencies` using Kahn's algorithm (in-degree counting). The sort is deterministic: zero-degree nodes are processed in sorted (alphabetical) order within each batch.

#### Scenario: Dependencies ordered correctly
- **WHEN** module A depends on module B
- **THEN** `load_from_config()` returns module B before module A

#### Scenario: Cycle detection
- **WHEN** module A depends on module B and module B depends on module A
- **THEN** `_topological_sort()` raises `ValueError` identifying the cycle

#### Scenario: Missing dependency
- **WHEN** module A depends on module C but C is not in the enabled set
- **THEN** `load_from_config()` raises `ValueError` stating C is not in the enabled module set

### Requirement: Unknown Module Names Block Startup
When `modules_config` references a module name that is not registered in the registry, startup fails with a `ValueError`.

#### Scenario: Unknown module name
- **WHEN** `load_from_config({"nonexistent": {}})` is called
- **THEN** a `ValueError` is raised: `"Unknown module: 'nonexistent'"`

### Requirement: Config Schema Validation
Each module's `config_schema` is a Pydantic BaseModel class. The daemon validates module configuration against this schema at startup. Invalid configuration produces a `ModuleConfigError` with structured validation details.

#### Scenario: Valid module config
- **WHEN** a module's configuration matches its `config_schema`
- **THEN** validation passes and the module proceeds to startup

#### Scenario: Invalid module config
- **WHEN** a module's configuration contains unknown fields or missing required fields
- **THEN** Pydantic `ValidationError` is caught and reported as a startup error for that module

### Requirement: Module Migration Chains
Modules with persistent data provide an Alembic branch label via `migration_revisions()`. Migration chains are run at daemon startup after core migrations. Chains must be deterministic and conflict-free.

#### Scenario: Module with migrations
- **WHEN** a module returns a non-None Alembic branch label from `migration_revisions()`
- **THEN** the daemon runs Alembic migrations for that branch at startup

#### Scenario: Module without migrations
- **WHEN** a module returns `None` from `migration_revisions()`
- **THEN** no module-specific migrations are run

### Requirement: Module Startup and Shutdown Hooks
Module `on_startup(config, db, credential_store)` is called in topological order after migrations. Module `on_shutdown()` is called in reverse topological order during daemon shutdown.

#### Scenario: Startup in dependency order
- **WHEN** multiple modules are loaded
- **THEN** `on_startup()` is called for each module in topological order (dependencies first)

#### Scenario: Shutdown in reverse order
- **WHEN** the daemon shuts down
- **THEN** `on_shutdown()` is called for each module in reverse topological order

### Requirement: Load-All Module Loading
The `load_all()` method instantiates ALL registered modules regardless of `butler.toml` config presence. Modules listed in config receive their explicit config dict; unconfigured modules receive `{}`. This enables runtime enable/disable management independent of static config.

#### Scenario: Unconfigured module loaded with empty config
- **WHEN** `load_all(modules_config)` is called and a registered module is not in `modules_config`
- **THEN** the module is instantiated with an empty dict `{}` as its config

### Requirement: Channel Egress Ownership Enforcement
Non-messenger butlers are prohibited from registering channel egress tools (matching `<channel>_(send_message|reply_to_message|send_email|reply_to_thread)`). A `ChannelEgressOwnershipError` is raised if a non-messenger butler attempts this.

#### Scenario: Non-messenger egress tool rejected
- **WHEN** a non-messenger butler's module registers a tool matching the channel egress pattern (e.g., `telegram_send_message`, `email_send_message`)
- **THEN** a `ChannelEgressOwnershipError` is raised at startup
