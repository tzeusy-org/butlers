## MODIFIED Requirements

### Requirement: Multi-Account Leak Prevention

The owner-default `/secrets` inventory projection SHALL surface ONLY the primary Google account's credential. Non-primary Google accounts SHALL NOT appear in the owner-default projection and SHALL be accessible ONLY under an explicit `?identity=<entity_id>` lens targeting that account's companion entity.

This requirement is a security invariant. It MUST hold regardless of how many Google accounts are connected or which account is designated primary at any given time.

#### Scenario: Owner-default inventory surfaces only the primary Google account

- **WHEN** `GET /api/secrets/inventory` is called without an `?identity=` parameter (owner-default projection)
- **AND** the system has two or more connected Google accounts (e.g. a primary `owner@example.com` and a non-primary `owner.secondary@example.com`)
- **THEN** the response SHALL include exactly one `google_oauth_refresh` entry in the `user` array
- **AND** that entry SHALL correspond to the primary account (`is_primary = true` on `public.google_accounts`)
- **AND** the non-primary account's `google_oauth_refresh` entry SHALL NOT appear in the response

#### Scenario: Non-primary account credential accessible under explicit identity lens

- **WHEN** `GET /api/secrets/inventory?identity=<non_primary_entity_id>` is called
- **AND** `<non_primary_entity_id>` is the companion entity ID of a non-primary Google account
- **THEN** the response SHALL include the `google_oauth_refresh` entry for that non-primary account
- **AND** the primary account's `google_oauth_refresh` entry SHALL NOT appear in this identity-scoped response

#### Scenario: Single Google account — no leak surface exists

- **WHEN** exactly one Google account is connected and it is primary
- **THEN** the owner-default inventory SHALL surface that account's `google_oauth_refresh` entry
- **AND** no `?identity=` parameter is needed to reach the scope-set picker at `/secrets?focus=u:google`
