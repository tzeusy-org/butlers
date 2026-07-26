## Why

The day-close refresh route currently reports the active cache row's build time
for both a normal cache write and a contained invalid candidate. A caller cannot
therefore tell that a refresh generated invalid prose while an admissible row
was preserved.

## What Changes

- Add an additive invalid refresh response branch carrying the deterministic
  admission reason and no prose or provenance.
- Preserve the existing valid refresh, rate-limit, dispatch-error, writer,
  scheduler, transport, and GET response behavior.
- Specify both containment behind an admissible row and audit-only invalid
  outcomes when no admissible row exists.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `chronicler-api`: distinguish invalid day-close refresh candidates from
  normal refresh/cache reuse without exposing invalid content.

## Impact

- `roster/chronicler/api/models.py` and `roster/chronicler/api/router.py`
- Focused refresh-route API tests
- No database migration, LLM path, scheduler, transport, GET endpoint,
  frontend caller, or cross-schema change
