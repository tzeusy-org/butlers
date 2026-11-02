## MODIFIED Requirements

### Requirement: Memory maintenance jobs are module-default

The memory module SHALL self-register its maintenance jobs (decay sweep,
consolidation, episode cleanup, superseded-fact purge, a bounded consolidation
backfill, and local ANN observability) as scheduled jobs for every butler that
enables `[modules.memory]`, rather than requiring each butler's `butler.toml`
to declare them by hand. `butler.toml` MAY still declare a
`[[butler.schedule]]` block reusing one of these schedule names to override
cadence while that declaration is active; existence of the schedule is never
conditional on a `butler.toml` block, only its current TOML ownership.

Module registration SHALL insert the ordinary enabled DB-owned default only
when no same-named row exists. An existing `source='db'` row, including an
operator-disabled row, remains DB-owned and SHALL be left unchanged. In the
distinct TOML-orphan case, after TOML synchronization has established that a
registered default's same-named row is `source='toml'` and `enabled=false`,
module-default recovery SHALL reclaim it as `source='db'` and `enabled=true`
while preserving its stored cadence and execution payload. No registered
default is exempt: a disabled TOML-orphaned `memory_episode_cleanup` row is
recovered like any other, because its sweep deletes only reapable expired
episodes in bounded batches and cannot perform an unbounded catch-up. A
recovered cleanup row runs only that ordinary sweep; recovery confers no
historical-drain authority. Recovery SHALL NOT replace a recovered row's last
TOML cadence with the module-default cadence.

Under the goal runtime (RFC 0038 D2 and D18), every memory maintenance schedule
remains a job-mode row with a deterministic handler. Episode cleanup, decay,
superseded-fact purge and ANN observability stay deterministic jobs that never
admit a goal. The `memory_consolidation` handler, including its bounded
backfill schedule, SHALL perform no model judgment itself: it submits one
obligation admission per `(tenant_id, butler, partition)` as the
`module-memory` consolidation pipeline requires, and admits none for an empty
pending set. Recovering a consolidation row therefore resumes those partitioned
obligation goals at its preserved cadence; recovery itself admits no goal.

ID: REQ-memory-retention-policy-010
Source: bu-kqnum.13 module-default TOML-orphan recovery; PR #3565; RFC 0038 D2 and D18
Scope: v1-mandatory

#### Scenario: Decay sweep runs without any TOML schedule

- **WHEN** a butler's `butler.toml` enables `[modules.memory]` and declares no
  `[[butler.schedule]]` block named `memory_decay_sweep`
- **THEN** a `memory_decay_sweep` scheduled task MUST exist and be enabled
  after the daemon boots
- **AND** it MUST dispatch to a handler that calls `run_decay_sweep`

#### Scenario: DB-owned module schedule remains idempotent across restarts

- **WHEN** the daemon restarts and the memory module's `on_startup` runs again
  for an existing same-named `source='db'` schedule
- **THEN** no duplicate schedule row is created
- **AND** that schedule's cron, `enabled` state, and `job_args` are left
  untouched, including a deliberate operator disable

#### Scenario: Active TOML override remains TOML-owned

- **WHEN** a butler's active `butler.toml` declares a
  `[[butler.schedule]]` block named `memory_consolidation` with a custom cron
- **THEN** TOML synchronization MUST own that schedule's cadence and enablement
- **AND** module-default recovery MUST NOT change its `source` or emit a
  recovery audit entry

#### Scenario: Removed TOML override is re-enabled at its last TOML cadence

- **WHEN** that `memory_consolidation` TOML block is later removed and, after
  TOML synchronization, its same-named row is `source='toml'` and
  `enabled=false`
- **THEN** module-default recovery MUST change the row to `source='db'` and
  `enabled=true`
- **AND** the row's cron MUST remain the last TOML-declared cadence rather than
  falling back to the registered module-default cadence
- **AND** its dispatch mode, prompt/job name, job arguments, complexity, and
  `next_run_at` MUST remain unchanged

#### Scenario: Recovered consolidation submits partitioned obligation goals

- **WHEN** a `memory_consolidation` row has been recovered and later falls due
  with pending episodes
- **THEN** the recovery itself MUST have admitted no goal and started no session
- **AND** the due job MUST submit one obligation admission per
  `(tenant_id, butler, partition)` with the git-declared consolidation
  capability set, passing no Spawner
- **AND** an empty pending-episode set MUST admit no goal

#### Scenario: Disabled TOML cleanup is recovered like any other default

- **WHEN** a memory-enabled butler has removed its TOML
  `memory_episode_cleanup` declaration and, after TOML synchronization, the
  same-named row is `source='toml'` and `enabled=false`
