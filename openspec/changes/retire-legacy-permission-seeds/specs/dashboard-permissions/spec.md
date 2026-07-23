## ADDED Requirements

### Requirement: Inherited defaults have no legacy persisted seed
The permissions matrix SHALL represent a system default as an inherited cell
with no persisted operator row. A forward core migration MUST remove only
legacy `public.permissions` rows whose `reason` is exactly
`seeded default (core_121)`, and MUST preserve every row with any other reason
or value. The migration MUST safely no-op when the optional permissions table
is absent, MUST be idempotent, and its downgrade MUST NOT recreate default
rows.

#### Scenario: Exact legacy seed becomes inherited
- **WHEN** a migrated database contains a `public.permissions` row whose
  `reason` is exactly `seeded default (core_121)`
- **THEN** the forward migration removes that row
- **AND** `GET /api/permissions` serializes the corresponding active
  butler-permission cell with `inherited: true`, the system default `granted`
  value, and no persisted `reason` or `updated_at` value.

#### Scenario: Explicit operator choices survive seed retirement
- **WHEN** a migrated database contains explicit grant or revoke rows with
  reasons other than `seeded default (core_121)`
- **THEN** the forward migration leaves their `granted`, `reason`, and
  `updated_at` values unchanged
- **AND** `GET /api/permissions` serializes those cells as `inherited: false`.

#### Scenario: Partial and repeated migration runs are safe
- **WHEN** the forward migration runs against a partial core-only schema that
  lacks `public.permissions`, or runs again after legacy seeds were removed
- **THEN** it completes without changing unrelated data
- **AND** downgrading does not insert or recreate permission rows.
