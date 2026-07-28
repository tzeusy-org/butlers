## Context

The flat `GET /api/approvals` endpoint is the approvals dashboard's compact
read model. Its decided-history rows are intentionally bounded, so they cannot
truthfully answer whether an owner-approved action has never produced an
execution result. Approval storage already carries the two facts needed for a
read-only radar: `status` and `execution_result`.

This is a dashboard/API presentation change. The approval module remains the
owner of state transitions and execution; this change neither writes a new
state nor changes an action's lifecycle.

## Goals / Non-Goals

**Goals:**

- Define one precise derived stalled predicate: `status = approved` and
  `execution_result IS NULL`.
- Make the flat endpoint expose both a `state=stalled` list view and a
  limit-independent, whole-population `meta.stalled_count`.
- Preserve per-pool degraded-source truth so neither an incomplete count nor
  an unavailable source can render as an all-clear.
- Feed the approvals verdict opener from the metadata count rather than the
  bounded history payload.

**Non-Goals:**

- Persisting a `stalled` enum/status, changing approval transitions, or adding
  a schema migration.
- A URL-backed stalled lane, retry/recovery/abandon mutation, or dossier
  redesign.
- Exposing action arguments, execution payloads, credentials, or any other
  sensitive data in the radar.

## Decisions

### 1. Derive stalled at the flat-read boundary

The flat endpoint accepts `state=stalled` and translates it into the exact
stored predicate `approved AND execution_result IS NULL` for each eligible
approval pool. It does not introduce a stored status or reinterpret `approved`
rows with a non-null result.

Alternative: persist `stalled` after a timer or transition. Rejected because
it would add lifecycle policy, migrations, and an additional state that can
drift from the existing execution record.

### 2. Count separately from the bounded list

Every flat response runs a whole-population stalled aggregate with the same
per-pool eligibility and predicate as the stalled list. The aggregate ignores
the caller's `limit`, `offset`, and selected `state`, then writes its observed
integer to `meta.stalled_count`.

Alternative: count rows returned by the decided-history request. Rejected
because a page limit can hide stalled approvals indefinitely and produce a
false all-clear.

### 3. Keep incomplete sources explicit and fail closed in the opener

List and aggregate work share the same per-pool eligibility and report failed
pools through `meta.sources_degraded`. A response can contain a partial
observed `stalled_count`, but the frontend must treat non-empty degraded
sources as unknown coverage: it names the unavailable source and suppresses
any calm "no stalled approvals" conclusion.

Alternative: return a zero count on a failed source without metadata.
Rejected because zero would impersonate health on the trust console.

## Risks / Trade-offs

- [A pool fails after other pools report zero] -> `sources_degraded` remains
  attached to the response and the opener reports incomplete coverage instead
  of an all-clear.
- [A caller requests `state=stalled` with a small limit] -> its visible rows
  are bounded, but metadata retains the whole-population count.
- [A future execution representation is non-null but not successful] -> it is
  not stalled under this narrow read predicate; this slice deliberately does
  not introduce a new outcome taxonomy.

## Migration Plan

1. Deploy the additive query-state and metadata field with frontend support.
2. Existing `waiting`, `decided`, and `all` callers continue to receive their
   existing rows plus the additive metadata field.
3. Roll back by removing the UI clause and endpoint additions; no persisted
   data requires migration or repair.

## Open Questions

None. The Bead fixes the predicate and excludes recovery policy from this
slice.
