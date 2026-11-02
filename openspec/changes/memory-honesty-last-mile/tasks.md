`bu-kqnum.14.8` amends the remaining work to adopted RFC 0038 goal-runtime
terms before `bu-kqnum.14.7` reconciliation/archive. Existing checkmarks record
their merged implementation slices, not goal-runtime completion. Goal delivery
remains with the generalist change's domain conversion (2.12) and switch (4.1);
its baseline rebuild (4.2) must retain this change's recovery and throughput
clauses. PR [#5074](https://github.com/tzeusy-org/butlers/pull/5074) landed that
specification amendment; it did not deliver the goal runtime. The reconciliation
below records the subsequently landed slices. Unchecked goal-contract work has
one existing implementation owner, `bu-vbdug9.2` (partitioned consolidation),
which consumes `bu-o9qax4`'s declaration/admission API. Generalist task 2.12 is
the domain-conversion locator and 4.1 is the switch locator, not Beads IDs.
`bu-kfnoah` separately owns goal-outcome episode capture; it does not replace the
consolidation owner. Archive and baseline promotion remain withheld until the
amended contract and switch have real implementation and execution evidence.

## 1. Episode consolidation lifecycle

- [ ] 1.1 Add failing module-memory regressions for pending claims, failed rows
  before and after their retry timestamp, expired leases, dead-letter exclusion,
  concurrent claimant safety, and exclusion of Chronicler's private
  `chronicler_mem` episodes from the new failed-retry claimant; cover one
  obligation admission per tenant/source/taint partition, empty-population
  no-admission, dry-run no-execution and independent group failures.
- [ ] 1.2 Update the source-scoped read capability's schema-qualified episode
  claimant to select only eligible pending/failed rows, preserve ordered
  `FOR UPDATE SKIP LOCKED` grouping, and
  never automatically select `dead_letter` or `consolidated` rows; apply the
  recovery-eligible source predicate so a distinct private `memory_schema` is
  not newly retried without changing its existing private-source pending path.
  The deterministic schedule submits declared obligation admissions rather than
  spawning; read context preserves authority/taint partition isolation.
- [ ] 1.3 Complete deterministic consolidation-apply lifecycle persistence for
  failed, terminal, and successful outcomes: sanitized errors/reasons, bounded
  retry timing, lease clearing, and
  truthful public-field reset behavior, including a requeue reset of the
  non-null `consolidation_attempts` integer to `0`. Validate artifact evidence
  before persistence and retain source tenant, authority/taint and held-rule
  guarantees; use the active memory pool/embedding lifecycle and catalog-backed
  goal-runner policy without job model/runtime/timeout overrides.
- [x] 1.4 Add focused lifecycle-event regressions proving terminal failure and
  owner requeue event payloads contain no raw runtime, prompt, credential, or
  lease data. Landed in PRs [#3707](https://github.com/tzeusy-org/butlers/pull/3707)
  and [#4846](https://github.com/tzeusy-org/butlers/pull/4846); the audit PR
  records the protected hosted lifecycle execution receipt.

## 2. Memory API truth and bounded requeue

- [x] 2.1 Extend episode response projections and frontend API types to carry
  only the approved lifecycle dossier fields; introduce typed
  `MemoryPaginationMeta` / `MemoryPaginatedResponse<T>` so paginated episode,
  fact, rule, and inspect responses carry `pools_failed?: string[]` without
  unsafe casts; do not expose worker lease internals. Landed in PRs
  [#4846](https://github.com/tzeusy-org/butlers/pull/4846) (dossier) and
  [#4829](https://github.com/tzeusy-org/butlers/pull/4829) (typed metadata).
- [x] 2.2 Add API regressions for episode/fact/rule detail resolution: found
  rows win over unrelated degraded pools, clean misses are 404, and unresolved
  named failures are source-named 503s. Landed in PR
  [#3346](https://github.com/tzeusy-org/butlers/pull/3346).
- [x] 2.3 Make episode, fact, rule, and inspect `meta.total` exact after their
  filters, independent of the bounded globally merged page; retain named typed
  `meta.pools_failed` for genuine source failures and test healthy/degraded
  counts, ordering, `has_more`, and the typed pagination metadata contract.
  Landed in PR [#4829](https://github.com/tzeusy-org/butlers/pull/4829).
- [x] 2.4 Implement `POST /api/memory/episodes/{id}/requeue` behind the
  dashboard owner guard, with schema-qualified target resolution and a
  parameterized conditional dead-letter-to-pending transaction that resets
  `consolidation_attempts` to `0` and writes one sanitized lifecycle event;
  constrain target resolution and mutation to recovery-eligible sources,
  excluding Chronicler's private `chronicler_mem` schema. Landed in PR
  [#4846](https://github.com/tzeusy-org/butlers/pull/4846).
- [x] 2.5 Add API/auth/race regressions for requeue success, malformed UUID,
  clean 404, named 503, the central boundary outcomes (401, 403 Origin/CSRF, 503) pinned on the requeue route, non-dead-letter 409, and two concurrent
  calls yielding exactly one queued outcome/event; assert a successful requeue
  persists and returns `consolidation_attempts=0`; prove an id present only in
  `chronicler_mem` remains outside requeue resolution and is not mutated.
  Landed in PR [#4846](https://github.com/tzeusy-org/butlers/pull/4846).
- [x] 2.6 Prove the requeue request never invokes a spawner, consolidation
  runner, scheduler run-now path, MCP tool, or bulk replay path; keep migrations
  and direct operator SQL out of this implementation.
  Goal-runtime reconciliation must also prove the same request admits no goal;
  this historical checkmark does not certify that future path. The bounded
  requeue slice landed in PR [#4846](https://github.com/tzeusy-org/butlers/pull/4846).

## 3. Truthful and accessible dashboard surfaces

- [x] 3.1 Update EpisodeDetailPage, FactDetailPage, and RuleDetailPage so only
  true 404s render not-found copy; render named 503 and other query failures as
  visible retryable errors, with focused regression coverage. Landed in PR
  [#4870](https://github.com/tzeusy-org/butlers/pull/4870).
- [x] 3.2 Add the episode lifecycle dossier and owner/dead-letter-only requeue
  control, including queued-not-running, conflict, and failure landing states;
  test keyboard operation, visible focus, disabled-in-flight behavior, and live
  announcements. Landed in PR
  [#4870](https://github.com/tzeusy-org/butlers/pull/4870).
- [x] 3.3 Render `SourceDegradedNote` for episode/fact/rule registers and
  inspect search whenever typed `MemoryPaginationMeta.pools_failed` is
  non-empty, including a zero-row partial response; add register and inspect
  regressions for named failed pools and a healthy absent/empty flag while
  retaining healthy true-empty behavior. Landed in PR
  [#4829](https://github.com/tzeusy-org/butlers/pull/4829).
- [x] 3.4 Route SearchResults through the shared query boundary and implement
  URL-backed inspect paging from exact API metadata, including qualified ranges
  while sources are degraded; test loading, error, clean empty, filter, and
  back/forward page behavior that preserves `q` and `kind` while changing
  `offset`. PR [#4829](https://github.com/tzeusy-org/butlers/pull/4829)
  landed typed URL paging and ranges; PR
  [#5077](https://github.com/tzeusy-org/butlers/pull/5077) landed the
  shared `QueryBoundary`, cached-empty and cached-row refetch-error/retry
  regressions, and actual back/forward navigation assertions.
- [x] 3.5 Add local accessible success/failure feedback to the Fact detail
  Confirm and Retract footer without changing request payloads or optimistic
  rollback/cache semantics; test both actions, rollback, retryability, and
  non-duplicated screen-reader announcements. Landed in PR
  [#4873](https://github.com/tzeusy-org/butlers/pull/4873).

## 4. Cross-contract verification and handoff

- [ ] 4.1 Run the focused module-memory, dashboard API, frontend, and
  accessibility regression suites named above; expand to the repository's
  required quality gates in proportion to the merged implementation risk.
- [ ] 4.2 Validate that public copy, API types, and tests jointly preserve the
  no-run-now/no-bulk/no-MCP boundary and do not widen into activity, entities,
  or re-embedding surfaces; requeue must admit no goal, and a later scheduled
  obligation must preserve retry, lease and partition rules.
- [ ] 4.3 Run `openspec validate memory-honesty-last-mile --strict`, review the
  final diff against the proposal/design/spec scenarios, and record the actual
  verification evidence against the amended goal-runtime contract before
  implementation reconciliation. Preserve all twelve baseline consolidation
  scenario names, the private-source scenario, the adopted goal-partition
  scenarios and all five throughput-decision scenarios/four policy declines;
  do not infer delivery from a spec-only amendment or archive unproven work.

### Reconciliation evidence and retained gaps

The audit PR contains the scenario-to-source, executing-test and landing-PR map,
including the named scope and source identity of each receipt. The existing
claim/retry/lease implementation for 1.1–1.3 landed in PR #3707; owner requeue
landed in #4846, truthful paging in #4829, detail/recovery UI in #4870,
and accessible Fact feedback in #4873. These receipts do not prove obligation
admission, partitioned read/apply capabilities, authority/taint isolation or the
switch. Tasks 1.1–1.3 remain open with `bu-vbdug9.2`. Reconciliation custody for
4.1–4.3 stays with `bu-kqnum.14.7`, blocked on that delivery and the generalist
4.1 switch disposition; those checks cannot be completed from legacy passes.
Strict validation checks specification consistency and does not establish these
runtime outcomes. No archive or baseline rebuild is performed by this audit.
