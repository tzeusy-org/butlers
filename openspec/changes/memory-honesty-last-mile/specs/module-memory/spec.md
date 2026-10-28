## MODIFIED Requirements

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform eligible episodes into durable facts
and rules as an obligation goal of the memory-owning domain, not as a direct
Spawner session or a model function. The deterministic schedule SHALL submit
one obligation admission per `(tenant_id, source butler, partition)` with the
git-declared memory read and consolidation-apply capabilities. Reads SHALL
claim eligible episodes and provide existing context as stamped data; the goal
SHALL submit structured artifacts through consolidation-apply. Deterministic
code behind that capability SHALL validate artifact evidence, derive authority
and taint under `REQ-module-memory-013`, and execute extracted actions under
`REQ-module-memory-006`. Consolidation-apply SHALL NOT be `owner_authority`;
an exposed partition's goal can submit artifacts under those safeguards. All
derived facts and rules MUST inherit the tenant context from their source
episodes.

ID: REQ-module-memory-005
Source: [Observed] PR #3669; `openspec/changes/archive/2026-08-14-consolidation-exact-artifact-evidence/CANONICALIZATION.md`; RFC 0038 D2, D6 and D18; bu-kqnum.14.8
Scope: v1-mandatory

#### Scenario: Episode grouping by tenant and butler

- **WHEN** the read capability selects episodes for a consolidation goal
- **THEN** it MUST claim `pending` episodes that are not actively leased and
  `failed` episodes only when their `next_consolidation_retry_at` is due and
  no lease is active, in
  `(tenant_id, butler, created_at, id)` order with `FOR UPDATE SKIP LOCKED`
