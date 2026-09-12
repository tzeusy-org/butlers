## 1. Approval and dependency gate

- [ ] 1.1 Obtain exact owner approval for the twelve product choices in `proposal.md`; record the
  approved artifact digest and do not infer implementation, migration, provider, deployment,
  archive, or merge authority from draft review.
- [ ] 1.2 Land or explicitly rederive against the durable message-scoped Stop and owner-facing turn
  projection from `durable-dashboard-terminal-action-recovery`; re-read the canonical requirements
  after every archive that changes them.
- [ ] 1.3 Serialize future dashboard conversation/API/schema implementation behind foreign PR #3960
  and independently reconcile the landed files. Treat draft PRs #4056 and #4059 as unapproved and
  independent; do not import their product choices.

## 2. Durable fast runtime and Stop

- [ ] 2.1 Add a cumulative core migration for the `fast_answer` durable session phase, monotonic
  `answer|non_answer` intent lane, boot-scoped owner instance, lease generation, heartbeat/expiry,
  immutable expired-lease reconciliation anchor `L`, immutable `D = L + 15 minutes`, and
  deterministic answer-reply claim/receipt state required by this contract. Preserve all existing
  turn phases, target kinds, and legacy/null behavior; registration age is not an execution deadline.
- [ ] 2.2 Register one Switchboard-owned fast-answer session before catalog/provider work, claim the
  existing pre-invoke fence once, heartbeat its 60-second fenced lease at least every 20 seconds,
  expose the live coroutine through Switchboard's registered `cancel_session` MCP handler, and
  release/complete it on every settled outcome.
- [ ] 2.3 Implement Stop fences before classification, each read, phrasing, and reply persistence;
  reconcile crash/transport uncertainty without automatic replay and make repeat/concurrent Stop
  idempotent.
- [ ] 2.4 Add the supervised Switchboard fast-runtime reconciler at startup and at most 60-second
  cadence. Inspect expired leases and durable receipts, conditionally claim a fencing generation,
  atomically capture the predecessor's exact lease expiry as stable `L`, preserve proven
  completion/cancellation/failure, and otherwise transition once at the first available sweep at or
  after `D` with reason `fast_answer_runtime_outcome_unknown`. Never replay work, infer death/Stop
  from lease expiry, move `L`/`D` on restart, or impose an execution timeout. Under continuously
  available supervision/storage, prove classification falls in `[D, D + 60 seconds]`; after an
  outage, settle receipt-first within the next 60-second sweep without extending the original clock.

## 3. Admission, catalog, and target-owned reads

- [ ] 3.1 Split structured dashboard classification into a side-effect-free admission result and a
  separate execution phase. Preserve the existing Spawner paths for domain questions, statements,
  actions, bugs, ambiguity, invalid schema, unsupported runtime, and pre-effect unavailability.
- [ ] 3.2 Add typed `matched|no_match|unavailable` catalog results with at most three
  held-sensitivity-filtered provenance records and the deterministic selected-owner rule. Poison the
  whole result to `unavailable` for any malformed envelope or candidate, without record filtering;
  keep RRF scores labeled as ranking evidence and prove authoritative no-match makes zero
  `invoke_structured` ownership calls.
- [ ] 3.3 Verify the selected-owner rule against a fixed labeled seeded catalog corpus; record corpus
  digest, rule version, result limit, coverage, wrong-owner count, per-owner breakdown, and confusion
  matrix, and refuse enablement on any wrong-owner selected hit.
- [ ] 3.4 Add the exact checked-in `FAST_ANSWER_CONCIERGE_TOOLS_V1` names from the spec and intersect
  them with Concierge's enabled `dashboard_read` module plus live target registration. Use the
  accepted Concierge/module/RFC contracts as the read-only authority, never `ToolMeta` or
  presentation metadata; reject plans over three calls and missing/contradictory authority, keep
  extra ungranted live tools ineligible, and reject direct-handler and cross-schema paths before any
  read.

## 4. Reply, observation, and model attribution

- [ ] 4.1 Persist exactly one sourced answer through the registered conversation-reply contract
  under a deterministic message-derived idempotency identity; reconcile an uncertain write from its
  durable receipt without replay.
