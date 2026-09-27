## Context

The memory module stores raw episode content only for the episode retention
window, while facts, rules, and `memory_links` are durable derived knowledge.
The baseline facts and rules foreign keys use `ON DELETE SET NULL`; therefore
the normal bounded cleanup path erases the only source identifier when it
deletes an episode. `memory_links` has no foreign key and can instead retain an
unresolved episode identifier. Both outcomes misrepresent provenance.

This is an owner-authorized prerequisite to retention cleanup, not an
authorization to drain existing history. RFC 0006 permits per-schema module
tables; the dashboard is the privileged reader for cross-pool memory views.

## Goals / Non-Goals

**Goals:**

- Preserve durable fact, rule, and generic-link provenance without retaining
  source content after an episode is deleted.
- Make an unavailable source explicit and typed at every reader boundary so
  UI never offers a route to a deleted episode.
- Make every future episode deletion, including the existing cleanup path,
  atomically leave content-free provenance evidence.
- Prove the behavior with migrated PostgreSQL, API, and React regressions.

**Non-Goals:**

- No historical retention drain, backfill, bulk repair, cleanup schedule
  change, or operator SQL runbook.
- No indefinite retention of raw episode content, titles, prompts, runtime
  output, credentials, or owner-message text.
- No new MCP mutation tool or cross-schema runtime access.

## Decisions

### D1: Keep source identifiers and write a content-free tombstone

Each memory schema will gain an `episode_tombstones` table containing only the
deleted episode UUID and deletion timestamp. A `BEFORE DELETE` trigger records
the tombstone in the same transaction. Facts and rules retain their existing
`source_episode_id` scalar after their source is deleted, so their durable
content remains attributable without retaining the source's raw body.

The facts/rules `ON DELETE SET NULL` foreign keys will be removed by a new
memory migration; their source UUID is instead interpreted through the live
episode or tombstone relation. A trigger was selected over teaching only
`run_episode_cleanup` because deletion can occur through any valid local
database path. A content digest or copied excerpt was rejected: neither is
needed to prove expiry and both risk extending owner-message retention.

### D2: Resolve provenance as available, expired, or unresolved

Readers determine a source episode's state by joining the source UUID to
`episodes` and then `episode_tombstones`. `available` means the live episode
exists; `expired` means the content-free tombstone exists; `unresolved` means
neither relation proves the identifier's state. `unresolved` remains explicit
instead of being silently presented as no source.

Facts and rules expose this as a typed source-episode status. Generic
`memory_links` expose the status for either endpoint when that endpoint is an
episode. The dashboard treats only `available` as a linkable episode door;
`expired` and `unresolved` are plain provenance text.

### D3: Keep the UI and API honest without creating a recovery surface

The memory API adds the typed status to list/detail projections and memory-link
read results. Detail and register components use it to replace a deleted source
link with a visible `Source expired` state. They never hide the source or point
at `/memory/episodes/:id` when the API cannot establish availability.

No endpoint starts cleanup or restores an episode. This is provenance evidence,
not archival recovery. A future approved retention drain can rely on the
triggered tombstone invariant without changing this slice.

## Risks / Trade-offs

- [A removed foreign key permits arbitrary source UUIDs] → readers expose an
  explicit `unresolved` state, and write paths remain covered by existing
  source-resolution tests.
- [Tombstones grow] → they contain two non-content fields only and are required
  for durable provenance; no raw message data is retained.
- [A reader misses the state] → migrated-DB, API, and UI tests cover facts,
  rules, generic links, and navigation affordances.
- [Deletion trigger failure blocks cleanup] → this is intentional: a deletion
  that would erase provenance must fail atomically rather than create a silent
  loss.

## Migration Plan

1. Add the content-free tombstone table and deletion trigger in a new memory
   migration, then remove only the facts/rules source-episode foreign keys.
2. Deploy code that projects typed source state before allowing any retention
   drain. Existing cleanup code remains unchanged and only gains the trigger's
   atomic invariant.
3. Rollback removes the reader affordance only after stopping cleanup; it must
   never delete tombstones or null surviving source identifiers.

## Open Questions

None. Existing source identifiers are durable evidence; a future historical
drain remains separately owner-gated.
