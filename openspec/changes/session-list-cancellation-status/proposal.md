## Why

The session list currently discards the owner-cancellation outcome that the
session detail view recognizes, so an intentional Stop is presented as a
generic failure. Operators need the list to remain truthful without exposing
raw runtime error text.

## What Changes

- Add a backwards-compatible owner-cancellation discriminator to every
  `SessionSummary` list response.
- Render that discriminator as `Cancelled` in the sessions table while
  preserving existing success, failure, and running states.
- Keep list pagination, detail and Stop semantics, transport, migrations, and
  schema boundaries unchanged.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-api`: Session-list responses expose the canonical
  owner-cancellation outcome without returning an error string.
- `dashboard-visibility`: The sessions table distinguishes owner-cancelled
  terminal sessions from generic failures.

## Impact

- `src/butlers/api/read_models/sessions_v1.py` and
  `src/butlers/api/routers/sessions.py`
- `src/butlers/api/models/__init__.py`
- `frontend/src/api/types.ts` and
  `frontend/src/components/sessions/SessionTable.tsx`
- Focused backend route/read-model and frontend table tests
