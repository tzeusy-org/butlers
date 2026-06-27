## Why

The one-shot ICS export (`bu-8yi687`, merged) made the workspace calendar
portable *out* of Butlers, but explicitly deferred two follow-ups that complete
the owner-sovereignty / anti-lock-in story for epic `bu-l3k0zg`:

- a **live feed** an external calendar app can subscribe to (so it re-renders the
  current workspace on its own schedule), and
- **importing** a `.ics` back into the workspace **without creating duplicates**
  of events already present.

Bead `bu-t2zxj` adds both. Both add HTTP contracts (new dashboard API routes), so
they are specified here before implementation (the project is spec-driven).

## What Changes

- **NEW `GET /api/calendar/subscribe.ics`** — a read-only live ICS feed for
  external calendar-app subscription (`webcal://…/subscribe.ics`). Each fetch
  re-renders the current workspace entries over a rolling `now − 30d … now + 60d`
  window (within the 90-day workspace cap), reusing the same projection, filters,
  and `BUTLER:` prefix preservation as the export. Served `Content-Disposition:
  inline` so clients treat it as a subscription feed, not a download. Read-only:
  no provider write, no LLM, no migration.
- **NEW `POST /api/calendar/import/ics`** — accepts an uploaded `.ics`, parses
  its VEVENTs, and creates them in the user calendar through the existing blessed
  `calendar_create_event` MCP path **deduped** against existing workspace entries
  using the read-model's existing `(title, starts_epoch)` collapse key. An event
  already present (including every event on a re-import of the same file) is
  skipped, not duplicated; duplicates within the uploaded file are also
  collapsed. The response reports `parsed` / `imported` / `skipped_duplicates`.

## Security

Both surfaces sit behind the same network boundary (localhost + Tailscale) as
every other dashboard/calendar endpoint (see `security.md` — the trust boundary
is network-level, not app-key). The subscribe feed adds no new unauthenticated
surface and no per-feed token; it is read-only and reuses the existing serving
pattern.

## Impact

- Affected specs: `dashboard-api` (two new requirements).
- Affected code: `src/butlers/api/routers/calendar_workspace.py` (new routes on
  the existing `export_router`; the read-model collapse keys extracted into shared
  `_origin_collapse_key` / `_title_collapse_key` helpers and reused by import).
- No database migration, no new MCP tool. Import reuses the existing
  `calendar_create_event` provider-write path; subscribe is read-only.
