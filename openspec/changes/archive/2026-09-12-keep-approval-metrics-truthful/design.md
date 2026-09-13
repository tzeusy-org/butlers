## Context

The metrics route aggregates two independently discoverable source families:
`pending_actions` supplies pending and decision metrics, while `approval_rules`
supplies the active-rule count. Today either family can fail after discovery,
but the route catches the exception and leaves the affected counters at their
default zeros. That loses the difference between a configured empty pool and a
failed read. Several readers then use `?? 0` or an empty section as an
all-clear.

The existing degraded-envelope convention already carries named sources in an
`ApiMeta` bag. The rule-promotion endpoints also already use that convention
for their independent subqueries. This change extends those established read
patterns only; its owner-approved boundary excludes every approval write path.

## Goals / Non-Goals

**Goals:**

- Preserve numeric contributions from healthy pools while making the affected
  metric family unavailable to consumers when any of its pools fails.
- Keep no configured pool as a genuine zero, distinct from an unreadable pool.
- Give each reader a typed, named, retryable unavailable state without
  discarding useful cached or independently successful data.
- Keep success-path presentation and supported navigation unchanged.

**Non-Goals:**

- Changing approval authorization, confirmation, dispatch, lifecycle,
  retention, mutation endpoints, database schema, or secret handling.
- Retrying a failed query automatically, inventing a source, or treating stale
  data as fresh.
- Extending this availability contract to unrelated sidebar badges or other
  consumers outside the dispatched vertical slice.

## Decisions

### Partition metrics availability by source family

`get_metrics` will retain two `DegradedSources` trackers: one for
`pending_actions` discovery/query failures and one for `approval_rules`. It
will return the successfully aggregated numeric values, plus
`meta.pending_actions_sources_degraded` and
`meta.approval_rules_sources_degraded` when the respective family is partial.
`meta.sources_degraded` remains the de-duplicated union for generic envelope
consumers.

An absent table/pool is not a failure: an empty degraded list means a real zero
for that family. A failed catalog probe is a degraded source just as a failed
query is. The response does not change numeric fields to nullable because the
partial values remain useful to diagnostics; typed reader helpers, not
zero-coercion, decide whether a count can make an all-clear claim.

Using only one union list was rejected because a rule-pool failure must not
hide a healthy pending-actions count, and an action-pool failure must not make
the active-rule result look unavailable. Encoding table names into source names
was rejected because it would overload the established source identity
vocabulary and make readers parse display strings.

### Type availability at the frontend boundary

Frontend API types will define the metrics response metadata and helpers in
`use-approvals` for pending-actions and approval-rules availability. This gives
all in-scope readers one interpretation of the API contract and prevents each
page from guessing based on whether a numeric field is zero.

`DashboardPage` passes only complete pending metrics into its aggregate model.
The KPI becomes an unavailable non-door for a partial pending aggregate, while
the overview model emits a named unavailable signal instead of a zero-derived
attention or Now result. `Sidebar` carries the same typed availability state
into an amber, accessible marker on its existing `/approvals` link rather than
casting the partial aggregate to zero. Independently successful individual
approval rows remain usable.

### Make query-level reader failures explicit without losing cached evidence

The Approvals suggestions and rule-promotion sections will inspect query error
state as well as response metadata. A failed initial request renders a named
unavailable note and a read retry. A failed refresh with cached data retains the
cards/tile alongside that note. Existing rule-promotion subquery degradation
continues to replace only the affected statistic block, so healthy blocks stay
visible.

The Settings approval panel uses the same metrics metadata and replaces a
partial pending number with a named degraded note plus a local query retry.
Retries only call the existing GET query refetch; they do not invoke a mutation
or disclose additional data.

## Risks / Trade-offs

- [Partial values could still be copied into a new reader as zero] → centralize
  category availability helpers and cover every dispatched reader with behavior
  tests.
- [A degraded note could hide valid cached evidence] → render the note alongside
  cached cards/tile, not instead of them.
- [A source name may be hard to interpret without its family] → each note names
  the affected family and the returned source names together.
- [The generic union could overstate which count is unavailable] → consumers
  that render a count use only the family-specific metadata.

## Migration Plan

1. Ship the typed API metadata and all readers in one change, protected by
   focused behavior tests.
2. No persistence, deployment, or data migration is required; old clients
   continue to receive their numeric fields and ignore the additive metadata.
3. Rollback is a normal application revert. It changes no approval records or
   authorization state.

## Open Questions

None.
