# Module System

The module system enables butlers to gain domain-specific capabilities through pluggable units. Each module implements a defined abstract base class, registers MCP tools on the butler's FastMCP server, and manages its own database tables. Modules are opt-in per butler via `butler.toml` configuration, and their dependencies are resolved automatically at startup.

## ADDED Requirements

### Requirement: Module Abstract Base Class

Every module SHALL implement the `Module` abstract base class (ABC). The ABC defines the contract between a module and the butler runtime.

The `Module` ABC SHALL declare the following abstract members:

- `name` — a `str` property uniquely identifying the module (e.g., `"email"`, `"telegram"`)
- `config_schema` — a `type[BaseModel]` property returning the Pydantic model class that describes this module's configuration shape
- `dependencies` — a `list[str]` property returning the names of other modules this module depends on
- `register_tools(mcp, config, db)` — an async method that registers MCP tools on the butler's FastMCP server; `mcp` is the FastMCP instance, `config` is the validated module config (instance of `config_schema`), and `db` is the asyncpg database connection pool
- `migration_revisions()` — a method returning `str | None` — the Alembic branch label for the module's migration revisions (e.g., `"email"`), corresponding to revisions in `alembic/versions/<module-name>/`, or `None` if no migrations are needed
- `on_startup(config, db)` — an async method called after dependency resolution and Alembic migration application; `config` is the validated module config, `db` is the asyncpg connection pool
- `on_shutdown()` — an async method called during butler shutdown

A module implementation MUST provide concrete implementations for all abstract members. A module that has no dependencies SHALL return an empty list from `dependencies`. A module that requires no migrations SHALL return `None` from `migration_revisions()`.

#### Scenario: Implementing a minimal module

WHEN a developer creates a new module class that inherits from the `Module` ABC
AND provides concrete implementations for `name`, `config_schema`, `dependencies`, `register_tools`, `migration_revisions`, `on_startup`, and `on_shutdown`
THEN the module class SHALL be instantiable without errors
AND the module SHALL be usable by the module registry.

#### Scenario: Failing to implement a required member

WHEN a developer creates a class that inherits from `Module`
AND omits the implementation of one or more abstract members
THEN instantiating the class SHALL raise a `TypeError` at construction time.

#### Scenario: Module with no dependencies

WHEN a module's `dependencies` property returns an empty list
THEN the module SHALL have no ordering constraints relative to other independent modules during startup.

#### Scenario: Module with no migrations

WHEN a module's `migration_revisions()` method returns `None`
THEN the butler SHALL skip Alembic migration application for that module and proceed to startup without error.

---

### Requirement: Module Registry Discovery

The module registry SHALL collect all available module classes and make them available for loading. The registry MUST maintain a mapping from module name to module class.

#### Scenario: Registering available modules

WHEN the butler runtime initializes the module registry
THEN the registry SHALL discover all concrete `Module` subclasses that have been registered
AND each module SHALL be indexed by its `name` property.

#### Scenario: Duplicate module names

WHEN two module classes declare the same `name` value
THEN the registry SHALL raise a `ValueError` at discovery time with a message identifying the conflicting module name.

#### Scenario: Querying available modules

WHEN the registry is queried for available module names
THEN it SHALL return the complete list of registered module names.

---

### Requirement: Module Loading from butler.toml

The module registry SHALL load only the modules enabled in the butler's `butler.toml` configuration file. Each enabled module corresponds to a `[modules.<name>]` section in the TOML file.

#### Scenario: Loading enabled modules

WHEN a `butler.toml` file contains `[modules.email]` and `[modules.telegram]` sections
AND the registry has module classes registered for `"email"` and `"telegram"`
THEN the registry SHALL load exactly those two modules
AND no other registered modules SHALL be loaded.

#### Scenario: Requesting an unknown module

WHEN a `butler.toml` file contains a `[modules.nonexistent]` section
AND no module class with `name = "nonexistent"` is registered
THEN the registry SHALL raise a `ValueError` at startup with a message indicating that the module `"nonexistent"` is not available.

#### Scenario: No modules configured

WHEN a `butler.toml` file contains no `[modules.*]` sections
THEN the butler SHALL start with zero modules loaded
AND only core MCP tools SHALL be available.

