## 1. Module Skeleton

- [ ] 1.1 Create `src/butlers/modules/metrics/` package with empty `__init__.py`
- [ ] 1.2 Implement `MetricsModuleConfig` Pydantic model with required `prometheus_query_url: str` field
- [ ] 1.3 Implement `MetricsModule` class stub: subclass `Module`, return `name="metrics"`, `dependencies=[]`, `migration_revisions()=None`, empty `on_startup`/`on_shutdown`/`register_tools`
- [ ] 1.4 Verify `ModuleRegistry.default_registry()` auto-discovers `"metrics"` (run existing registry tests)

## 2. Storage Layer

- [ ] 2.1 Create `src/butlers/modules/metrics/storage.py` with `save_definition(pool, name, defn)` writing to state store key `metrics_catalogue:<name>`
- [ ] 2.2 Add `load_all_definitions(pool) -> list[dict]` that reads all state store keys with prefix `metrics_catalogue:` using `state_list`
- [ ] 2.3 Add `count_definitions(pool) -> int` helper for cap enforcement

## 3. Instrument Management

- [ ] 3.1 Add `_butler_name: str`, `_pool`, `_instrument_cache: dict[str, tuple[str, Any]]` instance state to `MetricsModule`
- [ ] 3.2 Implement `_full_name(name) -> str`: prefix `butler_<butler_name>_` with hyphens replaced by underscores
- [ ] 3.3 Implement `_validate_name(name) -> bool`: regex `^[a-z][a-z0-9_]*$`
- [ ] 3.4 Implement `_build_instrument(full_name, type, help) -> OTELInstrument`: calls `get_meter().create_counter()` / `create_up_down_counter()` / `create_histogram()` as appropriate

## 4. `on_startup` Implementation

- [ ] 4.1 Derive `_butler_name` from `db.schema` (replace hyphens with underscores) and store `_pool = db.pool`
- [ ] 4.2 Load all persisted definitions via `load_all_definitions(pool)` and populate `_instrument_cache` by calling `_build_instrument` for each

## 5. Write-Side MCP Tools

- [ ] 5.1 Implement `metrics_define` tool: validate name format, check cap (1,000 limit), check for existing entry (idempotent return), create instrument, persist to state store, update cache. Tool description MUST include cardinality advisory warning against high-cardinality label values.
- [ ] 5.2 Implement `metrics_emit` tool: look up instrument in cache (error if missing), validate label keys match declared set, enforce non-negative value for counters and histograms, call `instrument.add()` or `instrument.record()` as appropriate

## 6. Prometheus Query Layer

- [ ] 6.1 Create `src/butlers/modules/metrics/prometheus.py` with `async_query(url, query, time=None) -> list[dict]` calling `GET /api/v1/query` via `httpx.AsyncClient`; surface HTTP errors and `httpx` exceptions as descriptive strings
- [ ] 6.2 Add `async_query_range(url, query, start, end, step) -> list[dict]` calling `GET /api/v1/query_range`; surface errors the same way

## 7. Read-Side & Catalogue MCP Tools

- [ ] 7.1 Implement `metrics_query` tool: delegate to `async_query()`, return result vector
- [ ] 7.2 Implement `metrics_query_range` tool: delegate to `async_query_range()`, return result matrix
- [ ] 7.3 Implement `metrics_list` tool: call `load_all_definitions(pool)` and return the list

## 8. Tool Registration

- [ ] 8.1 Wire all six tools (`metrics_define`, `metrics_emit`, `metrics_list`, `metrics_query`, `metrics_query_range`) into `register_tools()` in `__init__.py`, injecting `pool` and `prometheus_query_url` from module state

## 9. Tests

- [ ] 9.1 Config validation: `prometheus_query_url` required; valid config passes
- [ ] 9.2 Name validation: valid names accepted; uppercase, leading-digit, and space names rejected
- [ ] 9.3 Namespace prefix: `finance` butler + `api_calls` → `butler_finance_api_calls`; hyphens in butler name replaced
- [ ] 9.4 `metrics_define`: counter creates OTEL Counter; gauge creates UpDownCounter; histogram creates Histogram
- [ ] 9.5 `metrics_define`: re-defining existing metric returns cached instrument without state store write
- [ ] 9.6 `metrics_define`: cap at 1,000 — error returned, no instrument created
- [ ] 9.7 `metrics_emit`: counter add, gauge add (negative OK), histogram record
- [ ] 9.8 `metrics_emit`: undefined metric returns error without OTEL call
- [ ] 9.9 `metrics_emit`: negative counter value rejected
- [ ] 9.10 `metrics_emit`: label key mismatch rejected
- [ ] 9.11 `metrics_list`: returns all definitions; returns empty list when none defined
- [ ] 9.12 `metrics_query`: success returns vector; PromQL parse error surfaced; network error surfaced without exception
- [ ] 9.13 `metrics_query_range`: success returns matrix; invalid step error surfaced
- [ ] 9.14 `on_startup`: re-registers instruments from state store; empty state store is a no-op
- [ ] 9.15 Module registry: `"metrics"` appears in `available_modules` after `default_registry()`
