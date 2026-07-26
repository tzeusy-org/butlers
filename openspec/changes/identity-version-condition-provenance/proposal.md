## Why

Complete snapshots already resolve a condition whose producer retired its old
fingerprint after an identity-payload version bump. Without provenance, that
terminal event is indistinguishable from genuine recovery and operators cannot
trace the successor that replaced the historical identity.

## What Changes

- Record a producer-declared identity-payload version as condition evidence.
- Let the first explicitly declared successor link to its predecessor when a
  complete snapshot resolves the predecessor by absence after a version bump.
- Render the resulting terminal reason as supersession, rather than recovery,
  in the existing Standing Conditions panel.
- Preserve ordinary recovery, incomplete-snapshot non-resolution, immutable
  historical fingerprints, and idempotent successor confirmations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `infrastructure-reliability`: identity-payload version-bump provenance and
  operator-facing terminal status for condition episodes.

## Impact

- `src/butlers/core/condition_ledger.py` and the existing infrastructure
  producers carry and retain explicit identity-version provenance in condition
  metadata.
- `frontend/src/components/system/StandingConditionsTile.tsx` distinguishes a
  superseded terminal episode from a recovered one without a new dashboard
  surface or API route.
- No condition fingerprint migration, historic backfill, or new lifecycle
  state is introduced.
