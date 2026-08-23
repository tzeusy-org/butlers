## Why

The `/secrets` passport's initial inventory request waits for serial reads from every
butler pool and their audit evidence. On the live dev database, that path exceeds the
frontend's 15-second request deadline even though the dashboard API health endpoint is
healthy, leaving the owner unable to inspect credentials.

## What Changes

- Make the inventory audit lookup an index-backed top-N query per credential target.
- Bound and concurrently execute inventory source reads below the browser deadline.
- Return surviving rows with named degraded-source metadata when a source times out or
  fails, rather than hanging the entire passport.
- Make the passport explicitly incomplete when its inventory is partial, so a partial
  zero cannot read as an all-clear.
- Preserve the existing content-blind response fields and credential authority model.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: add a distinct requirement for bounded, truthful availability of
  Secrets inventory fan-out reads without rewriting the active content-blind inventory
  requirement block.

## Impact

- `src/butlers/api/routers/secrets_v2.py`: audit-query shape and inventory fan-out.
- `frontend/src/components/secrets/passport/DirectionPassport.tsx`: partial-inventory
  headline.
- Inventory API behavior: `meta.sources_degraded` becomes the truthful timeout/failure
  path for omitted sources; the response field shape remains compatible.
- Tests: Secrets inventory API coverage, audit-index performance coverage, and passport
  partial-state rendering coverage.
