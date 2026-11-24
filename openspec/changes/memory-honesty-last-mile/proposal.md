## Why

The memory console can currently confuse an unreachable memory pool with an
empty or missing record, and its inspect total can be inferred from a bounded
fetch rather than the selected result set. Episodes can also reach
`dead_letter` without a bounded, auditable owner recovery path, while fact
mutations give the owner no reliable landing feedback. These gaps make failure
look like calm and leave an otherwise durable lifecycle incomplete.

## What Changes

- Align consolidation with adopted RFC 0038 D2, D6 and D18: the deterministic
  schedule submits one declared obligation goal per tenant, source domain and
  taint partition. Model judgment belongs to that goal; source-scoped read and
  consolidation-apply capabilities retain the bounded claimant and lifecycle.
  This replaces direct consolidation spawning without changing RFC decisions.
- Define truthful memory detail semantics: a clean cross-pool miss is a 404;
  an unresolved miss with one or more named failed pools is a 503. List and
  inspect responses retain partial results but expose genuine failed pools in
  `meta.pools_failed`.
- Require exact, filtered backend totals for episode, fact, rule, and inspect
  paging. A page-local bounded union must never be represented as a global
  total, and a total from only reachable pools must be visibly qualified while
  sources are degraded.
- Define the episode consolidation lifecycle for recovery-eligible memory
  sources: retry-eligible `failed` rows respect backoff, `dead_letter` rows
  are never auto-claimed, and the owner can queue one eligible dead-letter
  episode for a future scheduled write-up through a dashboard-only requeue
  transition that neither admits a goal nor starts execution. Chronicler's
  private `chronicler_mem` store remains outside this recovery slice.
- Define the public episode dossier fields, typed paginated memory metadata,
  and the owner requeue API's authorization, eligible-source boundary,
  atomicity, lifecycle-event, error, and concurrency contracts.
- Require dashboard detail, register, and search surfaces to retain source
  failure context; require accessible queued/not-running requeue feedback and
  accessible success/failure feedback for fact Confirm and Retract mutations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: clarify the generic episode consolidation retry,
  dead-letter, and owner-requeue lifecycle on the adopted obligation-goal path
  without adding an MCP recovery tool.
- `dashboard-api`: define memory fan-out failure outcomes, exact pagination
  totals, public episode lifecycle fields, and the bounded requeue endpoint.
- `dashboard-domain-pages`: define truthful memory page/detail states, paging,
  degraded-source notices, and accessible mutation/requeue feedback.

## Impact

- Planned backend work is limited to the memory API fan-out/read paths and the
  memory module's source-scoped claimant, consolidation capabilities and
  lifecycle persistence within the existing goal-runtime domain conversion.
- Planned dashboard work is limited to the memory registers, unified inspect
  search, episode/fact/rule detail pages, their query/mutation hooks, and their
  focused tests.
- The later implementation must add focused API, lifecycle, frontend, and
  accessibility regressions. This change itself contains no application code,
  migration, direct SQL, live requeue, bulk replay, or MCP run-now surface.

## Amendment and delivery boundary

`bu-kqnum.14.8` re-scopes this active change before the reconciliation/archive
in `bu-kqnum.14.7`, as required by the generalist change's collision disposition.
It does not archive this change or claim that goal-runtime conversion has
shipped. Existing implementation checkmarks retain their historical meaning;
the remaining tasks require evidence against the amended contract.

The archived consolidation-throughput decision's five scenarios and four
explicit policy declines remain binding. This amendment changes the model
execution mechanism, not the recorded evidence, existing operational claim
sizes, retry constants, cadence, or decisions against additional controls.
