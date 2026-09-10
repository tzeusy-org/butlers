## ADDED Requirements

### Requirement: Contact routes are compatibility aliases

The routes `/contacts` and `/contacts/:contactId` SHALL remain compatibility aliases, not
canonical pages. Both MUST replace-navigate to `/entities/index?has=contact`. Because the retired
`public.contacts` identity and its per-contact resolver no longer exist, the detail alias MUST NOT
invent an entity ID, render a not-found claim about the legacy ID, or revive the retired contact
detail page. Owners reach canonical `/entities/:entityId` details from the entity index.

#### Scenario: Contact index URL redirects to the filtered entity index

- **WHEN** a user navigates to `/contacts`
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`

#### Scenario: Legacy contact detail URL falls back to the filtered entity index

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`
- **AND** it MUST NOT claim that `abc-123-uuid` resolved to an entity

### Requirement: Pending identities queue on the entity index

The entity index page (`/entities/index?has=contact`) SHALL display a "Pending
Identities" section listing all contacts with
`metadata.needs_disambiguation = true`. This section MUST appear above the
main entity table when pending contacts exist.

#### Scenario: Pending identities displayed

- **WHEN** a user navigates to `/entities/index?has=contact` and 2 temporary
  contacts exist with `metadata.needs_disambiguation = true`
- **THEN** a "Pending Identities" section MUST appear above the entity table
- **AND** each pending contact MUST display the contact's name, source
  channel, source value, and creation date

#### Scenario: Merge action on pending identity

- **WHEN** the user clicks "Merge" on a pending identity
- **THEN** a dialog MUST open with a contact search/select input
- **AND** the user MUST be able to search existing contacts by name
- **AND** selecting a contact and confirming MUST call the merge API
- **AND** the pending identity MUST disappear from the queue after successful
  merge

#### Scenario: Confirm as new action on pending identity

- **WHEN** the user clicks "Confirm as new" on a pending identity
- **THEN** the `needs_disambiguation` flag MUST be removed from the contact's
  metadata
- **AND** the contact MUST move to the main entity table

#### Scenario: Archive action on pending identity

- **WHEN** the user clicks "Archive" on a pending identity
- **THEN** the contact's `listed` MUST be set to `false`
- **AND** the pending identity MUST disappear from the queue

#### Scenario: No pending identities

- **WHEN** no contacts have `metadata.needs_disambiguation = true`
- **THEN** the "Pending Identities" section MUST NOT be displayed

## REMOVED Requirements

### Requirement: Contact detail page canonical route is /contacts/:contactId

**Reason**: The retired contact identity can no longer be resolved at this compatibility boundary.

**Migration**: Both `/contacts` and `/contacts/:contactId` replace-navigate to `/entities/index?has=contact`; canonical details use `/entities/:entityId`.

### Requirement: Contact detail page conforms to the detail-page archetype

**Reason**: The standalone contact detail page was decommissioned, so it no longer owns a detail-page shell.

**Migration**: The entity detail page owns the canonical detail archetype and contact-channel composition.

### Requirement: Pending identities queue on contacts page

**Reason**: The standalone contacts page and pre-Plex entity route are retired.

**Migration**: Preserve the queue at `/entities/index?has=contact`.
