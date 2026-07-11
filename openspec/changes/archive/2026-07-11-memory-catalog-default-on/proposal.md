# memory-catalog-default-on

## Why

The cross-butler knowledge plane (`memory-discovery-catalog` spec) is fully
built — table, indexes, grants, write-behind, cross-butler search tool — but
was switched off by default and had **zero rows** fleet-wide (see
`docs/redesigns/2026-07-04-jarvis-pursuit.md`, ranked move #15). The spec
already requires the write-behind and search mechanics; it is silent on
whether the feature flag defaults on or off, and it has no requirement that
pre-existing facts/rules ever get cataloged. That silence is exactly what let
a fully-shipped, spec-compliant discovery plane sit dormant: `enable_shared_
catalog` defaulted `False` (`src/butlers/modules/memory/__init__.py:185`
pre-change) and zero `butler.toml` files opted in, so no butler could
discover another's knowledge short of a full Switchboard LLM round-trip.

This change closes that gap at the spec level: the catalog write-behind
SHALL be enabled by default (toml remains an explicit opt-out for a specific
butler), and an idempotent backfill mechanism SHALL exist to drain the
~3,600 facts/rules written before the flip.

## What Changes

- **ADDED (`memory-discovery-catalog`) — Catalog write-behind defaults to
  enabled.** `enable_shared_catalog` SHALL default to `True` at the module
  level; a butler's `butler.toml` MAY still set it to `False` to opt that
  butler out. The `source_schema` written to catalog rows SHALL be inferred
  from the module's database handle (falling back to the butler name) when
  not explicitly configured, so default-on actually produces correctly
  attributed rows rather than silently writing nothing.
- **ADDED (`memory-discovery-catalog`) — Backfill drains pre-existing
  facts/rules into the catalog.** A bounded, idempotent, safe-to-re-run
  backfill mechanism SHALL exist that upserts existing active facts and
  non-forgotten rules into `public.memory_catalog` via the existing
  `UNIQUE(source_schema, source_table, source_id)` key, excluding retracted
  facts and forgotten rules, and running as a module-default scheduled job
  so no per-butler toml configuration is required for the backlog to drain.

## Impact

- **Specs:** `memory-discovery-catalog` (ADDED — two new requirements; no
  existing requirement text is modified).
- **Code:**
  - `src/butlers/modules/memory/__init__.py` — `enable_shared_catalog`
    default flip, `source_schema` inference, module-default
    `memory_catalog_backfill` schedule registration, and a `## Fleet
    Knowledge` section wired into the real trigger-time context-assembly
    hook (first consumer).
  - `src/butlers/modules/memory/storage.py` — `run_memory_catalog_backfill`
    and its per-table helpers.
  - `src/butlers/scheduled_jobs.py` — `memory_catalog_backfill` deterministic
    job handler + `current_schema()` inference.
  - `src/butlers/modules/memory/tools/context.py` — `include_fleet_knowledge`
    opt-in section in `memory_context()`.
  - `src/butlers/api/routers/memory.py` +
    `src/butlers/api/models/memory.py` — `GET /api/memory/catalog/search`,
    a fleet-knowledge search surface on the dashboard memory router.
- **Data:** the backfill job catalogs the existing ~3,600 facts/rules across
  the fleet in bounded batches over the following scheduled runs; no
  migration is required (the `public.memory_catalog` table already exists
  per `memory-catalog-schema`).
- **No breaking API contract change.** `enable_shared_catalog` and
  `memory_catalog_search` both already existed; this flips a default and
  adds a new GET endpoint plus an opt-in context section.

## Source References

- `memory-discovery-catalog` spec (Requirement: Catalog write-behind on
  memory store; Requirement: Cross-butler search via catalog).
- `docs/redesigns/2026-07-04-jarvis-pursuit.md`, ranked move #15 ("Turn on
  the cross-butler knowledge plane").
