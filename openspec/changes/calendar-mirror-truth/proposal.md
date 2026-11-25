## Why

A butler-authored scheduled-task event is never mirrored to Google: its local link id was stored in
the provider-id column, so the mirror skipped the create, patched an event that does not exist and
logged the failure at debug. A failed delete of a disabled task's mirror was swallowed and the link
nulled, leaving a ghost event nothing retries. `calendar_set_primary` changed a fleet-shared key but
only one butler's in-memory default (bu-5ii4gj.4, slice B).

## What changes

- `scheduled_tasks.calendar_link_id` holds the caller's stable handle; `calendar_event_id` holds only
  the id Google returned. Migration `core_377` moves UUID-shaped provider ids into the link column.
- Each mirror row records `mirror_state` (`pending`, `mirrored`, `failed`, `retiring`), its last error
  and attempt count. A failed push or delete keeps the provider id and is retried every sync.
- An unchanged mirror sends nothing (a fingerprint of what was sent).
- Calendar sync health reports the counts and marks the internal scheduler source failed while any
  mirror is failing.
- `calendar_set_primary` writes the shared key first and refuses when it cannot; every calendar
  butler re-reads that key when it resolves a call.

## Deferred

- The sync deadman does not yet watch mirror state; health surfaces it.
