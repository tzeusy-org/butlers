## Why

Raw OwnTracks fixes survive indefinitely (bu-s11n0s.7). `core_081` creates `connectors.owntracks_points` with no TTL, and the six-hour connector purge deletes only `public.ingestion_events`. The declared 30-day location promise is therefore not enforced for durable coordinates, raw payloads or Chronicler point-event copies.

The full protocol (bead design, run16 #7) keeps every original outcome: a 30-day default the owner may shorten, deletion only after genuine place/movement projection coverage, reduced-precision summaries with a forgotten-row receipt, and visible lag or failure instead of deleting unprojected evidence. It lands in slices.

## What changes (slice 1: declared, owner-shortenable horizon)

- Migration `chronicler_028` adds `chronicler.location_retention_policy`, a single-row typed policy record: `days` (CHECK 1..30, default 30), a compare-and-set `version`, `updated_at` and `updated_by`. The default row is seeded at migration time, never from model or caller input.
- `GET /api/chronicler/location-retention` returns the horizon, its version, the bounds, the raw-age basis, the precision kept after forgetting, the covered sources and `status = not_enforced`.
- `PUT /api/chronicler/location-retention` is owner-only (`require_dashboard_owner_control`). It takes a strict body (`days`, `expected_version`), refuses booleans, strings, floats, nulls, out-of-range values and unknown fields (including a caller actor), and derives the actor from authentication. A stale version returns 409 and writes nothing.
- A missing policy row fails closed (503). The API never reports a default in its place.

No location data is read, altered or deleted by this slice. `status` stays `not_enforced` until raw expiry is gated on projection coverage, so nothing claims raw evidence was forgotten.

## Remaining slices (not in this change yet)

- Immutable per-row retention clock on raw fixes, plus per-row projection coverage written in the same transaction as projection output.
- Cumulative movement `path_m`, versioned ~150 m spatial coarsening, tombstones and the forgotten-row receipt.
- Two-stage purge: a Chronicler-prepared grant, then a connector-owned raw delete with a committed receipt. Projection-lag and purge-failure owner conditions.
- Map plaque and source-strip states, and coverage of the raw-copy cohort (Switchboard inbox, route copies).

## Non-goals

- No new location source and no change to place clustering.
- `OWNTRACKS_RETENTION_DAYS` keeps governing `public.ingestion_events` audit rows only. It is not a second raw-policy authority.
