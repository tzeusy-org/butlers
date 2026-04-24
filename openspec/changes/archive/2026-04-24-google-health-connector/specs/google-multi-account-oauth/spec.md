# Google Multi-Account OAuth — Google Health Delta

## ADDED Requirements

### Requirement: Scope Set Registry

The OAuth start endpoint SHALL accept a `scope_set` query parameter enumerating one or more named scope sets to include in the authorization URL. This is a net-new capability — the current implementation hard-codes a fixed default scope string and has no selector. Named scope sets allow the dashboard to grant Google Health access to an existing account without re-granting Calendar/Drive, and allow future scope sets (e.g. Photos) to be added without rewriting callers.

#### Scenario: Registered scope sets

- **WHEN** the scope catalog is consulted
- **THEN** it SHALL enumerate named scope sets keyed by identifier, each mapping to one or more fully-qualified Google OAuth scope URLs
- **AND** it SHALL contain at least:
  - `base` — `openid email profile` (identity basics)
  - `calendar` — `https://www.googleapis.com/auth/calendar` and related read variants
  - `drive` — `https://www.googleapis.com/auth/drive.readonly` and related variants
  - `gmail` — existing Gmail scopes already used by `connector-gmail`
  - `health` — `https://www.googleapis.com/auth/googlehealth.sleep`, `https://www.googleapis.com/auth/googlehealth.activity_and_fitness`, `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements`
- **AND** the `base` set SHALL always be included implicitly so userinfo calls succeed regardless of which other sets are requested

#### Scenario: Single-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=health` is called
- **THEN** the generated authorization URL SHALL include the `health` set's scopes unioned with any scopes already stored in `granted_scopes` for the hinted account (scope-widening, never scope-replacement)
- **AND** SHALL implicitly include the `base` set

#### Scenario: Multi-set request

- **WHEN** `GET /api/oauth/google/start?scope_set=calendar,drive,health&force_consent=true&account_hint=owner@example.com` is called
- **THEN** the authorization URL SHALL include the union of scopes for all three requested sets (plus `base`)
- **AND** the callback SHALL update `granted_scopes` on the account with the full union after successful consent

#### Scenario: Unknown scope set

- **WHEN** `GET /api/oauth/google/start?scope_set=bogus` is called
- **THEN** the endpoint SHALL return HTTP 400 with `{"error": "unknown_scope_set", "scope_set": "bogus", "known": [...]}`
- **AND** SHALL NOT fall back to the old hard-coded default scope list silently

#### Scenario: Backward compatibility for callers that omit scope_set

- **WHEN** `GET /api/oauth/google/start` is called with no `scope_set` parameter
- **THEN** the endpoint SHALL behave as it does today (use the pre-existing default scope composition used by Calendar/Drive/Gmail bring-up) so existing integrations are not broken
- **AND** Google Health scopes SHALL only be included when explicitly requested via `scope_set=health`

### Requirement: Google Health Scopes are Restricted

The scope catalog SHALL document that the three Google Health scopes are classified Restricted by Google.

#### Scenario: Restricted-scope documentation in the OAuth catalog

- **WHEN** a developer or operator reads the Google OAuth scope catalog source
- **THEN** each Google Health scope entry SHALL carry an inline comment noting:
  - The scope is classified Restricted by Google
  - Production-mode use requires a one-time privacy and security review of the OAuth client
  - Test mode is sufficient for single-developer / single-user self-hosting, subject to a 7-day refresh token expiry

#### Scenario: Test-mode awareness in the OAuth callback

- **WHEN** the OAuth callback completes for a Google Health scope grant and the underlying OAuth client is in test mode
- **THEN** the callback SHALL set a metadata flag on the `google_accounts` row (`metadata.google_health_test_mode = true`) capturing that the refresh token will expire in 7 days
- **AND** the dashboard SHALL surface this flag as a warning banner on the account card

### Requirement: Additive Schema Support for Test-Mode Tracking

The `public.google_accounts` table SHALL support the metadata flag and refresh-timestamp columns that the test-mode warning and the dashboard status card rely on.

#### Scenario: Metadata JSONB column

- **WHEN** the `public.google_accounts` schema is migrated as part of this change
- **THEN** it SHALL include a `metadata JSONB NOT NULL DEFAULT '{}'::jsonb` column (if not already present)
- **AND** `metadata.google_health_test_mode` SHALL be a boolean flag written only by the OAuth callback
- **AND** absence of the key SHALL be interpreted as "not test mode"

#### Scenario: Last-refresh timestamp column

- **WHEN** the OAuth callback issues or refreshes a token for a `google_accounts` row
- **THEN** `public.google_accounts.last_token_refresh_at TIMESTAMPTZ` SHALL be updated to `now()` (column added by this change if not already present)
- **AND** the dashboard's 7-day test-mode expiry heuristic SHALL read this column

### Requirement: Scope-Selective Revocation

The OAuth pipeline SHALL support revoking a subset of an account's granted scopes without disconnecting the full account.

#### Scenario: Revoke Google Health scopes only

- **WHEN** `DELETE /api/connectors/google-health/disconnect` is invoked
- **THEN** the pipeline SHALL call Google's token-revocation endpoint scoped to the three Google Health scopes
- **AND** SHALL update `public.google_accounts.granted_scopes` to remove the three entries while preserving `calendar`, `drive`, and any other granted scopes
- **AND** SHALL NOT delete the `google_accounts` row or the companion entity
- **AND** the Google Health connector SHALL detect the scope removal on its next `granted_scopes` check and transition to degraded mode

#### Scenario: Full account disconnect preserves semantics

- **WHEN** an owner fully disconnects a Google account via the existing `DELETE /api/oauth/google/accounts/<id>` endpoint
- **THEN** all Google Health scopes SHALL be revoked alongside any other granted scopes (union revocation; no change to existing behaviour, documented here for clarity against the new scope-selective endpoint)

## Source References

- `google-multi-account-oauth` (base spec)
- `google-account-registry` (column additions reference; see "Additive Schema Support" above)
- `connector-google-health` (consumer of the Health scope set)
- `dashboard-google-accounts` (UI surface for granting / revoking scopes)
- Google Health API documentation: https://developers.google.com/health/about — Restricted scope classification
