# Module System

## Purpose
Defines the pluggable module architecture for butlers: the Module abstract base class, automatic discovery and registration, topological dependency resolution, Pydantic config schema validation, tool registration, migration chains, and runtime enable/disable state management.

## Requirements

### Requirement: Module Abstract Base Class
Every pluggable module MUST subclass `Module` (from `butlers.modules.base`) and implement all abstract members: `name` (property), `config_schema` (property returning Pydantic BaseModel class), `dependencies` (property returning list of module names), `register_tools(mcp, config, db, butler_name)`, `migration_revisions()` (returns Alembic branch label or None), `on_startup(config, db, credential_store)`, and `on_shutdown()`.

The `butler_name` parameter is the canonical butler identity string, passed by the daemon from its loaded configuration. Modules MUST NOT derive butler identity from database attributes (`db.schema`, `db.db_name`, or similar). Modules that need identity for tool logic MUST store it from this parameter.

#### Scenario: Concrete module implements all abstract members
- **WHEN** a module subclass implements all required abstract properties and methods including the `butler_name` parameter on `register_tools`
- **THEN** it can be instantiated and registered in the module registry

#### Scenario: Incomplete module implementation fails
- **WHEN** a module subclass omits one or more abstract members
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

### Requirement: Tool Metadata for Approvals
Modules MAY override `tool_metadata()` to return `dict[str, ToolMeta]` mapping tool names to `ToolMeta(arg_sensitivities)` instances. When overridden, the returned mapping SHALL declare which tool arguments are safety-critical for the approvals subsystem.

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
- **WHEN** a module stores `butler_name` from `register_tools()` (phase 14)
- **AND** `wire_runtime()` is called later in phase 14, after the Switchboard connection is established in phase 12
- **THEN** the module already has its identity and can use it in runtime wiring logic

### Requirement: Module Registry with Auto-Discovery
The `ModuleRegistry` SHALL discover all concrete `Module` subclasses by walking the `butlers.modules` package tree via `pkgutil.walk_packages()`. Modules SHALL be registered by class, then instantiated when a butler's configuration is loaded. Calling default_registry() SHALL explicitly perform complete discovery. Installing on-demand roster import resolution or importing unrelated test infrastructure SHALL NOT perform full discovery. Healthy repeated discovery SHALL preserve concrete classes, sorted names, dependency behavior and the existing declared-only daemon startup boundary. A failed requested module import or different-class duplicate-name collision SHALL be visible rather than yielding an apparently complete registry; a failed newly-created module SHALL NOT remain as a successful cached discovery. The same re-exported class MAY be registered idempotently.

ID: REQ-core-modules-004
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Built-in modules discovered
- **WHEN** `default_registry()` is called
- **THEN** all concrete `Module` subclasses in `butlers.modules.*` are registered
- **AND** `available_modules` returns a sorted list of their names

#### Scenario: Duplicate module name rejected
- **WHEN** `register()` is called with a module class whose `name` property matches an already-registered module
- **THEN** a `ValueError` is raised

#### Scenario: Unrelated import does not discover all modules
- **WHEN** only the roster resolver or root test infrastructure is imported
- **THEN** roster modules are not executed solely to populate a global registry
- **AND** an explicit subsequent default_registry call discovers the full healthy module set

#### Scenario: Broken explicit discovery is visible and retryable
- **WHEN** a module body fails during an explicit discovery request
- **THEN** the failure names the source module and cannot be represented as complete healthy discovery
- **AND** repairing the source permits a clean retry without a partial cached-success entry
- **AND** ordinary post-discovery module startup failure isolation remains governed separately

### Requirement: Dependency Resolution via Topological Sort
Module loading SHALL order modules by their declared `dependencies` using Kahn's algorithm (in-degree counting). The sort SHALL be deterministic: zero-degree nodes are processed in sorted (alphabetical) order within each batch.

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
When `modules_config` references a module name that is not registered in the registry, startup SHALL fail with a `ValueError`.

#### Scenario: Unknown module name
- **WHEN** `load_from_config({"nonexistent": {}})` is called
- **THEN** a `ValueError` is raised: `"Unknown module: 'nonexistent'"`

### Requirement: Config Schema Validation
Each module's `config_schema` is a Pydantic BaseModel class. The daemon SHALL validate module configuration against this schema at startup. Invalid configuration SHALL produce a `ModuleConfigError` with structured validation details.

#### Scenario: Valid module config
- **WHEN** a module's configuration matches its `config_schema`
- **THEN** validation passes and the module proceeds to startup

#### Scenario: Invalid module config
- **WHEN** a module's configuration contains unknown fields or missing required fields
- **THEN** Pydantic `ValidationError` is caught and reported as a startup error for that module

### Requirement: Module Migration Chains
Modules with persistent data SHALL provide an Alembic branch label via `migration_revisions()`. Migration chains are run at daemon startup after core migrations. Chains MUST be deterministic and conflict-free.

#### Scenario: Module with migrations
- **WHEN** a module returns a non-None Alembic branch label from `migration_revisions()`
- **THEN** the daemon runs Alembic migrations for that branch at startup

