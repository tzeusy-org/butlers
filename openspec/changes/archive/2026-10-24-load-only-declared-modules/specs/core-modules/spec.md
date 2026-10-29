## MODIFIED Requirements

### Requirement: Module Abstract Base Class
Every pluggable module MUST subclass `Module` (from `butlers.modules.base`) and implement the three abstract members: `name` (a non-empty class-level string satisfying the abstract name contract), `config_schema` (property returning Pydantic BaseModel class), and `register_tools(mcp, config, db, butler_name)`. Every other hook has a default and is overridden only when needed: `dependencies` (property, default `[]`), `migration_revisions()` (Alembic branch label, default `None`), `on_startup(config, db, credential_store, blob_store)` and `on_shutdown()` (default no-ops), `credentials_env` (property, default `[]`), and `on_all_modules_ready(module_map)` (default no-op).

The `butler_name` parameter is the canonical butler identity string, passed by the daemon from its loaded configuration. Modules MUST NOT derive butler identity from database attributes (`db.schema`, `db.db_name`, or similar). Modules that need identity for tool logic MUST store it from this parameter.

ID: REQ-core-modules-006
Source: bu-qozovd.2 accepted 2026-10-22 two-PR design; 2026-10-21 simplicity audit
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

### Requirement: Module Registry with Auto-Discovery
The `ModuleRegistry` SHALL discover all concrete `Module` subclasses by walking the `butlers.modules` package tree via `pkgutil.walk_packages()`. Modules SHALL be registered by their non-empty class-level `name` string without invoking any constructor. Discovery SHALL import and register classes without constructing modules. Daemon startup SHALL instantiate only the names declared in the butler configuration. Calling default_registry() SHALL explicitly perform complete discovery. Installing on-demand roster import resolution or importing unrelated test infrastructure SHALL NOT perform full discovery. Healthy repeated discovery SHALL preserve concrete classes, sorted names, dependency behavior and the existing declared-only daemon startup boundary. A failed requested module import or different-class duplicate-name collision SHALL be visible rather than yielding an apparently complete registry; a failed newly-created module SHALL NOT remain as a successful cached discovery. The same re-exported class MAY be registered idempotently.

ID: REQ-core-modules-004
Source: bu-qozovd.2 accepted 2026-10-22 constructor-free registration; bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Built-in modules discovered
- **WHEN** `default_registry()` is called
- **THEN** all concrete `Module` subclasses in `butlers.modules.*` are registered
- **AND** `available_modules` returns a sorted list of their names

#### Scenario: Duplicate module name rejected
- **WHEN** `register()` is called with a module class whose class-level `name` matches an already-registered module
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

#### Scenario: Discovery does not construct omitted modules
- **WHEN** explicit discovery encounters a module whose constructor would raise or perform work
- **THEN** its class is registered without invoking its constructor
- **AND** loading a different declared module does not invoke that omitted constructor

### Requirement: Dependency Resolution via Topological Sort
Module loading SHALL order modules by their declared `dependencies` using Kahn's algorithm (in-degree counting). The sort SHALL be deterministic: zero-degree nodes are processed in sorted (alphabetical) order within each batch.

Unknown names, missing declared dependencies and cycles SHALL fail daemon startup before database provisioning, migrations, module startup hooks or tool registration. Constructors of undeclared modules SHALL NOT run to satisfy dependencies.

ID: REQ-core-modules-008
Source: bu-qozovd.2 acceptance criteria 1/2 and accepted 2026-10-22 loader design
Scope: v1-mandatory

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

ID: REQ-core-modules-009
Source: bu-qozovd.2 acceptance criteria 1/2; existing fail-closed declaration boundary
Scope: v1-mandatory

#### Scenario: Unknown module name
- **WHEN** `load_from_config({"nonexistent": {}})` is called
- **THEN** a `ValueError` is raised: `"Unknown module: 'nonexistent'"`

### Requirement: Module Migration Chains
Modules with persistent data SHALL provide non-empty migration directories recognized by the migration runner. The CLI and daemon SHALL select the same module chains from these directories for declared modules, in dependency order, after core and butler-specific migrations. Selection SHALL NOT depend on the legacy optional `migration_revisions()` hook. Chains MUST be deterministic and conflict-free. A chain already run as the butler's own chain SHALL NOT run twice. Module-specific private-schema routing SHALL be preserved.

A roster module whose public name differs from its owning roster directory SHALL resolve that directory's existing migration chain (for example, `home_assistant` in `roster/home/modules` resolves `home`). This mapping SHALL NOT instantiate additional modules or broaden declaration eligibility.

ID: REQ-core-modules-010
Source: bu-qozovd.2 accepted 2026-10-22 migration-directory reconciliation; existing private-schema and HA chain behavior
Scope: v1-mandatory

#### Scenario: Module with migrations
- **WHEN** a declared module has an existing non-empty recognized migration directory
- **THEN** the CLI and daemon select and run that directory's chain after the core and butler chains
- **AND** the module config's private schema override is used when present

#### Scenario: Module without migrations
- **WHEN** a declared module has no recognized migration directory with revision files
- **THEN** no module-specific migrations are run

#### Scenario: Roster alias and own-chain deduplication
- **WHEN** Home Assistant is declared by Health with its dependencies
- **THEN** its existing `home` chain is selected for Health's schema
- **AND** when Home declares the same module its own `home` chain runs only once

### Requirement: Load-All Module Loading
The `load_all()` method SHALL instantiate ALL registered modules regardless of `butler.toml` config presence. Modules listed in config receive their explicit config dict; unconfigured modules receive `{}`. This explicit utility SHALL remain separate from daemon startup. The daemon SHALL call `load_from_config()` and construct only explicitly declared module names, including modules without a config schema, before validation, migrations, startup hooks and tool registration. Discovery SHALL NOT construct or admit undeclared modules. The declared-only loader SHALL reject unknown configured names before provisioning or any module migration, startup or registration effect. Existing failed/cascade-failed and runtime enabled-state semantics SHALL remain separate from declaration eligibility.

ID: REQ-core-modules-002
Source: bu-qozovd.2 accepted 2026-10-22 Load-All amendment; heart-and-soul v1 opt-in modules; bu-1fe7xv declaration and failure-isolation boundary
Scope: v1-mandatory

#### Scenario: Unconfigured module loaded with empty config
- **WHEN** `load_all(modules_config)` is called and a registered module is not in `modules_config`
- **THEN** the module is instantiated with an empty dict `{}` as its config

#### Scenario: Omitted modules do not start regardless of schema shape
- **WHEN** a registered module with no config schema or an all-default or required-field schema is omitted from the butler's module declarations
- **THEN** neither registry discovery nor daemon startup invokes its constructor
- **AND** daemon startup does not invoke its migrations, startup hook or tool registration

#### Scenario: Actual roster startup respects declarations
- **WHEN** a healthy daemon starts from any current roster's valid explicit module configuration
- **THEN** its startup hook-invocation membership equals that roster's declared module names in dependency order
- **AND** module health failures and user-disabled state remain explicitly reported rather than becoming undeclared startup admission