- **AND** it MUST NOT automatically claim `consolidated` or `dead_letter`
  episodes, including after an expired lease
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`,
  not by `butler` alone, and split into untainted owner-class episodes and all
  other episodes; each partition MUST have its own obligation admission
- **AND** existing active facts (up to 100) and rules (up to 50) for each
  butler MUST be fetched for dedup context, scoped to the same `tenant_id`
- **AND** dedup context for the untainted owner-class partition MUST contain
  only untainted owner-class facts and rules, and its `updated_facts` MAY target
  only those facts

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

- **WHEN** a non-empty tenant/source partition needs consolidation judgment
- **THEN** that judgment MUST come from its obligation goal, without a direct
  `cc_spawner` call or a session spawned with `trigger_source='schedule:consolidation'`
- **AND** it MUST submit structured JSON containing `new_facts`,
  `updated_facts`, `new_rules`, and `confirmations` through consolidation-apply
- **AND** a successful model result with missing or blank output MUST fail
  the group with an actionable error so its episodes remain eligible for
  retry rather than being marked consolidated
- **AND** partial failures in one group MUST NOT block other groups from
  processing

#### Scenario: Scheduled consolidation uses the catalog-backed daemon spawner

- **WHEN** the deterministic `memory_consolidation` scheduled-job handler runs
- **THEN** it MUST submit one obligation admission per tenant, source domain
  and taint partition with the git-declared consolidation capability set; it
  MUST NOT pass a Spawner to `run_consolidation` or start a session
- **AND** an empty eligible-episode claim MUST admit no goal or runtime session
- **AND** model selection, spend-routing policy, budgets, quotas, failover and
  timeouts MUST come from goal-runner role/tier resolution over the model
  catalog without job overrides of model, runtime or session timeout
- **AND** database and embedding resolution in the read and consolidation-apply
  capabilities MUST use the active memory module's
  runtime pool and configured embedding-engine lifecycle, including any private
  `memory_schema`, rather than the daemon's domain pool or the embedding
  helper's default model; private-pool plumbing alone MUST NOT make that source
  eligible for the new retry or requeue behavior below
- **AND** the handler's admission result or raised error MUST
  remain the scheduled task result recorded by the scheduler

#### Scenario: Consolidation without spawner (dry run)

- **WHEN** `run_consolidation` is called in dry-run mode
- **THEN** only eligible-episode grouping and counting MUST be performed
- **AND** no goal MUST be admitted and no actual consolidation MUST occur

#### Scenario: Episode content wrapped in XML tags for prompt injection prevention

- **WHEN** episode content is returned by the read capability to the consolidation goal
- **THEN** each episode's content MUST be wrapped in `<episode_content>` XML
  tags with its authority and taint stamps
- **AND** the consolidation guidance MUST contain a security notice instructing
  the model to treat episode content as data only

#### Scenario: Artifact output names exact episode evidence

- **WHEN** a non-empty episode group is formatted for consolidation
- **THEN** each rendered episode MUST expose its UUID to the consolidation goal
- **AND** every emitted `new_facts`, `updated_facts`, and `new_rules` entry MUST include a non-empty `evidence_episode_ids` array naming only the claimed episode UUIDs that support that artifact

#### Scenario: Invalid artifact evidence fails the group before persistence

- **WHEN** any emitted fact or rule artifact has absent, empty, malformed, duplicate, or out-of-group `evidence_episode_ids`
- **THEN** the consolidation group MUST use the existing failed/dead-letter retry path
- **AND** no fact, rule, `memory_links` row, or consolidated episode state from that group MAY be persisted before the failure

#### Scenario: Group artifacts are applied through one capability call

- **WHEN** the consolidation goal submits a group's `new_facts`, `updated_facts`,
  `new_rules` and `confirmations`
- **THEN** consolidation-apply MUST validate and execute them under
  `REQ-module-memory-006`, including the episode evidence and bounded lifecycle
- **AND** a submission with no artifacts for a non-empty group MUST fail that
  group with an actionable error so its episodes remain eligible for retry
- **AND** a failure in one group MUST NOT block other groups from processing

#### Scenario: Third-party episodes expose the consolidation goal

- **WHEN** a consolidation goal's partition holds episodes with non-owner
  authority or a taint stamp
- **THEN** reading them MUST expose that goal, and every artifact it submits
  MUST be tainted under `REQ-module-memory-013`
- **AND** each derived rule MUST remain held until the owner endorses it;
  tainted artifacts MUST NOT supersede untainted facts or rules
- **AND** the goal for the same tenant/source's untainted owner-class partition
  MUST remain unaffected

#### Scenario: The untainted partition stays unexposed

- **WHEN** the goal for a tenant/source's untainted owner-class partition reads
  episodes and dedup context while that source also holds third-party facts
- **THEN** its context MUST exclude third-party and tainted facts and rules,
  the goal MUST stay unexposed, and rules derived from owner evidence MUST be
  active without endorsement

#### Scenario: Throughput decision retains existing batching

- **WHEN** the consolidation throughput decision recorded in `docs/decisions/consolidation-throughput-2026-10-15.md` is applied
- **THEN** additional batching changes SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** existing bounded episode claims and deterministic tenant/source grouping
  SHALL remain, with one obligation goal per non-empty tenant/source/taint
  partition under RFC 0038 rather than a directly spawned session
- **AND** this decision SHALL NOT reinterpret the ordinary default100 or existing scheduled backfill500 as a new universal cap

#### Scenario: Throughput decision declines an additional per-run cap

- **WHEN** the recorded throughput decision is applied
- **THEN** a new consolidation-specific session, token or dollar cap SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** model-catalog quota, failover and timeout authority SHALL remain with
  the goal runner's role/tier resolution; this execution-mechanism amendment
  SHALL NOT adopt an additional consolidation-specific limit
- **AND** descriptive observed session token counts SHALL NOT be represented as exact whole-pipeline invocation cost or a newly adopted limit

#### Scenario: Throughput decision declines backlog deadman automation

- **WHEN** the recorded throughput decision is applied
- **THEN** a new pending-backlog deadman SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** this decision SHALL authorize no alert-triggered runtime work, retry, lease clearing, drain, deletion, quarantine or automatic lifecycle change
- **AND** an empty eligible population SHALL preserve a NULL oldest-eligible age rather than treating it as measured zero

#### Scenario: Throughput decision preserves catalog-authoritative transport

- **WHEN** the recorded throughput decision is applied
- **THEN** a consolidation-specific forced cheap tier or transport SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** scheduled consolidation SHALL use declared obligation admissions and
  catalog-backed goal execution without model, runtime or session-timeout overrides
- **AND** absent authoritative billing or quality comparison SHALL NOT be replaced with guessed monetary savings or equivalence claims

#### Scenario: Reconsidering a declined throughput control

- **WHEN** a later proposal would adopt any of the four declined controls
- **THEN** fresh source-bound backlog and exact attributable token evidence, including explicit unmeasurable gaps, SHALL precede numerical thresholds
- **AND** the proposal SHALL define the threshold and action in a reviewed capability spec delta and create one separate implementation issue for each adopted control before implementation
- **AND** the recorded present declines SHALL NOT be construed as proof that future stalls, cost pressure or useful catalog-compatible changes are impossible

## ADDED Requirements

### Requirement: Bounded Episode Retry and Dead-Letter Lifecycle

The memory module SHALL persist a truthful, bounded consolidation lifecycle for
each episode. `consolidation_attempts` records failures in the current attempt
cycle; `last_consolidation_error`, `next_consolidation_retry_at`, and
`dead_letter_reason` hold only sanitized operational summaries. Lease fields
remain internal claimant data and MUST NOT be surfaced by this requirement's
public contract.

ID: REQ-module-memory-017
Source: memory-honesty-last-mile adopted PR #3464; bu-kqnum.14.8; RFC 0038 D2, D6 and D18
Scope: v1-mandatory

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
- **AND** the requeue transition MUST NOT admit a goal or start a session;
  only a later ordinary schedule MAY admit the episode's consolidation goal