#### Scenario: Module without migrations
- **WHEN** a module returns `None` from `migration_revisions()`
- **THEN** no module-specific migrations are run

### Requirement: Module Startup and Shutdown Hooks
Module `on_startup(config, db, credential_store)` SHALL be called in topological order after migrations. Module `on_shutdown()` SHALL be called in reverse topological order during daemon shutdown.

#### Scenario: Startup in dependency order
- **WHEN** multiple modules are loaded
- **THEN** `on_startup()` is called for each module in topological order (dependencies first)

#### Scenario: Shutdown in reverse order
- **WHEN** the daemon shuts down
- **THEN** `on_shutdown()` is called for each module in reverse topological order

### Requirement: Load-All Module Loading
The `load_all()` method SHALL instantiate ALL registered modules regardless of `butler.toml` config presence. Modules listed in config receive their explicit config dict; unconfigured modules receive `{}`. Discovery SHALL NOT admit a module to daemon startup: the daemon SHALL select only explicitly declared module names, including modules without a config schema, before validation, migrations, startup hooks and tool registration. The selector SHALL reject unknown configured names before provisioning or any module migration, startup or registration effect. Existing failed/cascade-failed and runtime enabled-state semantics SHALL remain separate from declaration eligibility.

ID: REQ-core-modules-002
Source: heart-and-soul v1 opt-in modules; bu-1fe7xv round 2; src/butlers/daemon.py _select_startup_modules correction
Scope: v1-mandatory

#### Scenario: Unconfigured module loaded with empty config
- **WHEN** `load_all(modules_config)` is called and a registered module is not in `modules_config`
- **THEN** the module is instantiated with an empty dict `{}` as its config

#### Scenario: Omitted modules do not start regardless of schema shape
- **WHEN** a registered module with no config schema or an all-default or required-field schema is omitted from the butler's module declarations
- **THEN** registry discovery still instantiates it
- **AND** daemon startup does not invoke its migrations, startup hook or tool registration

#### Scenario: Actual roster startup respects declarations
- **WHEN** a healthy daemon starts from any current roster's valid explicit module configuration
- **THEN** its startup hook-invocation membership equals that roster's declared module names in dependency order
- **AND** module health failures and user-disabled state remain explicitly reported rather than becoming undeclared startup admission

### Requirement: Tool Group Filtering

Modules MAY partition their tools into named groups. When a butler's `butler.toml` specifies `groups = [...]` under a module section, only tools belonging to listed groups SHALL be registered. When `groups` is absent or empty, all groups are enabled (backwards compatible — existing configs are unaffected).

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
Non-messenger butlers SHALL NOT register channel egress tools (matching `<channel>_(send_message|reply_to_message|send_email|reply_to_thread)`). A `ChannelEgressOwnershipError` SHALL be raised if a non-messenger butler attempts this.

#### Scenario: Non-messenger egress tool rejected
- **WHEN** a non-messenger butler's module registers a tool matching the channel egress pattern (e.g., `telegram_send_message`, `email_send_message`)
- **THEN** a `ChannelEgressOwnershipError` is raised at startup

### Requirement: MCP Tools Raise on Invalid Input
MCP tools registered by modules SHALL raise an exception on invalid input rather than returning a success-shaped empty payload. "Invalid input" includes (but is not limited to): required arguments missing or `null`, arguments of the wrong type, and values that fail schema validation (e.g. an empty string where a non-empty name is required). The exception SHALL be one that the runtime adapter renders as a tool-call error to the agent, not a silent empty result.

The motivating failure mode: a looping agent that invokes a tool with `null` arguments must see a typed error, not an empty list. Returning an empty list makes the failing call indistinguishable from a well-formed call that simply matched nothing, and rewards the agent for retrying.

This rule is cross-cutting: it applies to every MCP tool in every module. It establishes the normative contract so new tools comply by construction; existing tools are brought into compliance incrementally as they are touched (the `memory_entity_resolve` tool that triggered the motivating incident already complies — see `module-memory`).

#### Scenario: Missing required argument raises
- **WHEN** a module's MCP tool is invoked with a required argument missing or set to `null`
- **THEN** the tool SHALL raise an exception (e.g. `ValueError`, `TypeError`, or a module-specific `InvalidInputError`)
- **AND** SHALL NOT return an empty-success payload such as `[]`, `{}`, or `None`

#### Scenario: Empty-success payload is reserved for well-formed no-match
- **WHEN** a search-style MCP tool is invoked with valid, well-formed arguments that simply do not match any record
- **THEN** the tool MAY return an empty collection (e.g. `[]`) to indicate "no results for a valid query"
- **AND** this is the ONLY condition under which an empty-success payload is permitted

#### Scenario: Raised exception surfaces as a tool-call error to the agent
- **WHEN** a tool raises per this requirement
- **THEN** the runtime adapter SHALL surface the exception as a failed tool call in the session's event stream (not as a silent empty result)
- **AND** the spawner's degenerate-loop detector SHALL still treat identical failing calls as identical for loop-detection purposes

