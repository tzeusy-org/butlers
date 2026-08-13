## Context

`GET /api/memory/stats` already fans out across independently owned memory
pools. Its established `retention_*` fields and `RetentionSourceObservation`
rows report the cleanup-lag population, while a separate failed-pool list
prevents a partial aggregate from becoming a fleet-wide retention all-clear.
That remains the compatibility baseline from merged PR #3509; it is not
renamed, removed, or reinterpreted here.

The graph-health read model needs to answer a distinct operator question:
whether every relevant memory pool contributed evidence to the observation.
The prerequisites are already present on current `main`: exact consolidation
artifact evidence (`bu-27dxl.4.1`, PR #3669) and content-free durable
episode-provenance/source-expired behavior (`bu-kqnum.13.4`, PR #3643). This
change consumes neither as a new write dependency and does not turn their
provenance links into a new metric.

The owner selected the existing consolidation-aware cleanup-lag population:
an episode is a lag numerator only when it is expired and non-pending, or is
pending beyond the configured cleanup grace window. The denominator is every
episode with `expires_at IS NOT NULL`. `REAPABLE_EXPIRED_EPISODE_SQL` remains
the shared predicate with the cleanup reader; an expired pending episode inside
the grace window is deliberately not degraded.

## Goals / Non-Goals

**Goals:**

- Add a typed, additive `meta.graph_health` read model to memory stats.
- Represent every completed or genuinely unavailable relevant pool explicitly.
- Distinguish complete, incomplete, and unknown coverage without zero-filling
  values or claiming health from partial data.
- Render that coverage state in the existing Memory Overture with no action
  beyond retrying the same read when a source is unavailable.
- Preserve existing stats, retention fields, request authorization, and
  side-effect-free request handling.

**Non-Goals:**

- No write, job, migration, retention drain/deletion, cleanup invocation,
  graph repair, backfill, provenance-link mutation, or relationship entity-fact
  behavior.
- No provenance-link coverage percentage or new artifact-population definition.
- No endpoint, authorization, cache, or broad dashboard-layout redesign.

## Decisions

### D1 — Add metadata rather than alter the established retention contract

`meta.graph_health` will be a new typed object. It does not replace or alias
`data.expired_retained_episodes`, `data.retention_eligible_episodes`,
`data.expired_retained_ratio`, `meta.retention_status`,
`meta.retention_sources`, or `meta.retention_pools_failed`.

This preserves existing consumer semantics and lets current clients ignore the
new object safely. Putting the observation in `meta` follows the existing
catalog-drift and degraded-source pattern: it describes evidence quality for a
read, not a new memory-tier count.

**Alternatives considered:**

- Rename `retention_*` to graph health: rejected because it breaks consumers
  and changes an already-merged contract.
- Treat the existing retention metadata as the new graph-health API without a
  typed object: rejected because callers cannot distinguish source coverage in
  one stable, per-pool shape.
- Add a provenance-link ratio: rejected because this slice has no authoritative
  discriminator for every artifact that must carry a link, and inventing one
  would broaden the evidence contract owned by the merged prerequisites.

### D2 — Use an explicit per-pool coverage object

`meta.graph_health` is a `GraphHealthCoverage` object:

```text
coverage: "complete" | "incomplete" | "unknown"
pools: GraphHealthPoolCoverage[]

GraphHealthPoolCoverage:
  source_butler: string
  source_schema: string | null
  coverage: "complete" | "unknown"
  reapable_expired_episodes: integer | null
  retention_eligible_episodes: integer | null
  reapable_expired_ratio: number | null
```

A completed relevant pool has `coverage="complete"`, non-null integer
numerator/denominator, and a ratio that is `null` only for a zero denominator.
A genuinely failed relevant pool remains in `pools` with
`coverage="unknown"` and all three metric values `null`; it never becomes a
synthetic zero. Pools that genuinely lack a memory schema remain absent, as in
the established fan-out contract.

Fleet `coverage` is `complete` only when at least one relevant pool completed
and none failed; `incomplete` when both completed and unknown pools exist; and
`unknown` when no relevant pool produced a completed observation. This makes
an empty candidate set fail closed instead of manufacturing a healthy result.

### D3 — Reuse the cleanup-lag reader and exact denominator

The existing retention fan-out query is the sole data source for completed
graph-health rows. Its numerator is the shared
`REAPABLE_EXPIRED_EPISODE_SQL` predicate and its denominator is
`expires_at IS NOT NULL`; no episode content or IDs are fetched. Graph-health
objects are assembled from that successful result and the existing degraded
tracker after the query returns.

This keeps the cleanup and observation populations byte-for-byte aligned,
prevents duplicate SQL/read paths, and preserves the no-write boundary.

### D4 — Present coverage, not an all-clear or remediation control

`MemoryOverture` will render a muted, read-only completion line when coverage
is complete. Incomplete or unknown coverage renders a named existing
`SourceDegradedNote`, with a retry only because retry repeats a read that may
recover an unavailable pool. It does not offer cleanup, repair, re-enable,
drain, or authorization controls. The copy calls out coverage rather than
claiming the graph itself is healthy; existing retention, ordinary pool, and
catalog-drift notes remain separate.

## Risks / Trade-offs

- **[Risk] A failed pool is accidentally represented as zero.** → Unknown rows
  have nullable metrics and coverage is never `complete` when a tracker names
  a failure.
- **[Risk] A grace-protected pending episode is reported as lag.** → Reuse the
  cleanup module's exported predicate instead of reimplementing expiry SQL.
- **[Risk] Empty results read as healthy.** → No completed relevant pool yields
  fleet coverage `unknown`, not a zero/healthy fallback.
- **[Risk] Two similar metadata contracts drift.** → Graph health is derived
  from the existing retention fan-out result, and tests pin that all legacy
  retention fields remain unchanged.
- **[Trade-off] This does not measure provenance-link completeness.** → That
  population is intentionally unmodelled in this bounded read-only slice; the
  merged provenance contracts continue to own it.

## Migration Plan

No persistence migration or rollout action is required. The backend emits an
additive metadata key, existing clients ignore it, and the updated frontend
consumes it opportunistically. Reverting the change removes only the new
metadata and presentation; it does not require data repair or cleanup.

## Open Questions

None. The owner-selected cleanup-lag numerator and denominator are fixed for
this change.
