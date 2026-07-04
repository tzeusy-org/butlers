## ADDED Requirements

### Requirement: Memory maintenance jobs are module-default

The memory module SHALL self-register its maintenance jobs (decay sweep,
consolidation, episode cleanup, superseded-fact purge, and a bounded
consolidation backfill) as scheduled jobs for every butler that enables
`[modules.memory]`, rather than requiring each butler's `butler.toml` to
declare them by hand. `butler.toml` MAY still declare a `[[butler.schedule]]`
block reusing one of these schedule names to override cadence; existence of
the schedule is never conditional on a `butler.toml` block, only its cadence.

#### Scenario: Decay sweep runs without any toml schedule

- **WHEN** a butler's `butler.toml` enables `[modules.memory]` and declares no
  `[[butler.schedule]]` block named `memory_decay_sweep`
- **THEN** a `memory_decay_sweep` scheduled task MUST exist and be enabled
  after the daemon boots
- **AND** it MUST dispatch to a handler that calls `run_decay_sweep`

#### Scenario: Module registration is idempotent across restarts

- **WHEN** the daemon restarts and the memory module's `on_startup` runs again
- **THEN** no duplicate schedule rows are created for any of the module's
  default schedule names
- **AND** an existing schedule's cron, `enabled` state, and `job_args` are left
  untouched (module registration never clobbers an operator's DB-level cadence
  customization)

#### Scenario: TOML overrides cadence, not existence

- **WHEN** a butler's `butler.toml` declares a `[[butler.schedule]]` block
  named `memory_consolidation` with a custom cron
- **THEN** the schedule's cadence MUST follow the TOML-declared cron
- **AND** if the operator later removes that `[[butler.schedule]]` block, the
  schedule MUST remain enabled (falling back to the module-registered default
  cadence on the next module registration pass) rather than being disabled as
  an orphaned TOML schedule

#### Scenario: Every memory-enabled butler has a working consolidation path

- **WHEN** a butler enables `[modules.memory]`
- **THEN** `memory_consolidation`, `memory_episode_cleanup`,
  `memory_purge_superseded`, and `memory_decay_sweep` MUST all resolve to a
  registered deterministic-job handler for that butler — a module-registered
  schedule naming a job with no registered handler for that butler is a
  defect, not an acceptable configuration

#### Scenario: Bounded incremental backlog drain

- **WHEN** a butler has episodes in `consolidation_status = 'pending'` beyond
  what its steady-state `memory_consolidation` cadence clears
- **THEN** a `memory_consolidation_backfill` schedule MUST claim additional
  pending episodes on a tighter cadence, bounded to a configurable batch size
  per run (never one unbounded transaction)
- **AND** episodes in `consolidation_status = 'dead_letter'` MUST NOT be
  reclaimed by this or any consolidation pass

## Source References

- Non-Negotiable Rule 2 (`about/heart-and-soul/vision.md`): modules only add
  tools and declare intent — they never touch core infrastructure directly.
  The module-default registration mechanism lives as a core helper
  (`ensure_module_default_schedule` in `src/butlers/core/scheduler.py`); the
  memory module only declares which schedules it wants and their default
  cadence.
- Non-Negotiable Rule 5: operational tuning (cadence) lives in the database
  and is seeded from a default on first boot, mirroring the existing
  `runtime_config` seed-and-manage pattern rather than requiring a git-tracked
  toml block to exist for the job to run at all.
