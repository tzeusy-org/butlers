## 1. Decay sweep wiring

- [x] 1.1 Wrap `run_decay_sweep` as `_run_memory_decay_sweep_job` in
      `scheduled_jobs.py` and register it as `memory_decay_sweep` in
      `_MEMORY_MAINTENANCE_JOB_HANDLERS`
- [x] 1.2 Integration test: `run_decay_sweep` actually fades/expires facts and
      rules per `memory_policies` thresholds against a real Postgres DB

## 2. Module-default schedule registration

- [x] 2.1 Add `ensure_module_default_schedule` to `src/butlers/core/scheduler.py`
      — idempotent insert-if-absent, reclaims a schedule from `source='toml'`
      to `source='db'` without touching cron/enabled, so TOML overrides
      cadence but not existence
- [x] 2.2 Call it from `MemoryModule.on_startup` for
      `memory_decay_sweep`, `memory_consolidation`, `memory_episode_cleanup`,
      `memory_purge_superseded`, `memory_consolidation_backfill`
      (best-effort per entry — one failure does not disable the module)
- [x] 2.3 Remove the now-redundant `[[butler.schedule]]` blocks from
      `roster/{health,lifestyle,general,home,relationship}/butler.toml`
- [x] 2.4 Add `**_MEMORY_MAINTENANCE_JOB_HANDLERS` to the `finance`, `travel`,
      and `education` entries in `_build_deterministic_schedule_job_registry`
      (they had the memory module enabled but no handlers at all)
- [x] 2.5 Unit tests: idempotency across repeated calls, TOML-override
      precedence (integration, since it exercises the real reclaim handshake
      with `sync_schedules`), and best-effort failure isolation

## 3. Bounded consolidation backfill

- [x] 3.1 Extend `_run_memory_consolidation_job` to accept
      `job_args.batch_size` (validated positive int) so one handler serves
      both the steady-state and backfill cadences
- [x] 3.2 Register `memory_consolidation_backfill` as a module-default
      schedule: same `job_name=memory_consolidation`, larger `batch_size`,
      tighter cron
- [x] 3.3 Integration test: batch_size bounds how many *pending* episodes are
      claimed per run, and `dead_letter` episodes are never reclaimed

## 4. Spec delta

- [x] 4.1 Add "Memory maintenance jobs are module-default" requirement to
      `memory-retention-policy`
- [x] 4.2 `openspec validate --strict` on the change

## 5. Deferred (reported as follow-up, not implemented here)

- [ ] 5.1 Wire a real `Spawner` into the deterministic `memory_consolidation`
      job path so claimed episodes are actually parsed and promoted to
      facts/rules (today `cc_spawner=None` means only claim/lease happens —
      true for the 5 already-scheduled butlers too, not introduced by this
      change)
- [ ] 5.2 ivfflat → HNSW index migration + synthetic-scale recall harness
      (pursuit slice 4 — deferred as out of scope for this change)
