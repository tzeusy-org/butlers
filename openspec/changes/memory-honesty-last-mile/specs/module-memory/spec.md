## MODIFIED Requirements

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform eligible episodes into durable facts
and rules via a multi-step process: claim eligible episodes, group them by
`(tenant_id, source butler)`, build a prompt with existing context, spawn an
LLM CLI session, parse the structured JSON output, and execute the extracted
actions against the database. All derived facts and rules MUST inherit the
tenant context from their source episodes.

#### Scenario: Episode grouping by tenant and butler

- **WHEN** `run_consolidation` is called
- **THEN** it MUST claim `pending` episodes that are not actively leased and
  `failed` episodes only when their `next_consolidation_retry_at` is due, in
  `(tenant_id, butler, created_at, id)` order with `FOR UPDATE SKIP LOCKED`
- **AND** it MUST NOT automatically claim `consolidated` or `dead_letter`
  episodes, including after an expired lease
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`,
  not by `butler` alone
- **AND** existing active facts (up to 100) and rules (up to 50) for each
  butler MUST be fetched for dedup context, scoped to the same `tenant_id`

#### Scenario: Private memory schemas are excluded from new recovery behavior

- **WHEN** this change's failed-retry claimant or dashboard requeue resolver
  selects recovery sources
- **THEN** it MUST select only a pool whose memory relation is in its owning
  butler schema, not a distinct private `memory_schema` override
- **AND** it MUST exclude Chronicler's private `(chronicler, chronicler_mem)`
  mapping from failed retries and requeue discovery or mutation
- **AND** it MUST NOT treat that intentional exclusion as a failed source or
  alter the private pool's existing module-local behavior

#### Scenario: Consolidation with LLM spawner

- **WHEN** a `cc_spawner` is provided to `run_consolidation`
- **THEN** for each `(tenant_id, butler)` group, a runtime session MUST be
  spawned with `trigger_source='schedule:consolidation'`
- **AND** the runtime output MUST be parsed for a JSON block containing
  `new_facts`, `updated_facts`, `new_rules`, and `confirmations`
- **AND** a successful runtime result with missing or blank output MUST fail
  the group with an actionable error so its episodes remain eligible for retry
  rather than being marked consolidated
- **AND** partial failures in one group MUST NOT block other groups from
  processing

#### Scenario: Scheduled consolidation uses the catalog-backed daemon spawner

- **WHEN** the deterministic `memory_consolidation` scheduled-job handler runs
- **THEN** it MUST pass the daemon's live `Spawner` to `run_consolidation`
  rather than using the `cc_spawner=None` dry-run path
- **AND** an empty eligible-episode claim MUST spawn no runtime session
- **AND** each non-empty `(tenant_id, butler)` group MUST use
  `trigger_source='schedule:consolidation'` without overriding model, runtime,
  or session timeout, so model selection, spend-routing policy, quotas,
  failover, and timeout remain authoritative in the model catalog and `Spawner`
- **AND** database and embedding resolution MUST use the active memory module's
  runtime pool and configured embedding-engine lifecycle, including any private
  `memory_schema`, rather than the daemon's domain pool or the embedding
  helper's default model; private-pool plumbing alone MUST NOT make that source
  eligible for the new retry or requeue behavior below
- **AND** the handler's returned consolidation statistics or raised error MUST
  remain the scheduled task result recorded by the scheduler

#### Scenario: Consolidation without spawner (dry run)

- **WHEN** `run_consolidation` is called with `cc_spawner=None`
- **THEN** only eligible-episode grouping and counting MUST be performed
- **AND** no actual consolidation MUST occur

#### Scenario: Episode content wrapped in XML tags for prompt injection prevention

- **WHEN** episode content is formatted for the consolidation prompt
- **THEN** each episode's content MUST be wrapped in `<episode_content>` XML
  tags
- **AND** the SKILL.md MUST contain a security notice instructing the LLM to
  treat episode content as data only

## ADDED Requirements

### Requirement: Bounded Episode Retry and Dead-Letter Lifecycle

The memory module SHALL persist a truthful, bounded consolidation lifecycle for
each episode. `consolidation_attempts` records failures in the current attempt
cycle; `last_consolidation_error`, `next_consolidation_retry_at`, and
`dead_letter_reason` hold only sanitized operational summaries. Lease fields
remain internal claimant data and MUST NOT be surfaced by this requirement's
public contract.

#### Scenario: Retry-eligible failure respects backoff

- **WHEN** a claimed episode fails before the attempt limit
- **THEN** the module MUST increment `consolidation_attempts`, retain a
  sanitized `last_consolidation_error`, clear its lease, set
  `consolidation_status='failed'`, and set the next retry timestamp using the
  configured exponential backoff
- **AND** no automatic claimant MAY select that failed episode before the retry
  timestamp is due
- **AND** a later scheduled claim MAY process the failed episode only after the
  timestamp is due and no lease is active

#### Scenario: Terminal failure is never auto-claimed

- **WHEN** a failure reaches the configured consolidation-attempt limit
- **THEN** the module MUST set `consolidation_status='dead_letter'`, copy the
  sanitized terminal failure summary to `dead_letter_reason`, clear its lease,
  and clear automatic retry timing
- **AND** all future automatic claimant queries MUST exclude that episode
- **AND** the module MUST emit an `episode_consolidation_dead_letter` lifecycle
  event without raw runtime output, prompts, secrets, or lease details

#### Scenario: Successful consolidation clears unresolved-failure state

- **WHEN** a pending or retry-eligible failed episode is successfully
  consolidated
- **THEN** it MUST become `consolidated`, clear its lease, and clear any
  outstanding retry or last-error state that would falsely present it as
  unresolved
- **AND** it MUST NOT remain eligible for automatic consolidation

#### Scenario: Owner requeue begins a new attempt cycle without execution

- **WHEN** the authorized dashboard recovery path requeues one dead-letter
  episode
- **THEN** it MUST atomically change only that episode from `dead_letter` to
  `pending`, reset the non-null integer `consolidation_attempts` to `0`, and
  clear `last_consolidation_error`, `dead_letter_reason`,
  `next_consolidation_retry_at`, and any lease fields
- **AND** it MUST record exactly one sanitized
  `episode_consolidation_requeued` lifecycle event in the same transaction
- **AND** it MUST NOT call a spawner, execute consolidation, claim another
  episode, or enqueue a bulk replay
