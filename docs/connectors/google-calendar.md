# Google Calendar

> **Scope:** The Google Calendar connector: how it syncs, what it keeps, and what its health means.
> **Does NOT belong here:** Calendar module tools and the per-butler projections (see
> [Calendar module](../modules/calendar.md)).

The connector runs one loop per connected Google account. It reads `/calendars/primary/events` and
submits event changes to Switchboard as `ingest.v1` envelopes.

## Sync

- The first run does a full sync to establish a baseline and a `nextSyncToken`, saved through the
  cursor store (`switchboard.connector_registry.checkpoint_cursor`). Later polls send that token
  and fetch only what changed. A 410 from Google runs a full sync again; if that returns no token, the expired token is cleared
  from memory and the stored cursor. Each ingesting full sync (410 recovery or a pending resync) increments
  `connector_calendar_resync_full_ingest_total`. A full sync skips `cancelled` events; cancellations are
  processed only from incremental sync.
- Every sync request sends the same filters, `singleEvents=true` and `showDeleted=true`, and never
  `orderBy`. Google issues a sync token only to a request without `orderBy`, and a sync-token
  request must repeat the initial request's filters.
- A full sync whose last page has no `nextSyncToken` saves no cursor and reports **degraded**
  health ("Google Calendar full sync returned no sync token"). The loop then waits a poll interval
  before retrying; it never repeats the full sync back to back. Before this repair the connector
  never obtained a token, so it never ingested an event.

## Checking it

- `checkpoint_cursor` is non-null for every `google_calendar` account.
- The log line "performing initial full sync" appears once per account, not once per poll.

## Revision journal

Google keeps no edit history, so the connector appends every event version it observes to
`connectors.calendar_event_revisions` (core_369) before it submits anything or saves the sync
token. A row is keyed on `(endpoint_identity, calendar_id, event_id, etag)`: Google changes the
etag on every modification, RSVPs included, so a replayed page adds nothing and an edit adds a row.
`observation` says why it was seen: `baseline` (first full sync, routed nowhere), `change`
(incremental) or `resync` (410 recovery). The kind of change is derived by diffing an event's
consecutive revisions; it is not stored.

- Append-only: triggers refuse UPDATE, DELETE and TRUNCATE; nothing is pruned until the
  per-source retention contract (bu-uhbhzq.7).
- Titles, times and attendees are kept verbatim, and so are locations, except that meeting URLs,
  meeting ids and passcodes in a location are removed like those in a description.
  `extendedProperties` (app-private data) are not journaled. Descriptions are scrubbed of links and
  passcodes (also bare Zoom, Teams, Meet and Webex hosts). Conference data keeps only the solution
  name and key type; `hangoutLink` and attachment, source and gadget links are removed. An event
  blocked by source policy keeps structure and times only (no raw payload). If policy cannot be
  evaluated, the batch is held like a failed journal write.
- Only `connector_writer` (insert, select) and `butler_chronicler_rw` (select) can touch it.
- If the write fails, nothing is submitted, the sync token is held and health is **degraded**
  ("Google Calendar revision journal write failed"); the next cycle journals the same delta.
