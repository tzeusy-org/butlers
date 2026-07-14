# Tasks

## 1. Memory module schema isolation
- [x] 1.1 Add `memory_schema: str | None` to `MemoryModuleConfig`.
- [x] 1.2 Create a dedicated runtime pool (`_ensure_memory_schema_pool`) when
  the override is set; `_get_pool()` returns it so all storage/search/
  consolidation target the private schema. Close it in `on_shutdown`.
- [x] 1.3 Route the module's Alembic migration chain to the override schema in
  `lifecycle.py` step 8 (falls back to the butler schema for every other
  module). The Alembic env auto-creates the target schema.
- [x] 1.4 Point catalog provenance (`source_schema`) at the override schema.

## 2. Chronicler enablement + write-back
- [x] 2.1 Enable `[modules.memory]` with `memory_schema = "chronicler_mem"` in
  `roster/chronicler/butler.toml`.
- [x] 2.2 Deterministic synthesis + orchestrator in `writeback.py`
  (own-schema-only store, MCP enrichment proposal, no owner-facing message).
- [x] 2.3 Wire the day-close completion hook, using the memory module's pool
  (chronicler_mem), in `day_close_writer.py` + `daemon.py`.

## 3. Doctrine
- [x] 3.1 Amend `about/heart-and-soul/v1.md`, `MANIFESTO.md`, `AGENTS.md` for
  the own-schema write-back and the single sanctioned day-close summary. No
  em-dashes in the amended prose (non-negotiable #6).

## 4. Regression coverage
- [x] 4.1 Schema-matrix test proves `chronicler.episodes` and
  `chronicler_mem.episodes` coexist and memory-only tables never leak into the
  chronicler domain schema.
- [x] 4.2 Real-Postgres write-path test proves a memory fact lands in
  `chronicler_mem.facts`, not `chronicler`.
- [x] 4.3 Unit coverage for the write-back synthesis + own-schema/MCP/no-message
  invariants.