- **THEN** module-default recovery MUST change the row to `source='db'` and
  `enabled=true` with one recovery audit entry
- **AND** its cron, dispatch mode, job name, job arguments, complexity, and
  `next_run_at` MUST remain unchanged
- **AND** a same-named `source='db'` cleanup row, enabled or disabled, MUST be
  left unchanged
- **AND** the recovered row MUST run only the ordinary bounded sweep and admit
  no goal

#### Scenario: Every memory-enabled butler has a working consolidation path

- **WHEN** a butler enables `[modules.memory]`
- **THEN** `memory_consolidation`, `memory_episode_cleanup`,
  `memory_purge_superseded`, `memory_decay_sweep`, and
  `memory_ann_observability` MUST all resolve to a registered deterministic-job
  handler for that butler — a module-registered schedule naming a job with no
  registered handler for that butler is a defect, not an acceptable
  configuration
- **AND** `memory_episode_cleanup`, `memory_purge_superseded`,
  `memory_decay_sweep`, and `memory_ann_observability` MUST complete without
  admitting a goal, while `memory_consolidation` MUST reach model judgment only
  through its partitioned obligation admissions

#### Scenario: Bounded incremental backlog drain

- **WHEN** a butler has episodes in `consolidation_status = 'pending'` beyond
  what its steady-state `memory_consolidation` cadence clears
- **THEN** a `memory_consolidation_backfill` schedule MUST claim additional
  pending episodes on a tighter cadence, bounded to a configurable batch size
  per run (never one unbounded transaction)
- **AND** the claimed episodes MUST be submitted through the same partitioned
  obligation admissions as `memory_consolidation`, never a Spawner session
- **AND** episodes in `consolidation_status = 'dead_letter'` MUST NOT be
  reclaimed by this or any consolidation pass

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
## ADDED Requirements

### Requirement: Retention recovery and observation never authorize historical deletion

Memory retention recovery SHALL be limited to the scheduler control-plane
transition defined by `core-scheduler` and the read-only expired-retention
observation defined by `dashboard-api`. An expired-retention observation MUST
not invoke cleanup, toggle a schedule, enqueue a backfill, change an episode's
expiry, delete a row, send a notification, mutate provenance/evidence, or
admit a goal. Ordinary episode cleanup remains a deterministic job under the
goal runtime; it gains no model judgment and no goal-granted authority.

ID: REQ-memory-retention-policy-011
Source: bu-kqnum.13 expired-retention deadman; RFC 0038 D2
Scope: v1-mandatory

The retention observation SHALL use the exact eligibility predicate of ordinary
episode cleanup, currently `expires_at < now()`, rather than a separate expiry
definition. A row with `expires_at IS NULL` is not eligible for this
observation's denominator or numerator. The approved v1 threshold is zero
rows matching the cleanup predicate: a complete source with one or more such
rows is retention-degraded.

Any historical episode operation remains outside this capability until the
separate provenance contract establishes durable-evidence or truthful
source-expired behavior for affected readers and links. A switchboard
historical drain additionally requires explicit owner/ops authorization,
verified backup/restore evidence, a fixed-cutoff dry run, bounded atomic
batches, and the applicable retention-disposition decision. The ordinary
cleanup handler SHALL NOT be repurposed as an unbounded historical backfill.

#### Scenario: Expired-retention observation uses the cleanup population

- **WHEN** a memory source reports its expired-retained episode statistics
- **THEN** its numerator SHALL count exactly rows matching `expires_at < now()`
- **AND** its eligible denominator SHALL count rows with `expires_at IS NOT NULL`
- **AND** the observation SHALL not inspect or return episode content or IDs

#### Scenario: Observation does not trigger cleanup

- **WHEN** the expired-retention count is non-zero or exceeds the approved
  threshold
- **THEN** the statistics path SHALL remain read-only
- **AND** it SHALL not invoke `memory_episode_cleanup`, re-enable a cleanup
  schedule, alter a retention policy, delete an episode, or admit a goal

#### Scenario: Historical drain remains fenced by provenance and owner authorization

- **WHEN** a retained historical episode is selected only because it matches
  the cleanup predicate
- **THEN** this retention-recovery capability SHALL not delete, quarantine, or
  otherwise mutate that episode
- **AND** no historical drain SHALL begin until the separate provenance
  contract and explicit owner/ops gate have both been satisfied

#### Scenario: Ordinary scheduled cleanup remains distinct from a backfill

- **WHEN** an operator has not separately authorized a bounded historical drain
- **THEN** the ordinary cleanup handler SHALL retain its normal scheduled
  behavior only
- **AND** it SHALL not be called as an unbounded catch-up mechanism for the
  historical population observed by this capability
