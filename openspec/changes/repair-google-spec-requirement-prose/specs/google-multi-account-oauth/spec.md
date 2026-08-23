## MODIFIED Requirements

### Requirement: Force Consent for Scope Upgrade

When a scope upgrade is requested, the authorization URL SHALL force a fresh
Google consent prompt so that a new refresh token is issued covering the
combined scope set.

#### Scenario: Re-authorize with additional scopes

- **WHEN** `GET /api/oauth/google/start?account_hint=work@gmail.com&force_consent=true` is called
- **THEN** the authorization URL SHALL include `prompt=consent` to force Google to return a new refresh token
- **AND** the `scope` parameter SHALL include all requested scopes (existing + new)

### Requirement: State Token Carries Account Context

The OAuth CSRF state entry SHALL carry the account context supplied at start,
and the callback SHALL read it back during resolution.

#### Scenario: Account context in CSRF state

- **WHEN** the OAuth start endpoint generates a CSRF state token
- **THEN** the state store entry SHALL include `account_hint` (if provided) and `force_consent` flag
- **AND** the callback SHALL read these from the state store during resolution

### Requirement: Google Health Scopes are Restricted

Google Health scopes SHALL be treated as Restricted scopes: the scope catalog
SHALL document the production-mode review requirement and the test-mode
limits, and the OAuth callback SHALL record when a grant was issued under a
test-mode client.

#### Scenario: Restricted-scope documentation in the OAuth catalog

- **WHEN** a developer or operator reads the Google OAuth scope catalog source
- **THEN** each Google Health scope entry SHALL carry an inline comment noting that the scope is Restricted, production-mode use requires a one-time privacy and security review, and test mode is sufficient for single-developer / single-user self-hosting (subject to 7-day refresh token expiry)

#### Scenario: Test-mode awareness in the OAuth callback

- **WHEN** the OAuth callback completes for a Google Health scope grant and the OAuth client is in test mode
- **THEN** the callback SHALL set `metadata.google_health_test_mode = true` on the `google_accounts` row

### Requirement: Additive Schema Support for Test-Mode Tracking

The Google accounts table SHALL carry the additive columns that test-mode
tracking and the dashboard's consent-expiry heuristic read.

#### Scenario: Metadata JSONB column

- **WHEN** the `public.google_accounts` schema is migrated
- **THEN** it SHALL include a `metadata JSONB NOT NULL DEFAULT '{}'::jsonb` column (if not already present)
- **AND** `metadata.google_health_test_mode` SHALL be written only by the OAuth callback; absence of the key means not test mode

#### Scenario: Last-refresh timestamp column

- **WHEN** the OAuth callback issues or refreshes a token for a `google_accounts` row
- **THEN** `public.google_accounts.last_token_refresh_at TIMESTAMPTZ` SHALL be updated to `now()`
- **AND** the dashboard's 7-day test-mode expiry heuristic SHALL read this column

### Requirement: Scope-Selective Revocation

Disconnecting a single Google integration SHALL revoke only that integration's
scopes, preserving the account row, its companion entity, and every other
granted scope; a full account disconnect SHALL revoke them together.

#### Scenario: Revoke Google Health scopes only

- **WHEN** `DELETE /api/connectors/google-health/disconnect` is invoked
- **THEN** the pipeline SHALL call Google's token-revocation endpoint scoped to the three Google Health scopes
- **AND** SHALL update `public.google_accounts.granted_scopes` to remove the three entries while preserving `calendar`, `drive`, and other granted scopes
- **AND** SHALL NOT delete the `google_accounts` row or the companion entity

#### Scenario: Full account disconnect preserves semantics

- **WHEN** an owner fully disconnects a Google account via `DELETE /api/oauth/google/accounts/<id>`
- **THEN** all Google Health scopes SHALL be revoked alongside any other granted scopes (union revocation; no change to existing behaviour)
