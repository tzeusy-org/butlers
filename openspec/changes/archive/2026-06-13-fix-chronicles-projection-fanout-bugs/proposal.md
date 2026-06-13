## Why

Five user-visible defects on the Chronicles dashboard
(`https://tzeusy.parrot-hen.ts.net/butlers-dev/chronicles`) traced back to
spec-shaped contracts that were either underspecified or violated in
implementation:

1. **All routed conversations rendered as `Conversation via unknown
   channel`** — `Spawner.trigger()` did not forward the ingestion event
   UUID to `session_create`, so `{schema}.sessions.ingestion_event_id`
   was always NULL even when the session was triggered by an ingested
   message. `CoreSessionsAdapter._resolve_contacts` joins through that
   FK to resolve the channel and contact, so every routed conversation
   ended up with the catch-all unknown-channel title.

2. **Five identical Gantt bars per Google Calendar event** (e.g. five
   "Labour Day" blocks; `daily_briefing_contribution` repeated per
   schema) — `CalendarCompletedAdapter` derived `source_ref` from the
   per-schema `calendar_event_instances.id`, so the cross-schema
   fan-out (every butler with the calendar module enabled keeps its
   own row) generated a distinct `(source_name, source_ref)` upsert
   key per schema. The upsert dedup never engaged, and the in-run
   `seen_origin` set keyed on `(source_id, origin_instance_ref)` —
   which also varied per schema — was equally ineffective.

3. **Steam game bars always started at 00:00 UTC** — the anchor logic
   that pins `end_at` to the most recent observation already exists in
   the adapter (shipped in `f05b7d6c`), but `chronicler.episodes` rows
   created before that landed are never re-projected because the
   per-source `projection_checkpoints` watermark has already advanced
   past their `recorded_at`.

4. **Spotify episodes titled `Spotify session
   (spotify:tzeusii)`** when no playlist/album context was available —
   `_project_row` only inspected `context_name` and `context_uri`,
   even though the underlying `connectors.spotify_listening_sessions`
   row already carried `track_names` populated from the Web API. The
   richest available data was being discarded.

5. **Map widget showed `Failed to load the map. Try again`** — the
   trail-sync `useEffect` called `map.addSource(...)` /
   `map.addLayer(...)` synchronously after the `new
   maplibreGl.Map(...)` constructor, but MapLibre style loading is
   asynchronous, so the first call after a fresh mount threw `Style is
   not done loading` and bubbled into `MapErrorBoundary`.

Each of these has a code fix, but they collectively reveal contract
gaps the specs should make explicit so the next adapter author does
not re-introduce the same bugs.

## What Changes

- **`butler-chronicler` spec — new requirement**: cross-schema
  fan-out collapse. Adapters that read from sources mirrored across
  butler schemas SHALL derive `source_ref` from the upstream
  identifier, not the per-schema row id, so the
  `(source_name, source_ref)` upsert key naturally collapses
  fan-out to a single chronicler row.

- **`butler-chronicler` spec — new requirement**: episode title
  resolution priority. Listening / playback / conversation adapters
  SHALL favour the most-specific available signal (track names →
  playlist context → endpoint identity for Spotify; resolved contact
  → channel → unknown for routed sessions) and SHALL NOT discard
  evidence already present in the source row when a fallback is
  taken.

- **`butler-chronicler` spec — new scenario**: source projections
  with day-bounded precision. When a source exposes only daily
  aggregates (Steam playtime), the resulting episode SHALL carry
  `precision = "day"` and SHALL anchor its end-of-day bound to the
  most recent observation timestamp inside the calendar day rather
  than always parking the bar at midnight UTC.

- **`core-spawner` spec — new requirement**: ingestion-event
  propagation through the trigger pipeline. `Spawner.trigger()`
  SHALL accept an optional `ingestion_event_id` parameter and pass
  it through to `session_create()` so the resulting session row
  joins back to `public.ingestion_events`.

- **`core-sessions` spec — new scenario**: route trigger source
  carries an ingestion event identifier whenever the originating
  message produced a `public.ingestion_events` row.

- **`dashboard-chronicles` spec — new scenario**: map widget
  resilience to async style loading. The map widget SHALL defer
  source/layer mutations until the underlying tile style has
  finished loading and SHALL NOT crash when only trail-shaped
  data is available on first mount.

## Impact

- **Code**: `src/butlers/chronicler/adapters/calendar.py`,
  `src/butlers/chronicler/adapters/spotify.py`,
  `src/butlers/core/spawner.py`,
  `src/butlers/core_tools/_routing.py`,
  `frontend/src/components/chronicles/MapWidgetInner.tsx`.

- **Migrations**:
  `roster/chronicler/migrations/010_dedup_calendar_episodes_by_origin.py`,
  `roster/chronicler/migrations/011_reset_steam_spotify_watermarks.py`,
  `alembic/versions/core/core_088_backfill_route_session_ingestion_event_id.py`.

- **Tests**: `tests/chronicler/test_spotify_adapter.py` updated to
  assert the new track-names fallback ordering and added an explicit
  test for the endpoint-only fallback case.

- **No breaking API changes** — all dashboard endpoints retain their
  shape; episode rows just get correct titles and a single instance
  per upstream Google Calendar event.

## Why this is retroactive

This change retroactively documents code that has already been
written in response to a concrete user report. Following the same
pattern as `chronicles-owner-view-privacy-defaults`, the corrective
path is to refine the specs to match the now-shipped contract so
future projection adapters and trigger-source plumbing changes are
held to it.
