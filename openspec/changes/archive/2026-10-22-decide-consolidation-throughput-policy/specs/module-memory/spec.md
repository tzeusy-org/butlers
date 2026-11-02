## MODIFIED Requirements

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: fetch pending episodes, group by `(tenant_id, source butler)`, build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, validate artifact evidence, and execute the extracted actions against the database. All derived facts and rules MUST inherit the tenant context from their source episodes.

ID: REQ-module-memory-005
Source: [Observed] PR #3669; `openspec/changes/archive/2026-08-14-consolidation-exact-artifact-evidence/CANONICALIZATION.md`
Scope: v1-mandatory

#### Scenario: Episode grouping by tenant and butler

- **WHEN** `run_consolidation` is called
- **THEN** episodes with `consolidation_status='pending'` MUST be fetched ordered by `(tenant_id, butler, created_at, id)` with `FOR UPDATE SKIP LOCKED`
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`, not by `butler` alone
- **AND** existing active facts (up to 100) and rules (up to 50) for each butler MUST be fetched for dedup context, scoped to the same `tenant_id`

#### Scenario: Consolidation with LLM spawner

- **WHEN** a `cc_spawner` is provided to `run_consolidation`
- **THEN** for each `(tenant_id, butler)` group, a runtime session MUST be spawned with `trigger_source='schedule:consolidation'`
- **AND** the runtime output MUST be parsed for a JSON block containing `new_facts`, `updated_facts`, `new_rules`, and `confirmations`
- **AND** a successful runtime result with missing or blank output MUST fail the group with an actionable error so its episodes remain eligible for retry rather than being marked consolidated
- **AND** partial failures in one group MUST NOT block other groups from processing

#### Scenario: Artifact output names exact episode evidence

- **WHEN** a non-empty episode group is formatted for consolidation
- **THEN** each rendered episode MUST expose its UUID to the runtime session
- **AND** every emitted `new_facts`, `updated_facts`, and `new_rules` entry MUST include a non-empty `evidence_episode_ids` array naming only the claimed episode UUIDs that support that artifact

#### Scenario: Invalid artifact evidence fails the group before persistence

- **WHEN** any emitted fact or rule artifact has absent, empty, malformed, duplicate, or out-of-group `evidence_episode_ids`
- **THEN** the consolidation group MUST use the existing failed/dead-letter retry path
- **AND** no fact, rule, `memory_links` row, or consolidated episode state from that group MAY be persisted before the failure

#### Scenario: Scheduled consolidation uses the catalog-backed daemon spawner

- **WHEN** the deterministic `memory_consolidation` scheduled-job handler runs
- **THEN** it MUST pass the daemon's live `Spawner` to `run_consolidation` rather than using the `cc_spawner=None` dry-run path
- **AND** an empty pending-episode claim MUST spawn no runtime session
- **AND** each non-empty `(tenant_id, butler)` group MUST use `trigger_source='schedule:consolidation'` without overriding model, runtime, or session timeout, so model selection, spend-routing policy, quotas, failover, and timeout remain authoritative in the model catalog and `Spawner`
- **AND** database and embedding resolution MUST use the active memory module's runtime pool and configured embedding-engine lifecycle, including any private `memory_schema`, rather than the daemon's domain pool or the embedding helper's default model
- **AND** the handler's returned consolidation statistics or raised error MUST remain the scheduled task result recorded by the scheduler

#### Scenario: Consolidation without spawner (dry run)

- **WHEN** `run_consolidation` is called with `cc_spawner=None`
- **THEN** only episode grouping and counting MUST be performed
- **AND** no actual consolidation MUST occur

#### Scenario: Episode content wrapped in XML tags for prompt injection prevention

- **WHEN** episode content is formatted for the consolidation prompt
- **THEN** each episode's content MUST be wrapped in `<episode_content>` XML tags
- **AND** the SKILL.md MUST contain a security notice instructing the LLM to treat episode content as data only

#### Scenario: Throughput decision retains existing batching

- **WHEN** the consolidation throughput decision recorded in `docs/decisions/consolidation-throughput-2026-10-15.md` is applied
- **THEN** additional batching changes SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** existing bounded episode claims, deterministic grouping and one session per non-empty tenant/source group SHALL remain unchanged
- **AND** this decision SHALL NOT reinterpret the ordinary default100 or existing scheduled backfill500 as a new universal cap

#### Scenario: Throughput decision declines an additional per-run cap

- **WHEN** the recorded throughput decision is applied
- **THEN** a new consolidation-specific session, token or dollar cap SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** existing model-catalog and Spawner quota, failover and timeout authority SHALL remain unchanged
- **AND** descriptive observed session token counts SHALL NOT be represented as exact whole-pipeline invocation cost or a newly adopted limit

#### Scenario: Throughput decision declines backlog deadman automation

- **WHEN** the recorded throughput decision is applied
- **THEN** a new pending-backlog deadman SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** this decision SHALL authorize no alert-triggered runtime work, retry, lease clearing, drain, deletion, quarantine or automatic lifecycle change
- **AND** an empty eligible population SHALL preserve a NULL oldest-eligible age rather than treating it as measured zero

#### Scenario: Throughput decision preserves catalog-authoritative transport

- **WHEN** the recorded throughput decision is applied
- **THEN** a consolidation-specific forced cheap tier or transport SHALL be recorded as deliberately not needed for the evidenced current state
- **AND** scheduled consolidation SHALL continue through the live catalog-backed Spawner without model, runtime or session-timeout overrides
- **AND** absent authoritative billing or quality comparison SHALL NOT be replaced with guessed monetary savings or equivalence claims

#### Scenario: Reconsidering a declined throughput control

- **WHEN** a later proposal would adopt any of the four declined controls
- **THEN** fresh source-bound backlog and exact attributable token evidence, including explicit unmeasurable gaps, SHALL precede numerical thresholds
- **AND** the proposal SHALL define the threshold and action in a reviewed capability spec delta and create one separate implementation issue for each adopted control before implementation
- **AND** the recorded present declines SHALL NOT be construed as proof that future stalls, cost pressure or useful catalog-compatible changes are impossible
