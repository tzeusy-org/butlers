## Context

The archived `approval-stalled-radar` change established the read-only backend
predicate and a whole-population `meta.stalled_count`. The Trust Console still
queries only the waiting lane, so a stalled count has no truthful drill-down.
The existing dashboard Retry endpoint already enforces the same
`approved`/null-result predicate and returns an honest dispatch outcome.

## Goals / Non-Goals

**Goals:**

- Treat the URL as the source of truth for the Trust Console lane: the default
  lane is waiting and `?state=stalled` loads the existing stalled flat query.
- Make the stalled radar a real link only to that filtered lane, preserving the
  query state while selecting a dossier.
- Keep retry bounded to the established unexecuted predicate and refresh all
  affected approval reads only after the retry request completes successfully.
- Preserve native keyboard navigation, visible focus, and assistive semantics.

**Non-Goals:**

- New retry policies, server endpoints, dispatch fallbacks, or execution
  result interpretation.
- Retrying an action with any persisted execution result, including a failed
  execution.
- Abandonment, status persistence, schema changes, owner decisions, or
  optimistic removal from the rail.

## Decisions

### 1. Use `state=stalled` as a rail lane, not a new route or stored state

The page reads the query parameter and selects the corresponding existing flat
request. The route remains `/approvals` (and `/approvals/:id` for dossiers),
so a stalled dossier can retain `?state=stalled` without creating a parallel
screen or a duplicate state model.

Alternative: add `/approvals/stalled`. Rejected because the flat API and
existing radar already use the `state=stalled` vocabulary; another route would
make the URL, API, and cache keys drift.

### 2. Link the radar to the exact filtered lane

The verdict's stalled clause receives `/approvals?state=stalled` as its
destination. It never invents an individual approval-id link from an aggregate
count. The filtered lane continues to render degraded-source evidence, so a
partial count does not become a false all-clear.

Alternative: leave the count plain text. Rejected because the owner still has
to hunt through bounded history for the affected rows.

### 3. Keep waiting-only decision shortcuts out of the stalled lane

The stalled rail remains keyboard-navigable with its existing native row
buttons and `j`/`k` movement, but it registers no approve/deny/defer verbs.
Retry is the only existing action exposed for its exact eligible rows.

Alternative: reuse all waiting-lane shortcuts. Rejected because those verbs
would target rows that are no longer pending decisions.

### 4. Invalidate by approval read-family prefixes after successful retry only

The retry mutation waits for the server response, then invalidates the flat
prefix (covering waiting and stalled variants), history, the selected dossier,
and approval metrics. It never removes a row locally or invalidates on an
error; the server remains authoritative about whether dispatch ran.

Alternative: optimistically remove the row or invalidate before completion.
Rejected because dispatch can fail and an approved/null-result row must remain
truthfully visible until the server confirms a changed result.

### 5. Verify an unlisted stalled deep link through a fresh, isolated query

The normal dossier key may be populated by a route prefetch or an earlier
visit. An unlisted `?state=stalled` deep link therefore uses a separate query
key and requires a completed request made after the active stalled rail has
settled. The route renders only the verifier response itself when its id,
approved status, and explicit null result satisfy the stalled predicate. A
pending, errored, mismatched, or non-eligible verifier response leaves the
dossier absent; cached ordinary detail data is not an eligibility authority.

Alternative: reuse the ordinary detail key and inspect its current data.
Rejected because React Query may expose an already-cached success while its
fresh request is still pending or later fails, which could briefly reopen stale
Retry or decision controls under the Stalled lane.

## Risks / Trade-offs

- [A retry response reports no dispatch] -> retain the row, show a neutral
  not-run result rather than claiming a particular cause, and let the error
  response preserve its server-provided diagnostic.
- [A flat source is degraded] -> the stalled lane displays the existing
  degraded-source note; the count/link represents observed coverage, not an
  all-clear.
- [A bookmarked dossier carries a lane query] -> retain the query when rail
  navigation updates the dossier URL, so Back/reload remains in the same lane.
- [An unlisted direct id has an ordinary cached dossier] -> wait for the
  isolated verifier and render only its current eligible payload, not the
  cached route detail.

## Migration Plan

1. Deploy the frontend-only lane and retry reconciliation behavior against the
   already-shipped flat stalled API.
2. Roll back by removing the lane and verdict destination; no persisted state
   or API migration needs repair.

## Open Questions

None. The assigned Bead fixes the route vocabulary, retry predicate, and
non-optimistic completion behavior.
