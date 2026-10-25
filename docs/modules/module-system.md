# Module System

> **Purpose:** Describes the abstract module contract, registry, dependency resolution, and lifecycle hooks that all butler modules implement.
> **Audience:** Contributors and module developers.
> **Prerequisites:** Familiarity with the [Butlers architecture overview](../architecture/index.md).

## Overview

![Module System](./module-system-design.svg)

Every butler is a long-running MCP server with a fixed core (state store, scheduler, LLM spawner, session log) and a set of opt-in **modules**. Modules are the only mechanism for adding domain-specific MCP tools to a butler. They never touch core infrastructure directly.

This page is the single definition of the module contract. The code is authoritative for names and
signatures; this page states only the invariants the code cannot say for itself.

Source of truth:

- `src/butlers/modules/base.py` -- the `Module` ABC, `ToolMeta`, and the `ToolGroupMixin` /
  `group_enabled()` tool-group filter, the `tool_registrar` and `guarded_tool` helpers
- `src/butlers/modules/registry.py` -- `ModuleRegistry`, `default_registry()`, `_topological_sort()`
- `src/butlers/lifecycle.py` -- the startup sequence that drives the hooks (numbered steps)

## The Contract

Every module subclasses `Module` (`src/butlers/modules/base.py`) and implements its three abstract
members: a non-empty class-level `name` string, the `config_schema` property, and `register_tools`. Every other hook
(`dependencies`, `migration_revisions`, `on_startup`, `on_shutdown`, `credentials_env`,
`on_all_modules_ready`) has a default and is overridden only when needed. Read the signatures and
docstrings there.
The invariants:

- **Modules only add tools.** A module never modifies core infrastructure (state store, scheduler,
  spawner, session log); everything it contributes reaches the butler through `register_tools`.
- **`name` is the stable key** for the `[modules.<name>]` section in `butler.toml`, for
  dependency declarations, and for runtime enable/disable state.
- **Config is validated, not trusted.** The daemon validates the raw `[modules.<name>]` table
  against `config_schema` and passes the validated object to the hooks. Configs that mix in
  `ToolGroupMixin` gain an optional `groups` list; `register_tools` should gate each tool family
  with the `@_tool("<group>")` decorator from `tool_registrar(mcp, config)` so a butler can load
  only part of a module. Tools that need a credential and map provider errors use `guarded_tool`
  rather than repeating the check and a broad `except` per tool.
- **Every tool carries a description.** FastMCP takes it from the handler's docstring (or an
  explicit `description=`). Strict providers reject the whole request when any tool lacks one
  (GLM via OpenCode: `tools[N]: function.description is required`), so one bare handler takes a
  model out of rotation for every butler that loads it. `tests/contracts/test_tool_descriptions.py`
  enforces this for core and module tools.
- **Identity comes from `butler_name`.** `register_tools` receives the canonical butler name from
  the daemon; modules must use it and never derive identity from `db.schema` or similar.
- **`on_startup` owns connections and background work; `on_shutdown` must release them.**
  Secrets resolve DB-first through the optional `credential_store` argument.
- **Tool sensitivity is declared, not guessed, when it matters.** `tool_metadata()` (optional,
  default `{}`) maps tool names to `ToolMeta` argument sensitivities. Without a declaration the
  approvals subsystem falls back to name-based heuristics (see [Approvals](approvals.md)).
- Other optional hooks (`wire_audit_pool`, `extra_status_fields`) default to no-ops; see their
  docstrings in `base.py`.

## Module Registry

`default_registry()` discovers every concrete `Module` subclass under the `butlers.modules`
package, plus butler-specific modules in `roster/<butler>/modules/__init__.py`. Registration is
constructor-free and idempotent during discovery for the same class object; the public `register()` method still
rejects duplicate names. Explicit discovery import failures are raised rather than returning an
incomplete registry. Registry instances remain fresh; requested roster module names resolve from
their real checkout package without pytest setup.

