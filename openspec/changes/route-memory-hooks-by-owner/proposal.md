## Why

Multiple butler daemons can run in one Python process during development and
tests.  The memory module currently registers context and completed-session
episode hooks in one process-global slot, so the most recently started module
can redirect another butler's memory work into its own schema.  This is
especially unsafe when Chronicler uses its private `chronicler_mem` schema.

## What Changes

- Route started memory context and session-episode hooks by the invoking
  butler's identity rather than by last process-global registration.
- Make shutdown unregister only the exact runtime instance it registered, so a
  stale module shutdown cannot remove a replacement runtime for the same
  butler.
- Treat an unknown or stopped owner as unavailable for best-effort context and
  episode storage; never select another butler's runtime as a fallback.
- Add multi-daemon regression coverage for General, Travel, and a
  Chronicler-style private memory pool.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: started memory session hooks must preserve per-butler
  ownership and private-schema isolation when multiple daemons share a
  process.

## Impact

- `src/butlers/core/memory_hooks.py` gains owner-keyed session runtime
  registration and lookup.
- `src/butlers/modules/memory/__init__.py` registers and identity-unregisters
  each module's session runtime during lifecycle transitions.
- Focused core and memory-module tests prove routing and fail-closed behavior.
- No database migration, historical cleanup, maintenance scheduling, batching,
  catalog behavior, or model/tier policy changes are included.
