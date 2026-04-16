# Module System

## Purpose
Defines the pluggable module architecture for butlers: the Module abstract base class, automatic discovery and registration, topological dependency resolution, Pydantic config schema validation, tool registration, migration chains, and runtime enable/disable state management.

## Requirements

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

### Requirement: Tool Metadata for Approvals
Modules may override `tool_metadata()` to return `dict[str, ToolMeta]` mapping tool names to `ToolMeta(arg_sensitivities)` instances. This declares which tool arguments are safety-critical for the approvals subsystem.

#### Scenario: Module declares sensitive arguments
- **WHEN** a module returns `{"my_tool": ToolMeta(arg_sensitivities={"password": True})}` from `tool_metadata()`
- **THEN** the approvals subsystem uses these declarations for sensitivity classification

#### Scenario: No metadata declared
- **WHEN** a module does not override `tool_metadata()`
- **THEN** an empty dict is returned and the approvals subsystem falls back to heuristic classification

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

### Requirement: Tool Group Filtering

Modules MAY partition their tools into named groups. When a butler's `butler.toml` specifies `groups = [...]` under a module section, only tools belonging to listed groups are registered. When `groups` is absent or empty, all groups are enabled (backwards compatible — existing configs are unaffected).

#### Contract: ToolGroupMixin

`ToolGroupMixin` is a Pydantic `BaseModel` mixin that adds a `groups: list[str] | None = None` field. Module config classes that support group filtering inherit from it:

```python
class MyModuleConfig(ToolGroupMixin, BaseModel):
    some_setting: str = "default"
```

#### Contract: group_enabled utility

`group_enabled(config, group) -> bool` returns `True` when `config` has no `groups` attribute, or `groups` is `None` or empty. Otherwise it returns `True` only if `group` is in the list. The function accepts any object — configs without the mixin always pass.

#### Contract: _tool(group) decorator pattern

Inside `register_tools()`, modules define a local `_tool(group)` helper that returns `mcp.tool()` when the group is enabled, or a no-op passthrough (`lambda fn: fn`) when disabled. Tools are then decorated with `@_tool("group_name")` instead of `@mcp.tool()`:

```python
def _tool(group: str):
    if group_enabled(config, group):
        return mcp.tool()
    return lambda fn: fn

@_tool("core")
async def my_tool(...): ...
```

#### Contract: Group taxonomy documentation

Each module config class that uses `ToolGroupMixin` MUST document its group taxonomy in the class docstring under a `Tool groups` section listing each group name and its member tools.

#### Scenario: Groups absent — all tools registered
- **WHEN** `butler.toml` does not specify `groups` for a module (or specifies `groups = []`)
- **THEN** all tool groups are enabled and every tool is registered

#### Scenario: Groups restrict tool registration
- **WHEN** `butler.toml` specifies `groups = ["core", "entity"]` for the memory module
- **THEN** only tools in the `core` and `entity` groups are registered
- **AND** tools in `feedback`, `preferences`, `admin` groups are skipped

#### Scenario: Config without mixin passes unconditionally
- **WHEN** `group_enabled()` is called with a config object that does not have a `groups` attribute
- **THEN** it returns `True` (all groups enabled)

#### Modules with group support

The following modules implement `ToolGroupMixin` on their config class:

| Module | Config class | Example groups |
|---|---|---|
| memory | `MemoryModuleConfig` | core, feedback, entity, preferences, admin |
| calendar | `CalendarConfig` | core, butler_events, attendees |
| relationship | `RelationshipModuleConfig` | (see config docstring) |
| finance | `FinanceModuleConfig` | (see config docstring) |
| education | `EducationModuleConfig` | (see config docstring) |
| health | `HealthModuleConfig` | (see config docstring) |
| home_assistant | `HomeAssistantConfig` | (see config docstring) |
| approvals | `ApprovalsConfig` | (see config docstring) |

#### butler.toml syntax

```toml
[modules.memory]
groups = ["core", "entity"]

[modules.calendar]
groups = ["core"]
```

### Requirement: Channel Egress Ownership Enforcement
Non-messenger butlers are prohibited from registering channel egress tools (matching `<channel>_(send_message|reply_to_message|send_email|reply_to_thread)`). A `ChannelEgressOwnershipError` is raised if a non-messenger butler attempts this.

#### Scenario: Non-messenger egress tool rejected
- **WHEN** a non-messenger butler's module registers a tool matching the channel egress pattern (e.g., `telegram_send_message`, `email_send_message`)
- **THEN** a `ChannelEgressOwnershipError` is raised at startup
