## Context

The dashboard memory API fans out across independently owned memory pools. A
missing row is meaningful only after every relevant source was queried
successfully or was known to lack the memory schema. A genuine source failure
leaves ownership unresolved, so returning a calm empty state or a 404 would
fabricate certainty. The API already has the `meta.pools_failed` vocabulary and
a detail-miss helper that can name unreachable pools, but downstream dashboard
surfaces do not consistently preserve either signal.

Memory episodes already persist `consolidation_status`,
`consolidation_attempts`, `last_consolidation_error`,
`next_consolidation_retry_at`, and `dead_letter_reason`. The intended lifecycle
is incomplete: a failed row is not selected for automatic retry, while a dead
letter has no narrow owner recovery path. The design therefore closes the
lifecycle using existing durable state rather than a new run-now mechanism.

This crosses the memory module, dashboard API, and React dashboard. It must
honour schema isolation: each pool is queried through its own memory relation,
and the dashboard may read and make the one bounded state transition without
turning recovery into cross-butler MCP execution.

The new recovery behavior is deliberately narrower than generic memory-module
plumbing. A recovery-eligible source is a dashboard-selected memory pool whose
memory relation is in its owning butler schema (no distinct private
`memory_schema` override). A pool with a distinct private override is excluded;
this explicitly excludes Chronicler's `(chronicler, chronicler_mem)` mapping.
The exclusion applies to the new failed-retry claimant and manual requeue
lookup/update only: it does not change Chronicler's existing private runtime
pool or generic read fan-out behavior, and an excluded source is not itself a
failed or unavailable source.

## Goals / Non-Goals

**Goals:**

- Make a clean absence distinguishable from an unresolved named source failure.
- Give every memory register and unified inspect search an exact count of the
  reachable, filtered result set and make any incomplete source set visible.
- Make automatic retry bounded and deterministic, while making dead letters
  terminal until an explicit owner dashboard action queues one episode again.
- Keep Chronicler's private `chronicler_mem` episodes outside this generic
  retry and requeue slice.
- Give the owner an auditable, race-safe requeue operation that never starts a
  consolidation session.
- Make user-facing recovery and fact-mutation outcomes keyboard- and
  screen-reader-accessible.

**Non-Goals:**

- No application code, migration, direct SQL operator procedure, live requeue,
  or bulk repair in this change.
- No collection/bulk requeue endpoint, automatic dead-letter replay, MCP
  recovery tool, CLI recovery command, or "run consolidation now" control.
- No exposure of `leased_by`, `leased_until`, raw runtime output, prompts, or
  other worker internals.
- No change to activity, entity, or re-embedding surfaces, nor to the existing
  fact Confirm/Retract request payloads or approval semantics.

## Decisions

### 1. Resolve absence before choosing a detail error

A memory detail lookup keeps its successful 200 response when a reachable pool
owns the requested row, even if another pool failed. If no reachable pool owns
the row and every omitted pool is a known non-memory schema, the endpoint
returns 404. If no row is resolved and one or more named pools genuinely fail
or are unavailable, it returns 503 and names those sources in a safe,
actionable detail; it does not claim the record is missing.

This applies equally to episode, fact, and rule details, and to the requeue
target lookup. It reuses the dashboard fan-out convention rather than
introducing a memory-specific error envelope. A generic 404-on-any-miss was
rejected because it turns an unknown into a false absence. Failing every list
request on a single pool error was rejected because partial data remains useful
when its limits are explicit.

### 2. Count the selected union independently from its page

For `GET /api/memory/episodes`, `/facts`, `/rules`, and `/inspect`, `meta.total`
is the exact count after the endpoint's filters across every successfully
queried selected memory pool. It is calculated independently from the bounded
rows collected to assemble the current globally ordered offset page; `has_more`
therefore follows the exact count rather than the fetched slice.

