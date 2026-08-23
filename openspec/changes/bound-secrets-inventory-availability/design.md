## Context

The existing inventory path sequentially awaits every butler pool and runs a
windowed audit lookup for each populated source. Its `ROW_NUMBER()` query must
rank every matching audit row before retaining the newest three. The live audit
table is large enough for this read path to exceed the frontend's 15-second
deadline. See `proposal.md` for the incident evidence.

## Goals / Non-Goals

**Goals:**

- Bound the inventory response below the browser deadline without weakening the
  inventory's content-blind response contract.
- Preserve completed-source data and accurately name omitted sources.
- Make audit history retrieval proportional to requested credentials and the
  existing per-target limit.
- Prevent a partial result from presenting a zero-count all-clear.

**Non-Goals:**

- Changing secret storage, role grants, audit retention, schema/indexes, or
  credential values.
- Increasing the browser timeout or caching secret inventory responses.
- Turning the credential inventory into connector-health monitoring.

## Decisions

### Use index-backed lateral top-N audit reads

The audit helper will deduplicate requested targets, then use one `LATERAL`
subquery per target with `ORDER BY ts DESC LIMIT $2`. This uses the existing
`ix_audit_log_target_ts (target, ts DESC)` index and reads at most the requested
history per credential. A window function was rejected because it ranks all
matching history before applying the limit.

### Bound concurrency and preserve configured order

The route will schedule one source task per butler plus one shared-source
bundle. A semaphore permits six tasks at once, each source has a three-second
budget, and the enclosing read phase has a ten-second budget. Task results are
collected first and appended in configured source order; concurrent completion
order must not reorder the response or `meta.sources_degraded`.

### Omit an incomplete source atomically

Credential and audit evidence form one truth unit for this inventory. If either
cannot complete within the source budget, the entire source is omitted and named
degraded. Existing absent-probe semantics remain best-effort. Returning rows
with `audit: []` was rejected because it would make unavailable history
indistinguishable from genuinely empty history. The shared bundle uses the
stable source name `shared-public`.

### Retain existing wire metadata and make the headline truthful

`meta.sources_degraded` already reaches `InventoryResponse.sourcesDegraded` and
the page already renders a named banner. No API type is added. The passport
headline will use that existing signal to replace the all-clear phrase with
`Credential inventory incomplete.` when any source was omitted.

## Risks / Trade-offs

- [A temporarily slow but healthy source is omitted] → The three-second source
  budget and named degraded banner make the limitation explicit; a refresh can
  recover it without a credential mutation.
- [Concurrent reads increase database pressure] → The semaphore caps work at
  six sources, below the thirteen possible source tasks.
- [A future archive overwrites an active content-blind delta] → This change
  adds a unique requirement and never modifies the active `Secrets Inventory
  and Per-Credential Read Endpoints` requirement block; archive-time same-name
  scans remain mandatory.
- [An optimization leaks evidence] → Existing response-byte sentinel tests run
  unchanged and the implementation keeps explicit projection functions.

## Migration Plan

No database migration is required. Deliver through a reviewed PR, verify the
focused API/frontend/performance tests, and only then perform an owner-authorized
dev Compose verification using a body-discarding inventory request. Rollback is
the normal PR rollback because no persistent state or API field is added.
