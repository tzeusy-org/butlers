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

#### Scenario: Fading is recorded on the validity column, not only metadata

- **WHEN** a fact's effective confidence falls between the expiry and fading thresholds
- **THEN** the fact's `validity` column MUST be set to `'fading'` (not merely a `metadata.status` key), since every reader of the fading count — the dashboard API (`GET /api/memory/stats`, `GET /api/memory/facts?validity=fading`) and the `memory_stats` MCP tool — queries the `validity` column directly
- **AND** rules (which have no `validity` column, only `maturity`) continue to record fading via `metadata.status = 'fading'`

#### Scenario: Fading facts remain live for retrieval, supersession, and uniqueness

- **WHEN** a fact has `validity = 'fading'`
- **THEN** it MUST still be returned by recall/search (`memory_search`, `memory_recall`, `memory_context`), entity-scoped reads (`memory_entity_neighbors`, entity fact counts, profile/preference facts), and the discovery catalog backfill — effective confidence is a scoring weight (see the Retrieval composite-score formula), not a hard retrieval cutoff
- **AND** it MUST still be found by `store_fact`'s supersession lookup, so a fresh write for the same predicate supersedes the fading fact instead of leaving it orphaned alongside a new active row
- **AND** it MUST still be repointed (entity merge) or retracted (entity forget, `memory_forget`) alongside `'active'` facts, so it is never silently stranded

#### Scenario: A previously-fading fact recovers or expires on the next sweep

- **WHEN** the decay sweep runs
- **THEN** its source query MUST include facts with `validity IN ('active', 'fading')` (not `'active'` only), so an already-fading fact is re-evaluated every run
- **AND** if its recomputed effective confidence has since risen back to or above the fading threshold (e.g. after `memory_confirm` resets the decay clock), its `validity` MUST be set back to `'active'`
- **AND** if its recomputed effective confidence has fallen below the expiry threshold, it MUST progress to `'expired'` per the existing expiry scenarios above

#### Scenario: One-time backfill for facts marked fading before this contract existed

- **WHEN** a fact has `validity = 'active'` and `metadata->>'status' = 'fading'` (written by a decay sweep that predates this contract)
- **THEN** a migration MUST correct it to `validity = 'fading'` with the stale `metadata.status` key removed
- **AND** the migration MUST NOT touch facts whose `validity` is already `'fading'`, or any non-`'active'` validity (e.g. `'retracted'`, `'superseded'`, `'expired'`), even if a stale `metadata.status` key happens to be present

---

### Requirement: Forgotten rules are excluded from live rule readers

Rules have no `validity` column. Their terminal soft-delete state is the boolean `metadata->>'forgotten'` flag, written by `forget_memory` and by the decay sweep's expiry branch. Every reader that reports a rule count or returns a list/search of rules MUST filter on this same flag so a forgotten rule is never counted or displayed as a live belief — mirroring, for rules, the same "one vocabulary, every reader agrees" contract the validity column establishes for facts.

#### Scenario: Dashboard maturity counts and the Proven-rules KPI exclude forgotten rules

- **WHEN** `GET /api/memory/stats` computes `candidate_rules`, `established_rules`, `proven_rules`, or `anti_pattern_rules`
- **THEN** each query MUST include `(metadata->>'forgotten')::boolean IS NOT TRUE`
- **AND** `total_rules` remains an unfiltered raw table count (matching the `total_facts` convention), since it reports storage volume, not live-belief count

#### Scenario: The rules register excludes forgotten rules by default

- **WHEN** `GET /api/memory/rules` is called without an explicit `forgotten` parameter
- **THEN** the response MUST exclude rules with `metadata->>'forgotten' = 'true'`
- **AND** passing `?forgotten=true` MUST return only forgotten rules, for auditing
- **AND** `GET /api/memory/rules/{id}` (fetch by ID) remains unfiltered, mirroring how a fact stays fetchable by ID regardless of validity

#### Scenario: The inspect search bar excludes forgotten rules

- **WHEN** `GET /api/memory/inspect?kind=rule` (or `kind` omitted) searches rules
- **THEN** forgotten rules MUST NOT appear in the results, with no override — matching the MCP `memory_search`/`memory_recall` semantic and keyword search paths, which already hard-exclude forgotten rules unconditionally

#### Scenario: MCP and API readers agree on the forgotten predicate

- **WHEN** the `memory_stats` MCP tool computes any of its `rules.candidate`, `rules.established`, `rules.proven`, or `rules.anti_pattern` counts
- **THEN** each query MUST include the same `(metadata->>'forgotten')::boolean IS NOT TRUE` predicate the dashboard API uses, so the MCP and API surfaces never disagree about whether a given rule counts as live

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
consolidation, episode cleanup, superseded-fact purge, a bounded consolidation
backfill, and local ANN observability) as scheduled jobs for every butler that enables
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
  `memory_purge_superseded`, `memory_decay_sweep`, and
  `memory_ann_observability` MUST all resolve to a
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

