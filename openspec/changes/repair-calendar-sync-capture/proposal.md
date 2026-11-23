## Why

The Google Calendar connector has never ingested an event (bu-uhbhzq.1, run 24 move 1). Its full
sync sends `orderBy=startTime`, for which Google returns no `nextSyncToken`, so no cursor is saved,
every poll repeats the full sync, and each run logs "syncToken saved" regardless.

## What changes

- The full and incremental passes send one shared set of filters (`singleEvents`, `showDeleted`)
  and no `orderBy`, because a sync-token request must repeat the initial request's filters.
- A full sync whose last page carries no `nextSyncToken` saves no cursor, logs no "saved" line,
  reports degraded health naming the missing token, and waits a poll interval before trying again.
- Slice 2 adds the append-only revision journal; slice 3 (the Chronicler `plan_changed` adapter)
  follow as their own changes.
