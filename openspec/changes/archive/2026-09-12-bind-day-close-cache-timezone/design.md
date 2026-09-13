## Context

`tier2_cache.cache_key` is the durable identity and conflict key for
day-close prose. Today all day-close readers and writers derive it as
`day_close:{date}` even though the cache window, canonical bundle evidence,
coverage witness, and manual refresh target are local-timezone specific. The
reader consequently has no timezone input, and a refresh for one timezone can
rate-limit or overwrite a cache row for a different local-day window.

The canonical `chronicler-api` spec already describes day-close requests as
`(date, tz)` and requires a settled historical local day. This change makes
that existing semantic tuple the durable cache identity without changing the
Tier-2 invocation path or cache retention model.

## Goals / Non-Goals

**Goals:**

- Require a non-empty IANA timezone that `zoneinfo.ZoneInfo` can resolve on
  day-close GET and refresh before any cache, rate-limit, or dispatch work.
- Use one helper to derive a tuple key in the form
  `day_close:{YYYY-MM-DD}:tz:{exact-IANA-name}`.
- Use that helper for cache write/read, collision-safe tuple locking, staleness provenance
  lookup, rate limiting, refresh result lookup, and editorial cache selection.
- Require the typed dashboard day-close read/refresh clients and query keys to
  include `tz`, and expose regeneration only for a stale selected tuple.
- Leave date-only rows physically unchanged and never fall back to them.

**Non-Goals:**

- No cache-row schema change, historical-row rewrite, deletion, or cache
  backfill. A new auxiliary lock registry is permitted because it carries no
  cache content and never rewrites historic cache identity.
- No new LLM call, scheduler, transport, runtime configuration, or
  cross-schema behavior.
- No timezone alias canonicalization beyond `ZoneInfo` validation. The exact
  accepted request string is the tuple member and key suffix.
- No change to cache admission, prose containment, coverage-witness, or
  invalidation predicates other than ensuring their cache-row lookup is for
  the tuple key.

## Decisions

### Use a single cache-key helper rather than duplicated string formatting

A small Chronicler-local helper will build the tuple key from a `date` and
validated timezone string. The writer obtains the effective resolved timezone
that actually defined its local window; GET and refresh validate the supplied
string before calling the helper. The editorial cache reader uses the same
helper.

Duplicated f-strings were rejected because one missed call site can restore a
date-only rate limit, lock, or stale result even while the main lookup appears
fixed. Adding a database `timezone` column was rejected because the existing
primary cache key already provides tuple identity and the approved transition
must preserve, not mutate, legacy rows.

### Make timezone mandatory at the day-close boundary

GET accepts `date` and `tz`; refresh accepts body `{date, tz}`. Missing or
unresolvable timezone values fail before a cache query, lock, rate-limit, or
Tier-2 dispatch. There is no UTC or owner-settings fallback for these two
endpoints.

Other Chronicler endpoints retain their own documented timezone defaults. This
change deliberately narrows only the day-close surface because its cache
identity represents a local-day proof.

### Treat legacy date-only rows as unavailable cache history

The new key cannot equal the old `day_close:{date}` key. Readers issue only the
new exact-key query, so old rows return the existing cache-miss behavior. They
remain in `tier2_cache` for audit/recovery and are neither deleted, marked, nor
rewritten by this delivery.

### Lock the actual tuple, not a fixed-width hash

PostgreSQL advisory-lock inputs are fixed-width integers, so no hash of an
arbitrary exact IANA string can provide the unconditional guarantee that
different `(date, timezone)` tuples never block each other. A small
Chronicler-local `day_close_cache_locks` registry therefore stores
`(local_date, timezone)` as its composite primary key. Within the writer's
existing transaction, an `INSERT ... ON CONFLICT DO UPDATE ... WHERE FALSE`
holds the exact existing row lock without mutating the row; a first writer
holds its inserted row until the transaction completes. This provides exact
same-tuple serialization while allowing different tuple rows to proceed
independently.

### Preserve tuple scope through every cache decision

The writer's exact composite-key transaction lock and invalid-candidate
preservation use the tuple. GET uses its tuple key for both the primary row and
the staleness provenance subqueries. Refresh uses it for the 24-hour rate-limit
check and post-write timestamp lookup. The briefing cache reader uses it before
applying its existing local-window and admission checks. This prevents one
timezone's entry from answering, blocking, or invalidating another's.

## Risks / Trade-offs

- [Existing clients omit `tz`] -> They receive a deterministic validation
  error instead of a potentially wrong local-day cache row; the typed frontend
  contract changes in the same delivery.
- [A legacy row appears to be useful] -> It remains preserved but is a miss;
  serving it would reintroduce unproven timezone identity.
- [A future call site constructs a key directly] -> Focused tests and a
  centralized helper protect the current writer, reader, refresh, and
  editorial paths; code review rejects new day-close formatting outside it.
- [Zone aliases resolve to different accepted names] -> The design stores the
  exact validated identifier. It does not silently claim aliases are the same
  authority or rewrite cache history.
- [Lock registry growth] -> It stores only a date and exact timezone per tuple,
  grows no faster than cache identity, and never stores prose, provenance, or
  secret material.

## Migration Plan

1. Apply the Chronicler-local lock-registry migration. It creates only an
   empty composite-key lock table and neither reads nor alters cache history.
2. Deploy the code and documentation together.
3. New scheduled and manual day-close writes create tuple-keyed rows.
4. Tuple-keyed reads miss date-only rows and use the established miss/fallback
   behavior until an admissible tuple row exists.
5. Rollback removes no cache row or historic data. The auxiliary lock registry
   can remain safely if code is rolled back; dropping it is only valid after no
   writer uses the new lock helper.

## Open Questions

None. The owner approved Option A: exact `(date, timezone)` identity with
legacy date-only rows preserved as misses.
