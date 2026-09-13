## Context

`GET /api/timeline` already uses the versioned Timeline read model and obtains
the names of failed session fan-out pools. The router collapses that evidence
to `meta.degraded_sources = ["sessions"]`, so the reader cannot distinguish a
complete fleet from the reachable subset. On the client, the Timeline page
maps failed butler facets and saved-view reads to empty lists, while the ledger
logs and discards a failed `Load older` request. The pinned Session strip
reduces detail-query loading, failure, and a successful null error field to
the same "no error detail" text.

The dashboard is a read boundary over direct database queries. This change is
limited to that presentation boundary: it preserves successful data and tells
the operator exactly which evidence is unavailable. It does not alter the
fan-out implementation, endpoint inventory, session list/detail/drawer
surfaces, degraded-envelope registry, or any persisted state.

## Goals / Non-Goals

**Goals:**

- Carry failed session-pool names as additive Timeline metadata alongside the
  existing generic degraded-source list.
- Keep successful Timeline rows, built-in facets, and usable saved-view data
  visible while naming unavailable subreads and providing retry controls.
- Treat an older-page failure as a retryable request at the same cursor,
  retaining the committed historical snapshot.
- Make pinned error excerpts visibly loading, unavailable with a row-local
  retry, or loaded with an explicit known value including `null`.

**Non-Goals:**

- Any `bu-tpudw` Sessions list, aggregate, detail, or drawer behavior.
- Changes to `DatabaseManager.fan_out`, global endpoint routing, read-model
  versions, degraded-envelope semantics/registry, migrations, or stored data.
- A Timeline visual redesign, new transport, new dependency, or new retry
  policy beyond reissuing the reader's existing request.

## Decisions

### 1. Keep generic and named degradation separate

`TimelineMeta.degraded_sources` remains the existing source-level contract.
The router adds `degraded_butlers: string[]` only from the failed session
fan-out names it already receives. A session source failure therefore exposes
both the generic affected source and the named unavailable pools; notification
failure remains generic because it is a single Switchboard read, not a butler
fan-out. This is additive metadata, not a change to the shared
degraded-envelope convention.

Alternative: replace `degraded_sources` with names. Rejected: it loses the
existing source-level API and incorrectly implies that every source has a
per-butler topology.

### 2. Preserve the ledger snapshot and cursor before an older-page fetch

The hook commits the current events and its cursor before initiating `Load
older`. On failure it retains both, stores a local older-page error, and
exposes the same load action as a retry. The retry computes the request from
the retained cursor, so it cannot skip a historical boundary or promote a
failed request into a false end-of-history state.

Alternative: leave the page pinned and only log a failure. Rejected: a head
refresh can then reorder the view while the operator tries to retry, and the
button can disappear because no committed cursor exists.

### 3. Keep unavailable reader controls local and semantic

The Timeline page reads the query states for butler facets and custom saved
views. An error names that exact unavailable reader, retains all healthy
controls/data, and uses a native retry button. Custom view data that was
already loaded remains usable; an error never becomes the claim that there
are no custom views. The ledger presents a separate older-page failure notice
next to its retry control.

Alternative: use the page-wide Timeline error state for every auxiliary
reader. Rejected: it would hide useful Timeline evidence when only a filter or
saved-view subread failed.

### 4. Model pinned excerpts as a discriminated query state

`useSessionErrorExcerpts` returns one state per requested session: loading,
error with the query's own `refetch`, or loaded with the exact `error` value.
The strip renders the retry control outside the clickable row so native
interactive controls are never nested. A loaded `null` displays "no error
detail" only after a successful response.

Alternative: infer state from `Map.get(id)`. Rejected: both an absent map
entry and a successfully loaded `null` collapse into the same false claim.

## Risks / Trade-offs

- [A retry races with a filter change] → the hook resets its committed cursor
  and local failure state whenever the stable filter key changes, so an old
  cursor is never reused for a new filter.
- [A stale detail result exists when a refresh fails] → error takes precedence
  in the excerpt state so the operator knows the refresh is currently
  unavailable rather than mistaking cached text for a fresh detail read.
- [A client/server rolling deploy omits new metadata] → frontend types default
  the additive list to empty and keep the existing generic degraded banner.
- [A retry button is hard to reach] → each control is a named native button,
  remains visible with retained evidence, and is covered by page/ledger/pinned
  rendering tests.

## Migration Plan

1. Deploy the additive backend metadata and typed client default together.
2. Deploy page, ledger, and pinned-excerpt readers; old servers still render
   the generic source state and empty named list safely.
3. Roll back by reverting the consumer and metadata field; no migration,
   database state, or cursor format changes require recovery.

## Open Questions

None. The owner approved a focused reader contract, and the existing Timeline
read-model return value provides the named failed-pool evidence without a
topology change.
