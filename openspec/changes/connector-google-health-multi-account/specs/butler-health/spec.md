# Health Butler — Multi-Account Delta

## MODIFIED Requirements

### Requirement: Owner identity validation for wellness ingest

The Health butler SHALL accept wellness envelopes whose `sender.identity` resolves to any active Google account in `public.google_accounts` owned by the butler's owner entity, not just the primary account.

#### Scenario: Owner account accepted (any health-scoped account)

- **WHEN** a wellness envelope arrives whose `sender.identity` matches the `google_user_id` (canonically the email today) of ANY active `public.google_accounts` row whose `entity_id` equals the owner entity AND whose `granted_scopes` contains all three Google Health scopes
- **THEN** the Health butler SHALL accept and translate the envelope as normal
- **AND** the resulting fact's `entity_id` SHALL be the owner entity (single owner; one fact graph, regardless of which Google account ingested the data)

#### Scenario: Foreign-identity rejection

- **WHEN** a wellness envelope arrives whose `sender.identity` does NOT match any active health-scoped `google_accounts` row for the owner entity
- **THEN** the Health butler SHALL reject the envelope without storing any fact
- **AND** SHALL log a warning naming the mismatched identity and listing the recognised owner identities

## REMOVED Requirements

### Requirement: Non-primary account rejection

**Reason:** This rule (originally `Scenario: Non-primary account rejection` under the wellness ingest requirement) silently dropped data from non-primary owner accounts. It conflicted with `connector-google-health` multi-account polling. Replaced by the owner-identity validation above, which keeps the foreign-identity safety check but accepts any active health-scoped account owned by the owner entity.