### Requirement: Episode cleanup is bounded and consolidation-aware

The `memory_episode_cleanup` sweep deletes expired episodes, but it MUST NOT
delete an expired episode that is still awaiting consolidation until a grace
window has elapsed, and it MUST bound every delete so a large accumulated
backlog drains incrementally rather than in one table-wide-locking statement.
Before deleting a reapable episode, the sweep MUST rely on the memory module's
content-free source-tombstone invariant: it MUST NOT null a durable fact or
rule source identifier, retain raw episode content, or perform a historical
catch-up drain solely to establish provenance.
The set of episodes the sweep is permitted to delete for expiry — expired AND
(`consolidation_status <> 'pending'` OR expired beyond the grace window) — is
the single source of truth for what counts as un-reaped cleanup lag; the
dashboard's expired-retention observation MUST report against that same
predicate so an episode the sweep is deliberately still holding is never
surfaced as a degraded/lagging source.

Because the sweep can no longer trigger an unbounded destructive catch-up, a
disabled `memory_episode_cleanup` schedule is recovered like any other
module-default schedule per "TOML overrides cadence, not existence" above — it
is not exempt from module-default recovery.

#### Scenario: Expired-but-pending episode is protected within the grace window

- **WHEN** episode cleanup runs and an episode is past its `expires_at` but is
  still in `consolidation_status = 'pending'` and within the grace window
- **THEN** the episode MUST NOT be deleted (a lagging consolidator must never
  lose an un-extracted observation)
- **AND** an expired non-pending episode (`consolidated`, `failed`, or
  `dead_letter`) MUST be deleted as soon as it expires

#### Scenario: Stuck pending episode is reaped past the grace window

- **WHEN** an episode is `consolidation_status = 'pending'` and expired beyond
  the grace window
- **THEN** episode cleanup MUST delete it, so the episodes table cannot grow
  without bound behind a broken or disabled consolidator

#### Scenario: Cleanup drains a large backlog in bounded batches

- **WHEN** episode cleanup runs against a butler holding far more reapable
  expired episodes than one batch
- **THEN** it MUST delete them in bounded per-statement batches (never one
  unbounded delete), draining the full reapable backlog across the run

#### Scenario: A disabled episode-cleanup schedule is recovered

- **WHEN** a butler's `memory_episode_cleanup` schedule is a disabled TOML
  orphan (its `[[butler.schedule]]` block was removed) and the memory module's
  `on_startup` runs
- **THEN** the schedule MUST be re-enabled as a module-registered default,
  exactly as any other maintenance schedule would be
- **AND** an operator's explicit DB-level disable (`source='db'`) MUST still be
  left untouched

#### Scenario: Normal bounded cleanup preserves expired-source evidence

- **WHEN** the cleanup sweep deletes one reapable episode in its normal bounded
  batch
- **THEN** durable facts, rules, and generic links associated with that episode
  MUST remain attributable through content-free `expired` source evidence
- **AND** the sweep MUST NOT retain the deleted episode's raw content or select,
  delete, or mutate historical episodes beyond its normal bounded cleanup

### Requirement: Live-safe local HNSW observability

The `memory_ann_observability` scheduled job SHALL report aggregate health for
the local HNSW indexes on `episodes`, `facts`, and `rules`. It SHALL use the
active memory module's pool, including a configured private `memory_schema`,
and SHALL NOT inspect the IVFFlat-backed `public.memory_catalog`.

The job SHALL be read-only. It MUST NOT run `VACUUM`, `REINDEX`, a write, or an
unbounded exact vector scan. Its scheduler result SHALL contain only aggregate
health, recall, churn, and statistics-freshness values; embeddings, memory
text, IDs, and tenant IDs MUST NOT be emitted.

#### Scenario: Small local corpus receives sampled exact recall

- **WHEN** PostgreSQL's catalogue estimates a local searchable memory table at
  or below the monitor's hard exact-corpus limit
- **THEN** the job MAY select a bounded physical-page sample of query vectors
  and compare forced-HNSW top-k results against exact top-k results
- **AND** the result MUST report only aggregate `recall_at_k` and the number
  of queries compared

#### Scenario: Large local corpus reports degraded recall honestly

- **WHEN** PostgreSQL's catalogue estimates a local searchable memory table
  above the hard exact-corpus limit, or its heap relation exceeds the monitor's
  hard physical-page limit
- **THEN** the job MUST skip both sampling and exact comparison for that table
- **AND** the result MUST report recall as `degraded` with a reason that the
  corpus exceeds the exact-recall cap, while still reporting churn and
  statistics freshness

#### Scenario: Churn requires no maintenance action from the monitor

- **WHEN** lifecycle actions such as decay, retraction, cleanup, or purge have
  accumulated update/delete pressure on a local memory table
- **THEN** the job MUST report aggregate dead-tuple and modified-since-analyze
  ratios with threshold-based health
- **AND** it MUST not automatically vacuum, reindex, or otherwise mutate the
  table
