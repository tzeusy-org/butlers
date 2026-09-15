## Why

The permissions matrix describes inherited defaults but currently disables every
inherited cell, so the owner cannot create the first explicit grant or revoke.
Its writable API also accepts arbitrary butler and permission strings, which
could mint decorative rows outside the runtime-enforced governance vocabulary.

## What Changes

- Make inherited matrix cells interactive while preserving their dim inherited
  appearance until an operator-submitted mutation optimistically makes the
  cell explicit.
- Reuse the existing reason-required modal and permission UPSERT to create the
  first explicit grant or revoke, with an optimistic dim-to-foreground
  transition and rollback on a failed write.
- Validate permission mutations against the runtime-enforced permission set and
  the live butler registry before writing a permission or audit record.
- Add focused UI, API, and contract regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-permissions`: inherited cells can create the first explicit
  grant/revoke, and mutation paths reject non-enforced permissions and
  unregistered butlers.

## Impact

- `src/butlers/api/routers/permissions.py` mutation validation and its public
  API contract.
- `frontend/src/pages/SettingsPermissionsPage.tsx` matrix interactivity and
  optimistic state transition.
- Focused API/frontend regressions and a narrow `dashboard-permissions`
  OpenSpec delta; no permission-model migration, new dependency, or settings
  redesign.