The daemon calls `load_from_config()`: only explicit `[modules.<name>]` declarations are
constructed, including schema-less modules. Unknown names, dependencies outside the declared set,
and cycles raise `ValueError` before provisioning, migrations, startup hooks or tool registration.
Health and sticky user-enabled state remain separate from declaration eligibility. The explicit
`load_all()` utility still constructs all registered modules; daemon startup does not call it.

## Dependency Order

`_topological_sort()` orders modules so that every module's `dependencies` come before it
(Kahn's algorithm, alphabetical within a tier, so the order is deterministic). A cycle raises
`ValueError`. This single order governs module migrations, `on_startup`, and `register_tools`;
`on_shutdown` runs in reverse.

## Migrations

CLI and daemon share `get_module_migration_chains()` in `src/butlers/migrations.py`.
They select declared modules' existing non-empty migration directories in dependency order, after
the core and butler chains. The butler's own chain runs once. Roster source layout resolves aliases:
`home_assistant` lives in `roster/home/modules`, so Health runs its `home` chain while Home already
runs that chain as its butler chain. The legacy `migration_revisions()` hook remains optional
metadata and does not select migrations.

Module migrations run into the butler's own schema unless the module config declares a private
schema (for example memory's `memory_schema`). See [Migration patterns](../data_and_storage/migration-patterns.md).

## Startup Order

The authoritative step list is the module docstring of `src/butlers/daemon.py` and the numbered
comments in `src/butlers/lifecycle.py`. For modules, the order that matters is:

1. **Load and order declarations** -- `load_from_config()` constructs only declared modules and
   rejects unknown names, missing declared dependencies and cycles before database work.
2. **Validate config** -- each module's config is validated against `config_schema`.
3. **Migrate** -- core, butler-specific, then module migration chains.
4. **Start** -- `on_startup(config, db, credential_store, blob_store)` in dependency order.
5. **Register tools** -- only after the FastMCP server exists and core tools are registered,
   `register_tools(mcp, config, db, butler_name)` runs in dependency order; approval gates are
   applied afterwards.

So `on_startup` runs **before** `register_tools`: a tool body may assume the module has started.
Module-specific steps are non-fatal per module: a module that fails config validation, credentials,
migration, `on_startup` or registration is recorded as failed and skipped in later phases, and the
butler starts with the remaining modules. If startup aborts, modules already started get
`on_shutdown()`.

## Writing a New Module

Place the module in `src/butlers/modules/` (single file or package) and auto-discovery finds it;
butler-specific modules go in `roster/<butler>/modules/__init__.py`. Copy the shape of a small
existing module (for example `src/butlers/modules/pipeline.py`) rather than a template, and follow the
`adding-connectors-and-modules` subskill of the `butlers-development` skill.

## Implementation Notes

- `default_registry()` imports and registers classes without constructors. All implementations
  expose a stable class-level `name`; registering a property or an empty/non-string name fails.
  `load_from_config()` is the daemon admission boundary. `enabled` cannot admit an undeclared
  module. QA error reception and read-only attempt history are unconditional core tools,
  `report_error` and `get_healing_status`, not a `self_healing` module or skill. They do not
  install local investigation cleanup, recovery or publication.
- Home Assistant keeps its mandatory `contacts` and `approvals` dependencies. Health declares
  both even though its Home Assistant tools remain read-only.
- `credentials_env` describes daemon-side requirements. Module declarations do not add secrets
  to runtime child environments; explicit butler/runtime credential selection remains authoritative.
- Module configs pass through `ButlerDaemon._validate_module_configs`, which rejects extra and
  missing fields.
- Egress audit: every outbound call emits one `dashboard_audit_log` operation at its call site
  (`llm_api_call` from the spawner, `telegram_send`, `google_calendar_write`, `gmail_send`) via
  `write_audit_entry` or `emit_dashboard_audit`; `GET /api/system/egress` reads them. Modules get
  the pool through `Module.wire_audit_pool(pool)`, a post-startup no-op by default.

## Related Pages

- [Memory Module](memory.md)
- [Approvals Module](approvals.md)
- [Calendar Module](calendar.md)
- [Contacts Module](contacts.md)
