## Why

Model catalog priority currently selects one winning model per tier. When that selected
model is temporarily unavailable, misconfigured, over quota, or its CLI fails before
doing useful work, the whole butler session fails even when a lower-priority model in
the same tier could safely handle the request. Operators can already express this
preference ordering in the catalog, but the runtime does not use it as an availability
failover chain.

The change is needed now because the catalog has grown into the authoritative runtime
control plane. Failover must be deliberately scoped: retrying after user/event work has
started can duplicate side effects, so automatic failover must apply only to systemic
runtime failures and pre-invocation blocks.

## What Changes

- Add same-tier model failover for catalog-resolved spawner sessions.
- Add an exact-tier "next eligible candidate" resolver that applies butler overrides,
  model state filtering, priority ordering, and attempted-candidate exclusion.
- Treat quota exhaustion as a pre-invocation failover condition when another eligible
  same-tier model exists; otherwise preserve the existing hard-block behavior.
- Classify runtime failures into failover-eligible systemic errors versus normal
  session failures.
- Retry automatically only when the failed attempt has no captured tool calls and no
  other side-effect evidence.
- Record attempt provenance so operators can see primary failure, fallback success or
  exhaustion, and the final model used for the logical session.
- Preserve existing initial tier fallthrough behavior when no candidate exists in the
  requested tier; after a tier produces a candidate, failover stays inside that resolved
  tier only.

## Capabilities

### New Capabilities

- `model-failover`: same-tier availability failover semantics for model catalog
  candidates.

### Modified Capabilities

- `model-catalog`: adds candidate-list / next-candidate behavior and clarifies that
  priority order can be used as a same-tier failover chain.
- `core-spawner`: adds failure classification, side-effect gating, retry orchestration,
  and attempt provenance for model failover.
- `catalog-token-limits`: changes quota exhaustion from unconditional hard block to
  same-tier failover when another eligible candidate exists before invocation.

## Impact

- Affected code: `src/butlers/core/model_routing.py`,
  `src/butlers/core/spawner.py`, adapter failure surfaces under
  `src/butlers/core/runtimes/`, session/process-log helpers, and model settings
  failure-tail APIs.
- Affected tests: model routing integration tests, spawner failover unit tests,
  quota enforcement tests, runtime adapter error classification tests, and API tests
  for failure-tail attempt provenance.
- Affected database/schema: likely adds attempt/provenance fields either to
  `public.dispatch_failures`, `session_process_logs`, or a new lightweight attempt
  table. Any new public write surface must update the RFC 0006 public-schema grant
  matrix and matching core migration.
- No dashboard UX rewrite is required for v1, but existing model failures/history
  surfaces should expose enough provenance to diagnose fallback behavior.