- [ ] 4.2 Persist the intent lane before answer/non-answer execution and select 45- or 300-second SSE
  observation from the original observation start. Name the lane and awaited in-thread reply in
  timeout copy; leave the conversation/runtime unchanged and surface late replies normally.
- [ ] 4.3 Resolve both phase candidates before reads as exact `cheap`, catalog-backed API entries
  with tier fallthrough and static fallback disabled. Retain catalog execution timeouts, prohibit
  provider failover, and record distinct
  `dashboard_fast_answer_classification` and `dashboard_fast_answer_phrasing` attribution under the
  shared runtime/request identity without content-bearing telemetry.

## 5. Verification and evidence

- [ ] 5.1 Extend `roster/switchboard/tests/test_structured_classify.py` and
  `tests/modules/test_module_pipeline.py` for the full eligibility/fallback matrix, side-effect-free
  admission, exact-cheap/no-fallthrough/no-static-fallback resolution, one normal admission call,
  one same-candidate schema retry, no provider failover, exact successful-path provider/tool bounds,
  and zero CLI spawns.
- [ ] 5.2 Extend `tests/core/test_dashboard_turns.py`, `tests/core/test_core_spawner.py`, and
  `tests/api/test_dashboard_turn_cancellation.py` for registration-before-invoke, Stop at every
  boundary, concurrent/repeat Stop, owner-instance/generation/lease heartbeats, release ordering,
  late completion fencing, and no replay. Drive the real startup/periodic reconciler with an injected
  database clock through healthy renewal beyond registration minute 15; crash just before minute 15;
  expiry immediately before and after a sweep; repeated sweeps/restarts; both heartbeat/claim race
  winners; durable-store outage/recovery; predecessor partition; and reply/cancellation/failure
  receipt precedence. Assert immutable `L`, `D = L + 15 minutes`, available-store classification in
  `[D, D + 60 seconds]`, and zero replay; a direct transition-helper test is insufficient.
- [ ] 5.3 Extend `tests/core/test_delegation_ledger.py`,
  `roster/concierge/tests/test_dashboard_read.py`, and module integration coverage for typed catalog
  outcomes, `[malformed]`, `[valid, malformed]`, and `[malformed, valid]` whole-result poisoning,
  top-three provenance, held filtering, selected-owner rules, the exact V1 allowlist intersected with
  live registration/module state, missing/extra/contradictory authority rejection, registered
  target-owned MCP reads, and source attribution.
- [ ] 5.4 Add a new real-Postgres migration/replay and transaction suite beside
  `tests/config/test_dashboard_turn_cancellation_migration.py`; cover late-schema replay, legacy/null
  lane compatibility, runtime instance/generation/lease/heartbeat constraints, conditional
  heartbeat-versus-takeover winners, immutable expired-lease anchor/deadline across repeated claims
  and restarts, receipt precedence, reciprocal Stop/invoke/reply fences, idempotent reply receipt,
  operational crash/storage recovery, and non-narrowing downgrade behavior.
- [ ] 5.5 Extend `tests/api/test_conversations.py` for answer 45 seconds,
  non-answer/legacy/null 300 seconds, original-start timing, lane-specific timeout text, open thread,
  unchanged runtime, and visible late reply.
- [ ] 5.6 Retain the slow hermetic benchmark of 20 representative seeded system-plane questions
  using a declared fixed-latency stub adapter. Record stub latency, sample size, p50, p95, exact
  provider/tool/CLI call counts, require exactly 40 provider calls for the valid-schema corpus and
  tool calls equal to the admitted-plan sum, and require p95 `<3s`; keep it marked slow and eligible
  to skip in normal CI, record the result in the implementation PR body, and label it hermetic and
  never as live proof.
- [ ] 5.7 If separately authorized, record optional live evidence with sample size, date,
  runtime/model/catalog/timeout tuple, p50, p95, and failures in a separate evidence section. Its
  absence is not a gate.
- [ ] 5.8 Run targeted owning suites, the real-Postgres migration lane, `make test-plan
  BASE=origin/main`, `make check-spec-overwrites`, `make check-countable-tasks`, `make check-guards`,
  and terminal hosted CI on the exact implementation head. Report exact commands, SHA, warnings,
  pre-existing global debt, and `Tests: +a ~b -c` without upgrading partial evidence into a broad
  claim.