---

### Requirement: Module Configuration Validation

Each module's configuration SHALL be validated against that module's `config_schema` (a Pydantic `BaseModel` subclass). The configuration data comes from the corresponding `[modules.<name>]` section in `butler.toml`.

#### Scenario: Valid module configuration

WHEN a `butler.toml` contains a `[modules.email]` section with keys matching the `email` module's `config_schema`
THEN the registry SHALL parse and validate the configuration into an instance of the module's `config_schema`
AND the validated config instance SHALL be passed to `register_tools` and `on_startup`.

#### Scenario: Missing required configuration field

WHEN a `butler.toml` `[modules.email]` section omits a required field defined in the email module's `config_schema`
THEN the registry SHALL raise a validation error at startup before calling `on_startup`
AND the error message SHALL identify the missing field and the module name.

#### Scenario: Extra unknown configuration field

WHEN a `butler.toml` `[modules.email]` section contains a field not defined in the email module's `config_schema`
THEN the Pydantic validation SHALL reject the configuration
AND the registry SHALL raise a validation error at startup identifying the unexpected field.

#### Scenario: Invalid field type

WHEN a `butler.toml` `[modules.telegram]` section provides an integer value for a field that the `config_schema` declares as `str`
THEN the registry SHALL raise a validation error at startup
AND the error message SHALL identify the field, the expected type, and the actual value.

---

### Requirement: Dependency Resolution via Topological Sort

The module registry SHALL resolve module dependencies using topological sort. This determines the order in which modules are started and their tools registered.

#### Scenario: Linear dependency chain

WHEN module `A` depends on module `B`, and module `B` depends on module `C`, and module `C` has no dependencies
AND all three modules are enabled in `butler.toml`
THEN the registry SHALL resolve the startup order as `[C, B, A]`
AND `on_startup` SHALL be called in that order.

#### Scenario: Independent modules

WHEN modules `X` and `Y` are both enabled in `butler.toml`
AND neither declares a dependency on the other
THEN both modules SHALL be started successfully
AND either order of startup SHALL be acceptable.

#### Scenario: Diamond dependency

WHEN module `D` depends on modules `B` and `C`, module `B` depends on module `A`, and module `C` depends on module `A`
AND all four modules are enabled in `butler.toml`
THEN the registry SHALL resolve the startup order such that `A` starts before `B` and `C`, and `B` and `C` both start before `D`
AND each module's `on_startup` SHALL be called exactly once.

#### Scenario: Missing dependency not enabled

WHEN module `A` declares a dependency on module `B`
AND module `A` is enabled in `butler.toml` but module `B` is not
THEN the registry SHALL raise a `ValueError` at startup
AND the error message SHALL indicate that module `A` requires module `B` which is not enabled.

---

### Requirement: Circular Dependency Detection

The module registry SHALL detect circular dependencies among modules and refuse to start if any cycle exists.

#### Scenario: Direct circular dependency

WHEN module `A` declares a dependency on module `B`
AND module `B` declares a dependency on module `A`
AND both modules are enabled in `butler.toml`
THEN the registry SHALL raise a `ValueError` at startup
AND the error message SHALL identify the circular dependency cycle.

#### Scenario: Indirect circular dependency

WHEN module `A` depends on `B`, module `B` depends on `C`, and module `C` depends on `A`
AND all three modules are enabled
THEN the registry SHALL raise a `ValueError` at startup
AND the error message SHALL identify the circular dependency cycle involving `A`, `B`, and `C`.

#### Scenario: No circular dependency

WHEN all enabled modules form a directed acyclic graph (DAG) of dependencies
THEN the registry SHALL successfully resolve the startup order without raising any error.

---

### Requirement: Module Migrations

The butler runtime SHALL apply each loaded module's Alembic revisions during startup, after core and butler-specific Alembic chains and before calling `on_startup`. For each module whose `migration_revisions()` returns a non-`None` branch label, the runtime SHALL run `alembic upgrade head` on that branch against the butler's database. Alembic's version tracking prevents re-application of already-applied revisions.

#### Scenario: Applying module migrations on first startup

