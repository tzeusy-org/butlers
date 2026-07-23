## Why

The deterministic memory-consolidation job invokes the live Spawner to obtain
LLM output, so its successful runtime session is currently re-stored as a raw
memory episode. That creates self-referential consolidation input without
removing the useful session audit record.

## What Changes

- Exclude only successful sessions with the exact trigger source
  `schedule:consolidation` from the Spawner's session-to-episode write boundary.
- Keep normal session creation and completion intact for those sessions.
- Preserve existing episode writes for all other successful sessions, including
  ordinary scheduled tasks such as `schedule:daily_digest`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-spawner`: Successful-session memory episode persistence gains an exact
  `schedule:consolidation` exclusion while preserving the normal lifecycle and
  all other trigger sources.

## Impact

- `src/butlers/core/spawner.py` at the session episode-write boundary.
- Focused Spawner regression tests and the `core-spawner` delta specification.
- No schema, API, scheduler-dispatch, cross-schema, cleanup, or model-tier
  changes.
