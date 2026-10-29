## Why

The Module ABC carries seven abstract members although `dependencies` is `[]` in 26 of 27 modules and
`migration_revisions` is `None` in 22. Nine modules copy the same `_tool(group)` helper, and
credential-guard plus broad-except blocks repeat per tool in spotify, steam and google_health
(bu-qozovd.2, 2026-10-21 simplicity audit).

## What Changes

- `Module` keeps `name`, `config_schema` and `register_tools` abstract; every other hook has a default
  (`dependencies`, `migration_revisions`, `on_startup`, `on_shutdown`, `credentials_env`,
  `on_all_modules_ready`).
- `tool_registrar(mcp, groups)` replaces the per-module `_tool(group)` helpers.
- `guarded_tool(credential=, missing=, errors=)` replaces the per-tool credential check and broad `except`.

## Non-goals

- Declared-only module loading (a separate change in the same bead).
- Calendar registration internals, capability declarations (bu-qozovd.1, bu-o9qax4).

## Impact

- `src/butlers/modules/base.py`, every module's stub overrides, and the nine tool registration sites.