WHEN a butler starts for the first time
AND the loaded email module's `migration_revisions()` returns `"email"` (pointing to revisions in `alembic/versions/email/`)
THEN the butler SHALL apply all pending Alembic revisions from the `email` branch
AND the revisions SHALL be tracked by Alembic's `alembic_version` table.

#### Scenario: Skipping already-applied migrations

WHEN a butler restarts
AND a module's Alembic revisions have already been applied
THEN Alembic SHALL detect the branch is up-to-date and skip all revisions
AND SHALL NOT execute them again.

#### Scenario: Applying new migrations on upgrade

WHEN a module adds a new Alembic revision to its branch
AND the butler restarts
THEN the butler SHALL apply only the new, previously unapplied revisions
AND Alembic SHALL record them as applied.

---

### Requirement: Module Startup Order

Modules SHALL be started in topological dependency order. The `on_startup` method of each module SHALL be called only after all of its declared dependencies have completed their `on_startup` calls.

#### Scenario: Dependency completes before dependent starts

WHEN module `telegram` depends on module `email`
AND both modules are enabled
THEN `email.on_startup()` SHALL complete before `telegram.on_startup()` is invoked.

#### Scenario: Startup failure propagation

WHEN module `A`'s `on_startup` raises an exception
AND module `B` depends on module `A`
THEN module `B`'s `on_startup` SHALL NOT be called
AND the butler SHALL report the startup failure and halt.

---

### Requirement: Module Shutdown Order

Modules SHALL be shut down in reverse topological order (reverse of startup order). The `on_shutdown` method of each module SHALL be called in this order during butler shutdown.

#### Scenario: Reverse shutdown order

WHEN the startup order was `[C, B, A]` (C first, A last)
THEN during shutdown, `on_shutdown` SHALL be called in order `[A, B, C]` (A first, C last).

#### Scenario: Shutdown continues on error

WHEN module `A`'s `on_shutdown` raises an exception
THEN the butler SHALL log the error
AND SHALL continue calling `on_shutdown` on remaining modules in order
AND the butler SHALL NOT halt shutdown due to a single module's `on_shutdown` failure.

---

### Requirement: Tool Registration Isolation

Modules SHALL only add MCP tools to the butler's FastMCP server via the `register_tools` method. Modules MUST NOT directly access or modify core infrastructure components (scheduler, LLM CLI spawner, state store internals).

#### Scenario: Module registers MCP tools

WHEN a module's `register_tools(mcp, config, db)` method is called
THEN the module SHALL use the `mcp` FastMCP instance to register its domain-specific tools
AND those tools SHALL be available to runtime instances spawned by the butler.

#### Scenario: Module reads state via MCP tools

WHEN a module needs to read persistent state managed by the core state store
THEN the module SHALL access it via the state store MCP tools (`state_get`, `state_set`, etc.) rather than directly querying core tables
AND the module SHALL NOT import or call core state store internals.

#### Scenario: Module uses its own database tables

WHEN a module's registered MCP tools need to persist data
THEN the tools SHALL use the module's own database tables (created via the module's Alembic revisions)
AND the tools SHALL access those tables through the `db` connection pool passed to `register_tools`.

---

### Requirement: Module Config Source

Module-specific configuration SHALL come exclusively from the `[modules.<name>]` section of `butler.toml`. The key-value pairs in that TOML section SHALL be parsed and validated against the module's `config_schema`.

#### Scenario: Config passed from butler.toml

WHEN `butler.toml` contains:
```toml
[modules.email]
imap_host = "imap.example.com"
smtp_host = "smtp.example.com"
poll_interval_seconds = 60
```
AND the email module's `config_schema` declares `imap_host: str`, `smtp_host: str`, `poll_interval_seconds: int`
THEN the registry SHALL construct a validated config instance with those values
AND pass it to `register_tools` and `on_startup`.

#### Scenario: Environment variable references in config

WHEN a `butler.toml` module config value references an environment variable (e.g., `password = "${SOURCE_EMAIL_PASSWORD}"`)
THEN the butler runtime SHALL resolve the environment variable before passing the config to the module
AND SHALL raise an error at startup if the referenced environment variable is not set.
