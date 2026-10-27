## Why

The ingestion ledger currently labels sessions that never produced usage as
"unpriced," repeatedly dims during ordinary live updates, and offers email
replay controls even though Gmail replay is intentionally unsafe. Those states
make operational evidence misleading and leave an unsafe single-event replay
path open.

## What Changes

- Classify ingestion-session cost evidence as priced, unpriced usage, or no
  usage without exposing raw runtime failures.
- Preserve the ledger during live background refreshes without treating them as
  a visible loading transition.
- Establish one server-authoritative replay-safety policy for list affordances
  and both individual and bulk replay endpoints; email and unresolved policy
  remain fail-closed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion-event-registry`: distinguish missing model pricing from sessions
  that produced no usage, and expose replay-policy evidence on timeline rows.
- `connector-replay-queue`: require a resolved safe connector policy before a
  replay status transition is accepted.
- `dashboard-ingestion-dispatch-console`: keep passive live refreshes visually
  non-disruptive and make replay affordances respect server policy.
- `dashboard-visibility`: restrict the legacy timeline replay action to events
  that are both status-eligible and replay-safe.

## Impact

- `src/butlers/core/ingestion_events.py` and ingestion API models/routes.
- Timeline, drawer, replay-selection, and cost-evidence frontend components.
- Focused Python and Vitest regression coverage; no database migration, Gmail
  connector change, credential mutation, or live model-catalog mutation.
