# Tasks — memory-catalog-default-on

- [x] 1 — Flip `MemoryModuleConfig.enable_shared_catalog` default to `True`;
  infer `source_schema` from the module's database handle (falling back to
  `butler_name`) when `catalog_source_schema` is unset in toml.
  (spec: "Catalog write-behind defaults to enabled")
- [x] 2 — Add `run_memory_catalog_backfill` (+ per-table helpers) in
  `storage.py`: bounded, idempotent upsert of active facts / non-forgotten
  rules via the catalog's UNIQUE key.
  (spec: "Backfill drains pre-existing facts/rules into the catalog")
- [x] 3 — Register `memory_catalog_backfill` as a deterministic scheduled-job
  handler and a module-default schedule (every 5 minutes) so the backlog
  drains without per-butler toml configuration.
  (spec: "Backfill drains pre-existing facts/rules into the catalog")
- [x] 4 — Land the first consumers: an opt-in `## Fleet Knowledge` section in
  `memory_context()` (wired on by default in the real trigger-time context
  hook), and `GET /api/memory/catalog/search` on the dashboard memory router.
- [x] 5 — Tests: unit coverage for the default flip + source_schema
  inference, the backfill job's job_args validation and schedule
  registration, the Fleet Knowledge section, and the new endpoint; a
  real-Postgres integration test proving the backfill is idempotent and
  excludes retracted facts / forgotten rules.
