## MODIFIED Requirements

### Requirement: Module Abstract Base Class
Every pluggable module MUST subclass `Module` (from `butlers.modules.base`) and implement the three abstract members: `name` (property), `config_schema` (property returning Pydantic BaseModel class), and `register_tools(mcp, config, db, butler_name)`. Every other hook has a default and is overridden only when needed: `dependencies` (property, default `[]`), `migration_revisions()` (Alembic branch label, default `None`), `on_startup(config, db, credential_store, blob_store)` and `on_shutdown()` (default no-ops), `credentials_env` (property, default `[]`), and `on_all_modules_ready(module_map)` (default no-op).

The `butler_name` parameter is the canonical butler identity string, passed by the daemon from its loaded configuration. Modules MUST NOT derive butler identity from database attributes (`db.schema`, `db.db_name`, or similar). Modules that need identity for tool logic MUST store it from this parameter.

ID: REQ-core-modules-006
Source: bu-qozovd.2; 2026-10-21 simplicity audit
Scope: v1-mandatory

#### Scenario: Concrete module implements all abstract members
- **WHEN** a module subclass implements all required abstract properties and methods including the `butler_name` parameter on `register_tools`
- **THEN** it can be instantiated and registered in the module registry

#### Scenario: Optional hooks have defaults
- **WHEN** a module subclass implements only `name`, `config_schema` and `register_tools`
- **THEN** it can be instantiated
- **AND** `dependencies` is `[]`, `migration_revisions()` is `None`, and `on_startup` and `on_shutdown` do nothing

#### Scenario: Incomplete module implementation fails
- **WHEN** a module subclass omits `name`, `config_schema` or `register_tools`
- **THEN** instantiation raises `TypeError` (Python ABC enforcement)

#### Scenario: Daemon passes butler identity to register_tools
- **WHEN** the daemon calls `register_tools` on each active module during startup phase 14
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

### Requirement: Tool Group Filtering

Modules MAY partition their tools into named groups. When a butler's `butler.toml` specifies `groups = [...]` under a module section, only tools belonging to listed groups SHALL be registered. When `groups` is absent or empty, all groups are enabled (backwards compatible — existing configs are unaffected).

ID: REQ-core-modules-007
Source: bu-qozovd.2; 2026-10-21 simplicity audit
Scope: v1-mandatory

#### Contract: ToolGroupMixin

`ToolGroupMixin` is a Pydantic `BaseModel` mixin that adds a `groups: list[str] | None = None` field. Module config classes that support group filtering inherit from it:

```python
class MyModuleConfig(ToolGroupMixin, BaseModel):
    some_setting: str = "default"
```

#### Contract: group_enabled utility

`group_enabled(config, group) -> bool` returns `True` when `config` has no `groups` attribute, or `groups` is `None` or empty. Otherwise it returns `True` only if `group` is in the list. The function accepts any object — configs without the mixin always pass.

#### Contract: _tool(group) decorator pattern

Inside `register_tools()`, modules build one group-aware decorator with `tool_registrar(mcp, groups)` (from `butlers.modules.base`). The second argument is the enabled group list or a module config carrying `groups`. `@_tool("group")` returns `mcp.tool()` when the group is enabled, or a no-op passthrough when disabled, so the function is defined but not registered. Modules do not define their own `_tool` helper:

```python
_tool = tool_registrar(mcp, config)

@_tool("core")
async def my_tool(...): ...
```

#### Contract: guarded_tool(credential=, missing=, errors=)

Tools that need a credential and map provider errors decorate with `guarded_tool` (from `butlers.modules.base`) instead of repeating the check and a broad `except` per tool. `credential` returns whether the tool can run (evaluated per call), `missing` builds the response when it cannot, and `errors` maps an exception raised by the tool to its response (without it exceptions propagate). `credential` and `missing` are given together or not at all. The wrapper keeps the tool's name, docstring and signature.

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
