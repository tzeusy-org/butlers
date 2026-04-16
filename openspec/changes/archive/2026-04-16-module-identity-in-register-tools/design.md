## Context

The daemon is the sole authority for butler identity (`self.config.name`), but distributes it through two divergent paths:

1. **`register_tools(mcp, config, db)`** — all modules receive this, but identity is absent. Three modules (calendar, google_drive, metrics) reverse-engineer it from `db.schema` or `db.db_name`. CalendarModule had a dedicated `_resolve_butler_name(db)` with a 3-step fallback chain that read the wrong attribute (`db.db_schema` instead of `db.schema`), causing a production bug where the finance butler identified as `"butlers"`.

2. **`wire_runtime(butler_name, spawner, repo_root, ...)`** — only QA and self_healing modules implement this. Runs after `register_tools` in phase 13d. `butler_name` is the first positional arg.

## Goals / Non-Goals

**Goals:**
- Single, explicit identity propagation path: daemon passes `butler_name` to `register_tools`, available from phase 13 onward.
- Eliminate all ad-hoc identity derivation from database attributes.
- Remove the redundant `butler_name` parameter from `wire_runtime` — it still carries `spawner`, `repo_root`, and `switchboard_client`.
- Update RFC 0002 and core-modules spec to reflect the new contract.

**Non-Goals:**
- Changing `wire_runtime` beyond removing `butler_name`. The method continues to serve its purpose of wiring runtime dependencies (spawner, repo_root, switchboard_client) that aren't available at `register_tools` time.
- Adding `butler_name` to `on_startup` — modules that need it can store it from `register_tools`.
- Deprecation period — no external consumers exist; this is an internal ABC change.

## Decisions

### D1: `butler_name` as 4th positional parameter on `register_tools`

Add `butler_name: str` after `db` in the abstract signature:
```python
async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
```

**Why positional, not keyword-only:** Consistency with the existing 3-positional pattern. All callers (daemon + tests) pass args positionally. Adding a keyword-only param would create an asymmetry.

**Why not on `__init__`:** Modules are instantiated by the registry before butler config is loaded. Identity is only known at registration time.

**Alternative considered:** Pass identity via `config` (add a `butler_name` field to every module's Pydantic config). Rejected — config is module-specific domain settings, not framework plumbing. Mixing them violates separation of concerns.

### D2: Remove `butler_name` from `wire_runtime`

Current daemon call:
```python
wire_fn(self.config.name, self.spawner, repo_root, switchboard_client=...)
```

New daemon call:
```python
wire_fn(self.spawner, repo_root, switchboard_client=...)
```

QA and self_healing modules store `self._butler_name` in `register_tools` instead of `wire_runtime`. The `wire_runtime` method focuses purely on runtime dependencies that aren't available during tool registration.

**Why not keep both:** Redundant identity handoff creates confusion about which is authoritative. Single source of truth is cleaner.

### D3: Delete `_resolve_butler_name` and all db-based identity derivation

Calendar's `_resolve_butler_name(db)` (3-step fallback: `db.schema` → `db.db_name` with prefix strip → default) is deleted entirely. Google Drive and metrics similarly stop reading `db.schema` for identity. All three use the `butler_name` parameter directly.

## Risks / Trade-offs

- **[Risk] Missed module implementation** → TypeError at startup. Mitigation: grep-verified exhaustive list of 25+ implementations; CI catches any miss immediately.
- **[Risk] Test churn** → ~67 call sites across ~25 test files. Mitigation: mechanical changes, no logic changes. Each test adds one string argument.
- **[Risk] `test_mcp_only_inter_butler.py` contract test** → Tests that assert `register_tools` signature shape may fail. Mitigation: review and update the assertion.
- **[Trade-off] Unused parameter on simple modules** → 20 modules accept `butler_name` but don't use it. Accepted: ABC uniformity is more valuable than minimal signatures. The parameter is a single string, zero runtime cost.