### Requirement: Module Tool Naming Convention
Module MCP tool names SHALL use the plain `<channel>_<action>` format (e.g. `telegram_send_message`, `email_send_message`). No `user_*` / `bot_*` prefix convention applies: the daemon SHALL NOT wrap module tools in a per-audience (user vs. bot) I/O model. Accordingly, the `Module` ABC SHALL NOT define `user_inputs()`, `user_outputs()`, `bot_inputs()`, or `bot_outputs()` descriptor methods, and the daemon SHALL NOT carry a tool-I/O validation layer — the `ToolIODescriptor` dataclass, `_validate_tool_name()`, `_validate_module_io_descriptors()`, `_is_user_send_or_reply_tool()`, `_with_default_gated_user_outputs()`, `_CHANNEL_EGRESS_ACTIONS`, and `ModuleToolValidationError` are absent and SHALL NOT be reintroduced.

#### Scenario: Tool registered with plain name
- **WHEN** the Telegram module registers a send tool
- **THEN** the tool MUST be named `telegram_send_message` (not `user_telegram_send_message` or `bot_telegram_send_message`)

#### Scenario: Module ABC does not require descriptor methods
- **WHEN** a module class implements the `Module` ABC
- **THEN** it MUST NOT be required to implement `user_inputs()`, `user_outputs()`, `bot_inputs()`, or `bot_outputs()`

### Requirement: QA Error Relay Module
Every roster butler SHALL declare the self_healing module and make its shared self-healing guidance reachable. The module SHALL register report_error and read-only get_healing_status, relay error reports only through Switchboard MCP routing to QA, and perform no local investigation dispatch, redispatch, worktree or shared recovery operation. Relay acceptance SHALL mean actual target reception rather than investigation or publication. Existing canonical identity, sensitive metadata, configuration and independent QA ownership SHALL remain authoritative.

ID: REQ-core-modules-003
Source: accepted RFC 0015 centralized pipeline; roster/qa/MANIFESTO.md; bu-1fe7xv rounds 2-4; archived qa-staffer D3 non-blocking relay contract
Scope: v1-mandatory

#### Scenario: Every roster exposes declared relay tools and guidance
- **WHEN** any current roster butler starts with healthy declared self_healing configuration
- **THEN** report_error and get_healing_status are registered under its canonical daemon identity and its shared skill is reachable
- **AND** retry_healing is not registered by this relay module
- **AND** report_error error_message, traceback and context keep their sensitive metadata

#### Scenario: Accepted QA reception is relayed truthfully
- **WHEN** registered report_error receives valid structured input and QA accepts its Switchboard-routed report_finding call
- **THEN** the reporter returns accepted=true with its fingerprint and a relay-reception message
- **AND** the existing fingerprint/severity computation, allow_stale=true and optional context omission are preserved
- **AND** no local investigation or claim of a dispatched agent, PR or deployed fix is produced

#### Scenario: Wrapped target rejection is not acceptance
- **WHEN** actual MCP routing returns a tool error, rejected target acceptance, empty or malformed target payload, or route error
- **THEN** report_error returns accepted=false with an explicit unavailable or rejected outcome
- **AND** it creates no local investigation or automatic ambiguous retry

#### Scenario: Missing QA or Switchboard remains bounded and visible
- **WHEN** the Switchboard client, QA registration or registry query is unavailable, or a relay exceeds its deadline
- **THEN** report_error returns accepted=false within the overall two-second relay budget
- **AND** ordinary caller work and error evidence remain available without per-butler fallback

#### Scenario: Local active history cannot suppress centralized relay
- **WHEN** a valid report matches an existing legacy active healing attempt and QA can receive it
- **THEN** report_error still relays the finding to QA's authoritative triage boundary
- **AND** it does not locally create, mutate or redispatch a healing attempt

#### Scenario: Disabled and legacy configuration remain compatible
- **WHEN** relay admission is disabled or legacy threshold keys are supplied
- **THEN** disabled admission gives an explicit rejection without routing
- **AND** accepted legacy dispatch threshold keys cannot alter QA policy or enable a local dispatcher

#### Scenario: Relay startup and shutdown do not run shared investigation cleanup
- **WHEN** a declared relay module starts or shuts down with an available pool
- **THEN** it performs no shared attempt recovery, worktree reaping or watchdog dispatch
- **AND** the central QA lifecycle and shared utility interfaces remain unchanged

#### Scenario: Switchboard self-report uses its existing MCP route tools
- **WHEN** Switchboard's declared relay reports an error without an external self-client
- **THEN** it calls its existing list_butlers and route MCP tools through the bounded local FastMCP client
- **AND** the QA call follows the same normalization, identity and non-dispatch rules

#### Scenario: QA self-report preserves the recursion barrier
- **WHEN** QA's relay reports a QA-origin finding with unknown or QA/healing source trigger provenance
- **THEN** the existing QA meta/self-recursion handling remains authoritative
- **AND** the relay creates no local recursive investigation

#### Scenario: Read-only status is history rather than deployment proof
- **WHEN** get_healing_status queries a fingerprint or the reporter's recent attempts
- **THEN** it preserves the existing read-only status query behavior
- **AND** relay guidance distinguishes reception, investigation, PR merge and deployment rather than treating a merged PR as a deployed fix
