# Memory Retention Policy

## Purpose

The memory retention policy spec defines the `memory_policies` table for per-retention-class lifecycle configuration, the `rule_applications` audit table, retention-class-aware decay sweeps, and the acceptance of retention class parameters on all memory store operations.
## Requirements
### Requirement: Memory retention policy table

A `memory_policies` table SHALL define per-retention-class lifecycle configuration. Each retention class specifies TTL, decay behavior, archival rules, and summarization eligibility. The table is the authoritative source for memory lifecycle parameters.

#### Scenario: Memory policies table schema

- **WHEN** the retention policy migration runs
- **THEN** a `memory_policies` table MUST be created with columns: `retention_class` (TEXT PRIMARY KEY), `ttl_days` (INTEGER nullable — NULL means no TTL), `decay_rate` (DOUBLE PRECISION NOT NULL), `min_retrieval_confidence` (DOUBLE PRECISION NOT NULL), `archive_before_delete` (BOOLEAN NOT NULL DEFAULT false), `allow_summarization` (BOOLEAN NOT NULL DEFAULT true)

#### Scenario: Default retention classes seeded

- **WHEN** the retention policy migration runs
- **THEN** the following retention classes MUST be seeded:

| retention_class | ttl_days | decay_rate | min_retrieval_confidence | archive_before_delete | allow_summarization |
|---|---|---|---|---|---|
| transient | 7 | 0.1 | 0.1 | false | false |
| episodic | 30 | 0.03 | 0.15 | false | true |
| operational | NULL | 0.008 | 0.2 | false | true |
| personal_profile | NULL | 0.0 | 0.0 | true | false |
| health_log | NULL | 0.002 | 0.1 | true | true |
| financial_log | NULL | 0.002 | 0.1 | true | false |
| rule | NULL | 0.01 | 0.2 | false | true |
| anti_pattern | NULL | 0.0 | 0.0 | false | false |

#### Scenario: Policy lookup for episode TTL

- **WHEN** `store_episode` is called with `retention_class='episodic'`
- **THEN** the episode's `expires_at` MUST be set to `now() + interval '30 days'` (from the policy's `ttl_days`)
- **AND** if the retention_class has `ttl_days = NULL`, the episode MUST have `expires_at = NULL` (no expiry)

#### Scenario: Policy lookup for unknown retention class

- **WHEN** a memory is stored with a `retention_class` not present in `memory_policies`
- **THEN** the storage layer MUST fall back to the type-specific default retention class (transient for episodes, operational for facts, rule for rules)
- **AND** the fallback MUST be logged as a warning

---

### Requirement: Rule application audit tracking

A `rule_applications` table SHALL record each time a rule is applied during a runtime session, including the outcome and context. This provides a learning loop for rule effectiveness beyond simple counter increments.

#### Scenario: Rule applications table schema

- **WHEN** the retention policy migration runs
- **THEN** a `rule_applications` table MUST be created with columns: `id` (UUID PK DEFAULT gen_random_uuid()), `tenant_id` (TEXT NOT NULL), `rule_id` (UUID NOT NULL, FK to rules ON DELETE CASCADE), `session_id` (UUID nullable), `request_id` (TEXT nullable), `outcome` (TEXT NOT NULL — one of 'helpful', 'harmful', 'neutral', 'skipped'), `notes` (JSONB NOT NULL DEFAULT '{}'), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now())

#### Scenario: Recording a rule application

- **WHEN** `memory_mark_helpful` or `memory_mark_harmful` is called
- **THEN** a `rule_applications` row MUST be inserted with the rule_id, outcome ('helpful' or 'harmful'), and any available session/request context
- **AND** the existing counter increment logic MUST continue to operate as before (rule_applications is additive audit, not a replacement)

#### Scenario: Querying rule application history

- **WHEN** a dashboard or diagnostic tool queries rule applications
- **THEN** the query MUST be filterable by `tenant_id`, `rule_id`, `outcome`, and time range (`created_at`)
- **AND** results MUST be ordered by `created_at DESC`

---

### Requirement: Retention-class-aware decay sweep

The decay sweep SHALL consult `memory_policies` to determine per-class thresholds and behavior instead of using hardcoded constants.

#### Scenario: Policy-driven fading threshold

- **WHEN** the decay sweep processes a fact with `retention_class = 'health_log'`
- **THEN** the fading threshold MUST be read from `memory_policies WHERE retention_class = 'health_log'` using `min_retrieval_confidence`
- **AND** the expiry threshold MUST be `min_retrieval_confidence * 0.25` (25% of retrieval threshold)

#### Scenario: Policy-driven archival before deletion

- **WHEN** a memory's effective confidence falls below the expiry threshold and the policy has `archive_before_delete = true`
- **THEN** the memory MUST be archived (metadata augmented with `archived_at` and `archived_content`) before its validity is set to `'expired'`
- **AND** if archival fails, the memory MUST NOT be expired (fail-closed for archival-required classes)

#### Scenario: Fallback for missing policy

- **WHEN** a memory has a `retention_class` that does not exist in `memory_policies`
- **THEN** the decay sweep MUST use the hardcoded defaults (fading at 0.2, expiry at 0.05)
- **AND** a warning MUST be logged

---

### Requirement: Retention class on memory store operations

All memory write tools SHALL accept an optional `retention_class` parameter that is persisted on the stored row. The retention class determines the memory's lifecycle policy.

#### Scenario: store_episode with retention_class

- **WHEN** `store_episode` is called with `retention_class='episodic'`
- **THEN** the episode MUST be stored with `retention_class = 'episodic'`
- **AND** `expires_at` MUST be computed from the `episodic` policy's `ttl_days`

#### Scenario: store_fact with retention_class

- **WHEN** `store_fact` is called with `retention_class='health_log'`
- **THEN** the fact MUST be stored with `retention_class = 'health_log'`
- **AND** the fact's lifecycle (decay, archival, summarization) MUST follow the `health_log` policy

#### Scenario: store_rule with retention_class

- **WHEN** `store_rule` is called with `retention_class='rule'`
- **THEN** the rule MUST be stored with `retention_class = 'rule'`

#### Scenario: Default retention classes by memory type

- **WHEN** a memory is stored without an explicit `retention_class`
- **THEN** episodes MUST default to `'transient'`
- **AND** facts MUST default to `'operational'`
- **AND** rules MUST default to `'rule'`

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

