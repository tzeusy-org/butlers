## Why

The Switchboard's production notification ledger pre-serializes metadata before
binding it to asyncpg's JSONB codec. The codec serializes again, so newly
written notification metadata becomes a JSONB string rather than the structured
object required for reliable notification records.

## What Changes

- Normalize optional notification metadata to a JSON-safe Python mapping at the
  Switchboard writer boundary, then bind that mapping directly through asyncpg.
- Add a real-Postgres regression that proves a normal notification write stores
  `notifications.metadata` as a JSONB object.
- Explicitly limit this slice to preventing new write-side corruption.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-notify`: notification delivery logging preserves metadata as a JSONB
  object without pre-serializing it at the writer boundary.

## Impact

- Affected code: `roster/switchboard/tools/notification/log.py` and its
  real-pool Switchboard regression coverage.
- No API, UI, status vocabulary, request provenance, retry behavior, schema
  migration, backfill, Messenger integration, quiet-window, broker, scheduler,
  secrets, or retention change is included.
