## Why

`Module.register_tools(mcp, config, db)` does not receive the butler's identity. Modules that need `butler_name` (calendar, google_drive, metrics) reverse-engineer it from `db.schema` or `db.db_name` — a fragile pattern that already caused a production bug where the finance butler identified itself as `"butlers"` (the shared database name) because CalendarModule read the wrong attribute (`db.db_schema` instead of `db.schema`). Meanwhile, QA and self_healing modules receive identity via a separate `wire_runtime()` call that runs later. This split creates two identity distribution paths from the daemon, one implicit and error-prone, one explicit but only available to two modules.

## What Changes

- **BREAKING**: Add `butler_name: str` as the 4th positional parameter to `Module.register_tools()` on the abstract base class and all implementations.
- **BREAKING**: Remove `butler_name` from `wire_runtime()` signatures on QA and self_healing modules — `wire_runtime()` retains `spawner`, `repo_root`, and `switchboard_client`.
- Delete `CalendarModule._resolve_butler_name()` and all ad-hoc identity derivation from `db` attributes in calendar, google_drive, and metrics modules.
- Update the daemon's `_register_module_tools()` to pass `self.config.name` and `_wire_module_runtime()` to drop it.
- Update RFC 0002 Module ABC contract and CLAUDE.md quick-reference.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `core-modules`: The Module ABC contract changes — `register_tools` gains a 4th parameter, and the identity-propagation contract becomes explicit.

## Impact

- **Code**: `src/butlers/modules/base.py` (ABC), `src/butlers/daemon.py` (call sites), all 16 module implementations in `src/butlers/modules/`, ~10 roster module implementations, ~55 test call sites, ~15 test stubs, ~12 wire_runtime test calls.
- **Docs**: RFC 0002 (Module ABC signature), `openspec/specs/core-modules/spec.md`, root `CLAUDE.md`.
- **APIs**: No external API changes — this is an internal Python ABC contract.
- **Risk**: Low — no external consumers, atomic single-commit change, ~120 lines of mechanical edits.
