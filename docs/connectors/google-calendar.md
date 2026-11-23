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
  from memory and the stored cursor. A full sync skips `cancelled` events; cancellations are
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
