## 1. Durable condition ledger and deterministic reconciliation (bu-27dxl.6.2)

- [ ] 1.1 Add the guarded core migration from the current sole core-chain
  head for append-per-episode `public.infra_conditions`, including canonical
  source/fingerprint identity, `open|aging|resolved` state, first/last
  evidence, escalation due state, resolution metadata, and a partial
  uniqueness guarantee for one active episode per `(source, fingerprint)`.
- [ ] 1.2 Implement a small deterministic lifecycle service that accepts
  producer observations plus explicit snapshot completeness, creates/updates
  episodes, resolves only from complete successful snapshots, emits
  opened/confirmed/due/resolved/reopened/no-change transitions, and performs
  no audit, notification, LLM, session, worktree, or healing-attempt side
  effect.
- [ ] 1.3 Implement versioned sorted identity-payload construction and
  concurrency-safe due-level claiming for L0, producer-owned L1, L2 after one
  day, L3 after three additional days, and seven-day L3 recurrence.
- [ ] 1.4 Add focused lifecycle, migration, recovery, recurrence, incomplete
  snapshot, and concurrent-reconciler tests on fresh/core-only and existing
  multi-schema database shapes.

## 2. Deployment and calendar producer reconciliation (bu-27dxl.6.3)

- [ ] 2.1 Replace deployment drift's composition-wide audit debounce with
  complete-snapshot reconciliation for stable per-`(schema, chain)`
  `deployment_drift` conditions; retain expected/current revision values and
  diagnostic prose only as evidence.
- [ ] 2.2 Route deployment L1 through the existing terminal human-action
  case shape once per episode and record L2/L3 re-escalations as distinct
  audit evidence without new healing attempts; preserve the direct audit
  result fields delivered by bu-27dxl.3.2 / PR #3516.
- [ ] 2.3 Migrate calendar sync deadman to the same complete-snapshot
  lifecycle without editing external deadman, fleet-halt attention, model
  breaker attention, decision review, or unrelated audit-log paths.
- [ ] 2.4 Add focused deployment/calendar tests for complete recovery,
  failed/degraded/partial reports, reopen, recurring L3 action, direct-audit
  attribution, and concurrent due transitions.

## 3. InfraState reconciliation and pre-claim QA suppression (bu-27dxl.6.4)

- [ ] 3.1 Collect each InfraState observation set before reconciliation and
  submit `source = infra_state` condition evidence only with an explicit
  complete snapshot; query or health failure MUST leave active conditions
  unresolved and existing paused, archived, and fresh-registration exclusions
  intact.
- [ ] 3.2 After normal QA eligibility and before `create_or_join_attempt`,
  match `infra_state` findings by explicit canonical source plus fingerprint;
  for an active condition, persist `infra_condition_open` with null attempt
  linkage and explicit finding suppression reason, then return without an
  attempt, LLM, session, or worktree.
- [ ] 3.3 Add focused source, dispatch, triage, and persistence tests for
  complete/failed/partial/recovery/reopen snapshots, active-condition
  suppression ordering, repeated decision records, zero attempt side effects,
  unchanged QA source-type vocabulary, and the existing unconfigured external
  deadman as a legitimate absence with no finding.

## 4. Dashboard lifespan loop supervision (bu-27dxl.6.5)

- [ ] 4.1 Add a narrowly scoped supervisor for the nine named dashboard
  lifespan loops: secrets lifecycle, model verification, fleet-events bridge,
  settings-console delta, secrets staleness, migration drift, calendar sync
  deadman, conditional external deadman, and restore drill.
- [ ] 4.2 Ensure unexpected return and exception log the named loop and restart
  it with bounded backoff without duplicate concurrent tasks; keep existing
  loop business logic and interval configuration unchanged.
- [ ] 4.3 Mark shutdown before cancelling and awaiting every supervised task,
  including calendar deadman, so shutdown cancellation never restarts a loop.
- [ ] 4.4 Add deterministic supervisor/lifespan tests for all-loop inventory,
  return, exception, bounded restart, shutdown cancellation, and conditional
  external-deadman registration.

## 5. Heartbeat-derived connector liveness readers (bu-27dxl.6.6)

- [ ] 5.1 Inventory every active connector read path that presents liveness;
  classify it as already heartbeat-derived, repaired, or intentionally
  non-liveness, without changing connector writers, heartbeat payloads,
  startup state, or registration.
- [ ] 5.2 Make each violating reader derive `online|stale|offline` through
  `derive_liveness(last_heartbeat_at)` while retaining stored
  `healthy|degraded|error` state as independent operational health and
  preserving paused/archived exclusions.
- [ ] 5.3 Add focused API, connector-model, and InfraState tests for recent
  healthy/error, stale healthy, missing heartbeat, future-dated heartbeat,
  paused, and archived cases.

## 6. Integration boundaries and verification

- [ ] 6.1 Fetch and re-read the latest core migration frontier immediately
  before the ledger migration; stop rather than attach a migration to a stale
  or multi-head chain. Resolve a real migration conflict on the branch when one
  exists, but do not rebase a clean PR merely to refresh it; the merge queue
  validates the current combined tree before landing.
- [ ] 6.2 Run focused lifecycle, producer, QA, supervisor, and connector
  regression tests before the repository quality gates; run lint, format, and
  the right-sized final test gate after targeted failures are resolved.
- [ ] 6.3 Run `openspec validate define-infrastructure-reliability-lifecycle
  --strict`, inspect the full diff for the specified capability scope, and
  prove no implementation slice adds generic producer heartbeats,
  context-producer/scheduler/default-schedule work, connector writer/startup
  changes, core-daemon/delegation wake-loop changes, direct-audit-result work,
  external-monitor provisioning, or a dashboard condition page.
