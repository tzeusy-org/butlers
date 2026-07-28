## Why

`core_121` materialized system defaults as `public.permissions` rows. Those rows
look indistinguishable from an operator choice even though the permissions API
already defines an absent row as an inherited default. Retiring only the
identifiable historical seeds restores an honest operator signal without
changing the default policy or rewriting real choices.

## What Changes

- Add one forward, guarded core migration that deletes only rows whose reason
  is exactly `seeded default (core_121)`.
- Preserve explicit grants and revokes, including values that match the old
  default, and leave downgrade intentionally non-mutating.
- Add migrated-database and API regression coverage for inherited/default
  serialization, exact matching, idempotence, and partial-schema safety.
- Clarify the `dashboard-permissions` requirement that an inherited cell has no
  persisted operator row.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-permissions`: distinguish inherited defaults from persisted
  operator decisions after retiring the legacy `core_121` default seeds.

## Impact

- `alembic/versions/core/`: one successor migration on the core chain.
- `tests/migrations/` and `tests/config/test_migrations.py`: guarded migration
  and real-chain/API serialization regressions.
- No permission registry/default-policy, frontend, dependency, or external API
  shape changes.
