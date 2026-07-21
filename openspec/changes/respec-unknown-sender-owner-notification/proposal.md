## Why

The retired `contacts-identity` requirement described owner notification in
terms of a temporary `public.contacts` row. The owner has confirmed that
unknown-sender surfacing remains desired, but the live identity model now uses
transitory `public.entities` rows marked `metadata.unidentified = true`.

## What Changes

- Re-specify one-time owner surfacing for a newly discovered unknown sender as
  part of the transitory-entity convention, without restoring a contact-table
  dependency.
- Point Switchboard's unknown-sender identity-resolution scenario at the
  entity-first convention instead of the archived `contacts-identity` spec.
- Document the current activation gap: the helper creates an entity-only
  sender, but its inactive notification branch still uses a legacy contacts
  target and does not provide durable, race-safe deduplication; fleet
  Switchboard wiring also does not enable identity resolution or provide the
  owner-notification callback.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `entity-identity`: Define entity-first owner notification and idempotence for
  unknown-sender transitory entities.
- `switchboard-identity`: Replace the unknown-sender reference to the retired
  contact-table contract with the entity-first convention.

## Impact

- Authoritative specs: `entity-identity` and `switchboard-identity`.
- Developer documentation: `docs/concepts/identity-model.md` records the
  runtime activation gap so it does not promise fleet behavior that is not
  wired today.
- No migration, table restoration, API change, or runtime code change is in
  this slice. A later implementation change must enable identity resolution in
  the Switchboard pipeline, route owner delivery through the standard
  notification boundary, replace the legacy contacts target, and establish
  durable, race-safe notification deduplication.
