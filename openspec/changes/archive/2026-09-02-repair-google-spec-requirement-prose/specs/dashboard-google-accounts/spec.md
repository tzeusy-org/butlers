## MODIFIED Requirements

### Requirement: Per-Account Credential Status

The dashboard SHALL expose a per-account Google status endpoint reporting the
credential, scope, and token-validity state of one account.

#### Scenario: Account-level credential status

- **WHEN** `GET /api/oauth/google/accounts/<id>/status` is called
- **THEN** the response SHALL include:
  - `has_refresh_token`: boolean
  - `has_app_credentials`: boolean (shared across all accounts)
  - `granted_scopes`: array of scope strings
  - `missing_scopes`: array of scopes required by configured modules but not granted
  - `token_valid`: boolean (result of a test token refresh)
  - `last_token_refresh_at`: timestamp or null

### Requirement: Test-Mode Pre-Verification Warning

The dashboard SHALL warn the owner while a Google account's Health scopes are
held under an unverified test-mode OAuth client, and SHALL escalate that
warning as the consent expiry approaches.

#### Scenario: Test-mode banner

- **WHEN** `metadata.google_health_test_mode = true` on the Google account row
- **THEN** the Google Health status card SHALL render an orange banner warning that consent expires every 7 days until production-mode verification completes

#### Scenario: Approaching refresh expiry

- **WHEN** `last_token_refresh_at` on a test-mode account is older than 5 days 6 hours
- **THEN** the banner SHALL elevate to a red variant warning that consent is about to expire
- **AND** SHALL link directly to the re-consent flow for `scope_set=health`
