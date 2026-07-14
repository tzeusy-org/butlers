# Contacts Identity — Remove Orphaned preferred_channel

## REMOVED Requirements

### Requirement: contacts.preferred_channel CRM column and PATCH write path
After existing values are migrated to `prefers-channel` facts, the
`public.contacts.preferred_channel` column, the
`PATCH /api/relationship/contacts/{id}` `preferred_channel` write handling (and
the endpoint, if it serves no other field), and the `usePatchContact` frontend
hook are removed. Channel preference is owned entirely by the entity
`prefers-channel` fact (see `relationship-facts`).

**Reason for removal**: the column was orphaned (written only by the dashboard,
read by no runtime path); its sole consumer was the COMPAT-ONLY
`usePatchContact` → `PATCH /contacts/{id}` write documented in
`ContactChannelCard.tsx`. Tracked by `bu-g0y3m`.

#### Scenario: Migration preserves existing preferences
- **WHEN** the data migration runs against contacts that had a non-null
  `preferred_channel` and a resolvable `entity_id`
- **THEN** each becomes an active `prefers-channel` fact on that entity
- **AND** the count of migrated facts equals the count of non-null,
  entity-resolvable column values (backfill parity)

#### Scenario: Column dropped after migration
- **WHEN** the migration has completed and the dashboard writes via the fact API
- **THEN** `public.contacts.preferred_channel` no longer exists
- **AND** no API endpoint accepts a `preferred_channel` field
