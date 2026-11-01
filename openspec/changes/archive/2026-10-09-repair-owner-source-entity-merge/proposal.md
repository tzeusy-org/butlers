## Why

The existing role-union merge assigns owner to the target while the source still
owns the immediate singleton index entry. Its later source tombstone also retains
owner, contradicting both the singleton and role-union contracts.

## What Changes

- Release only the source owner role while tombstoning it before assigning the
  target union, inside the existing locked transaction.
- Extend existing owning nodes for old-order refusal, downstream rollback,
  independent readback, runtime authorization and existing role-union companions.
- Add owner-source and rollback scenarios to the full existing role-union requirement.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `entity-identity`: clarify singleton-preserving role union and atomic failure.

## Impact

Relationship's existing merge service, its owning test file and identity-model
documentation. The index, migrations, authorization boundaries, callers and API
remain unchanged. Real PostgreSQL qualification uses public main's fresh migration
helper without depending on the unmerged template cache.