When `meta.pools_failed` is empty or absent, that count is the exact selected
cross-pool total and may be presented normally (for example, "Showing 1–50 of
312"). When it names one or more genuinely failed pools, the count remains
exact only for the responding sources. The response and UI must identify the
omitted sources and must not represent the number as a complete all-memory
total; a qualified range such as "Showing 1–50 of 312 available records" is
permitted. Known absent memory schemas are silently skipped and never appear in
`pools_failed`.

The frontend contract represents this page metadata as
`MemoryPaginationMeta`, an extension of `PaginationMeta` with typed
`pools_failed?: string[]`, and returns it through
`MemoryPaginatedResponse<T>`. An absent `pools_failed` is interpreted as an
empty list; registers and inspect search must not use an unsafe cast or
untyped metadata access to render a degraded-source note.

Using `len(merged)` after each pool has supplied at most `offset + limit` rows
was rejected because it turns a page implementation detail into an understated
global count.

### 3. Make the episode state machine explicit and closed

For recovery-eligible sources, the lifecycle uses existing durable fields and
has these externally observable rules:

| State | Automatic claimant | On success | On failure |
|---|---|---|---|
| `pending` | Claimable when not actively leased | `consolidated` | `failed` or `dead_letter` |
| `failed` | Claimable only after its retry timestamp and when not actively leased | `consolidated` | stays `failed` with a later backoff, or becomes `dead_letter` at the attempt limit |
| `dead_letter` | Never claimable automatically | n/a | n/a |
| `consolidated` | Never claimable automatically | n/a | n/a |

A retry claimant applies this new lifecycle only to recovery-eligible sources.
It MUST exclude any source with a distinct private `memory_schema`, including
Chronicler's `chronicler_mem`; no newly added failed retry may select or update
an excluded private episode. The requeue resolver uses the same source set, so
it cannot discover or transition an excluded private episode. This exclusion
does not turn the private source into `pools_failed` and does not change its
existing module-local behavior.

A failed consolidation increments `consolidation_attempts`, records a
sanitized `last_consolidation_error`, clears its lease, and calculates the next
retry time using the established bounded exponential backoff. The terminal
failure also copies a sanitized reason to `dead_letter_reason` and clears the
automatic retry time. A successful consolidation clears the lease and any
pending retry/error state that would falsely imply unresolved failure. Public
episode representations expose the attempts, sanitized last error,
dead-letter reason, and next retry timestamp; they never expose lease fields.

Leaving `failed` out of the claimant was rejected because it makes retry
metadata performative. Allowing `dead_letter` into that query was rejected
because it erases the operator boundary that makes a dead letter trustworthy.

### 4. Requeue is one owner-scoped, conditional state transition

`POST /api/memory/episodes/{episode_id}/requeue` is a dashboard API-only owner
operation. It requires the dashboard's owner authorization guard; a caller
without owner authority receives `403` with the stable `owner_required` code.
It is not registered as an MCP tool and no scheduler, request handler, or UI
callback may invoke `run_consolidation` as a consequence.

After recovery-eligible source resolution, one transaction conditionally
updates only a `dead_letter` row to `pending`. It resets the current
attempt-cycle failure fields by setting the non-null integer
`consolidation_attempts` to `0` and clearing
`last_consolidation_error`, `dead_letter_reason`,
`next_consolidation_retry_at`, and any lease fields. It writes exactly one
sanitized lifecycle event in the same transaction. The event records the
episode identifier, the `dead_letter` → `pending` transition, the
dashboard-owner actor, and that the work was queued, but carries no raw error,
prompt, runtime output, or lease detail.

The endpoint returns the queued episode and an explicit message that the next
scheduled write-up may process it; it does not promise a run, a time, or a
successful result. Its outcomes are deliberately narrow:

- `400` for a malformed UUID;
- `404` for a clean absence after all recovery-eligible resolvable sources were
  checked;
- `503` for an unresolved target because no usable recovery-eligible memory
  source exists or a named eligible-source failure leaves ownership unknown;
- `409` when a known episode is not currently `dead_letter`;
- `200` only for the one queued transition.

The conditional update, not a read-then-write check, arbitrates concurrent
calls. Two requests for the same dead letter produce exactly one 200 and one
409; only the successful request creates the lifecycle event. A generic retry
endpoint, optimistic duplicate success, and a raw-SQL runbook were rejected
because each weakens the owner boundary or makes lifecycle evidence ambiguous.

### 5. The dashboard must preserve failure, recovery, and mutation outcomes

Episode, fact, and rule detail pages keep their existing true-not-found
presentation exclusively for a 404. A named 503 or any other query failure
renders an error state with source context and retry affordance, never a
not-found claim. Episode detail displays the approved lifecycle dossier fields
and shows an owner-only, dead-letter-only semantic requeue button. Its pending,
success, conflict, and failure states have clear text; success states that it
was queued for a later schedule and did not start a write-up.

Episode/fact/rule registers and unified inspect search render a named
`SourceDegradedNote` whenever `meta.pools_failed` is non-empty, including when
the returned page has zero rows. Search retains URL-backed `q` and `kind` while
page navigation changes `offset`, uses the exact backend total for
range/pagination, and routes loading and errors through the shared query
boundary so "Nothing in the books" only appears for a completed, healthy,
empty response.

Fact Confirm and Retract retain their optimistic update and rollback behavior.
Their commit footer also reports a concise local success or failure associated
with the initiating control. Dynamic messages use an appropriate named status
or alert region, do not double-announce, preserve keyboard access and visible
focus, and leave a failed action retryable after rollback. A global toast-only
solution was rejected because it loses the initiating action's context.

## Risks / Trade-offs

- **Exact counts add per-pool count work** → keep the existing bounded page
  fetch and perform only the filtered counts required for the selected
  endpoints; cover merged ordering and totals with focused API regressions.
- **A partial-source count can be misread as global** → always pair it with
  `pools_failed` and a named degraded-source note; do not show all-clear or
  unqualified global-total copy.
- **A requeue race can duplicate lifecycle history** → use one conditional
  update and write its event in the same transaction; test concurrent calls.
- **A private source can be widened into generic recovery** → define and test
  the recovery-eligible source predicate so `chronicler_mem` is neither
  retried nor requeued by this change.
- **Failure strings can leak implementation or sensitive content** → persist
  and render only sanitized summaries, and omit worker/lease/runtime details.
- **A recovery button can imply immediate execution** → copy and API response
  state that it queues for a future scheduled write-up, with no run-now path.

## Migration Plan

This specification proposes no data migration or live repair. The later
implementation should use the existing episode lifecycle fields and event
stream, deploy with focused regressions, and leave existing dead-letter rows
untouched until the owner explicitly queues an individual row. Rollback removes
the dashboard/API affordance and restores previous claimant behavior; it must
not bulk-transition stored rows or erase lifecycle events.

## Open Questions

None. The scoped decisions intentionally leave retry constants, batch size,
and scheduler cadence as existing operational configuration rather than public
API promises.
