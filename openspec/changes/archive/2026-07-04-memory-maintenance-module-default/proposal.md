## Why

The memory-retention-policy spec and `docs/modules/memory.md` describe a decay
sweep and policy-driven maintenance lifecycle as though it runs continuously,
but `run_decay_sweep` (storage.py) has never been wired to any scheduled-job
handler, has no `butler.toml` schedule, and no MCP tool — it has simply never
executed in production. Every confidence value the console displays is an
un-decayed write-time number masquerading as a maintained one. Separately,
only 5 of the 9 butlers with the memory module enabled ever scheduled
consolidation (each via a hand copy-pasted `[[butler.schedule]]` block), and
3 of those 9 (finance, travel, education) had no memory maintenance handler
registered in the deterministic job registry at all — so even adding a toml
schedule for them would have failed at dispatch with "unknown deterministic
job". The result: 6,500+ episodes are backlogged in `pending` consolidation
status (switchboard 4,148, finance 1,294, education 1,137) with no path to
drain.

This change makes the already-designed memory lifecycle (decay, consolidation,
episode cleanup, superseded-fact purge) run as a module default for every
butler that enables the memory module, instead of a per-butler opt-in that
operators have to remember to copy-paste and keep in sync by hand.

## What Changes

- **`memory_decay_sweep` becomes a real scheduled job.** `run_decay_sweep` is
  wrapped as a deterministic job handler and added to
  `_MEMORY_MAINTENANCE_JOB_HANDLERS`.
- **Maintenance schedules are module defaults, not copy-pasted toml.** The
  memory module self-registers default schedules (decay sweep, consolidation,
  episode cleanup, superseded-fact purge, and a bounded consolidation
  backfill) in `on_startup` for every butler that enables `[modules.memory]`.
  `butler.toml` may still declare a `[[butler.schedule]]` block reusing one of
  these names to override cadence — TOML wins on cadence, never on existence.
  The copy-pasted `[[butler.schedule]]` blocks in `roster/*/butler.toml` are
  removed now that they are redundant.
- **The deterministic job registry now covers every memory-enabled butler.**
  `finance`, `travel`, and `education` gain the memory maintenance handlers
  they were missing (switchboard already had the handlers, just no schedule).
- **A bounded, incremental backlog-drain job.** A `memory_consolidation_backfill`
  schedule reuses the `memory_consolidation` handler with a configurable,
  larger `job_args.batch_size` on a tighter cadence, so the pending-episode
  backlog drains in bounded increments per run rather than requiring one
  unbounded catch-up transaction. It never reclaims `dead_letter` episodes
  (the underlying claim query already excludes them).

## Impact

- **Affected specs:** `memory-retention-policy` (this change adds the
  "maintenance jobs are module-default" requirement; the sweep's per-class
  threshold logic itself is unchanged).
- **Affected code:** `src/butlers/scheduled_jobs.py` (new `memory_decay_sweep`
  handler, `memory_consolidation`'s `job_args.batch_size` override, registry
  entries for finance/travel/education), `src/butlers/core/scheduler.py` (new
  `ensure_module_default_schedule` core helper — Non-Negotiable Rule 2: the
  module only declares *what* schedule it wants; the mutation/idempotency
  logic against the core `scheduled_tasks` table lives in core, not in the
  module), `src/butlers/modules/memory/__init__.py`
  (`_register_default_maintenance_schedules` called from `on_startup`),
  `roster/{health,lifestyle,general,home,relationship}/butler.toml` (redundant
  schedule blocks removed).
- **Known follow-up (out of scope here):** the deterministic
  `memory_consolidation` job path always calls `run_consolidation(...,
  cc_spawner=None)`, which only claims/groups pending episodes — it does not
  spawn an LLM session to actually parse episodes into facts/rules. This is
  true today for the 5 already-scheduled butlers as well, not something this
  change introduces. Draining the backlog to zero *pending* episodes requires
  wiring a real `Spawner` into this path, tracked as a separate follow-up
  (see PR description).
