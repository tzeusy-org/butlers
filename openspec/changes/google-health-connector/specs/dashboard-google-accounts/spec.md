# Dashboard Google Accounts — Google Health Delta

> **Net-new UI work.** The existing Google Accounts settings page
> (`frontend/src/components/settings/GoogleOAuthSection.tsx`) displays
> `granted_scopes` as a read-only CSV. There is no per-account scope toggle
> or scope-set picker today. The requirements below introduce both — they
> are not refinements of existing toggles.

## ADDED Requirements

### Requirement: Per-Account Scope Set Picker

The Google Accounts settings page SHALL introduce a scope-set picker on each connected Google account card. This is a net-new component — it replaces the read-only `granted_scopes` CSV display on the existing card.

#### Scenario: Picker visibility

- **WHEN** the owner views a connected Google account card
- **THEN** the card SHALL display one row per available scope set (at minimum: `Calendar`, `Drive`, `Google Health`)
- **AND** each row SHALL show the current grant state (granted / not granted), derived by checking whether `public.google_accounts.granted_scopes` contains all of that scope set's scope URLs
- **AND** each row SHALL render a toggle or button that initiates consent or revocation

#### Scenario: Granting a scope set

- **WHEN** the owner activates the toggle for `Google Health` on an account that does not currently have those scopes
- **THEN** the UI SHALL call `GET /api/oauth/google/start?scope_set=health&force_consent=true&account_hint=<account_email>`
- **AND** SHALL redirect to the Google consent screen
- **AND** on successful callback, the card SHALL re-render showing `Google Health` as granted
- **AND** a Google Health connector status card (below) SHALL appear

#### Scenario: Revoking a scope set

- **WHEN** the owner deactivates the toggle for `Google Health`
- **THEN** the UI SHALL call `DELETE /api/connectors/google-health/disconnect`
- **AND** SHALL confirm via modal: `"This revokes Google Health access only. Calendar and Drive remain connected."`
- **AND** SHALL refresh the card on success to reflect the revoked state

#### Scenario: Full-account disconnect still revokes Health

- **WHEN** the owner fully disconnects a Google account via the pre-existing `DELETE /api/oauth/google/accounts/<id>` endpoint
- **THEN** all scopes are revoked as part of the full disconnect (pre-existing behaviour)
- **AND** the Google Health connector SHALL transition to degraded mode on its next `granted_scopes` check
- **AND** no separate Google Health revocation call is needed

### Requirement: Google Health Connector Status Card

The dashboard SHALL render a status card for the Google Health connector when the primary account has granted the Google Health scope set.

#### Scenario: Status card contents

- **WHEN** the primary Google account has Google Health scopes granted
- **THEN** the dashboard SHALL display a Google Health status card with:
  - Connection state (`Healthy`, `Degraded`, `Error`)
  - Last ingest timestamp (relative format: "3 minutes ago")
  - Counts of sleep sessions and daily summaries ingested in the last 7 days
  - Token expiry estimate and a refresh indicator
  - Rate-limit headroom — rendered only when the connector's metrics surface exposes `X-RateLimit-Remaining` or equivalent for the most recent poll; otherwise the row is hidden

#### Scenario: Status data source

- **WHEN** the card loads
- **THEN** it SHALL call `GET /api/connectors/google-health/status` for its contents
- **AND** SHALL poll every 30 seconds while the page is visible

#### Scenario: Health-card state when scopes absent

- **WHEN** the primary account does NOT have Google Health scopes granted
- **THEN** the status card SHALL NOT render
- **AND** the scope-set picker row for `Google Health` SHALL surface a CTA: `"Connect Google Health to enable sleep, HR, HRV, and activity ingestion for the Health butler."`

### Requirement: Test-Mode Pre-Verification Warning

The dashboard SHALL warn the owner when the underlying OAuth client is still in Google's test mode, because refresh tokens expire after 7 days in that configuration.

#### Scenario: Test-mode banner

- **WHEN** `metadata.google_health_test_mode = true` on the Google account row
- **THEN** the Google Health status card SHALL render an orange banner:
  `"This OAuth client is in Google's test mode. Your consent expires every 7 days until the production-mode verification completes. You may need to re-grant Google Health scopes periodically."`
- **AND** the banner SHALL include a `"Learn more"` link to the deployment guide's test-mode-vs-production section

#### Scenario: Approaching refresh expiry

- **WHEN** `last_token_refresh_at` on a test-mode account row is older than 5 days 6 hours (heuristic: Google test-mode tokens are known to invalidate ~7 days after issue; the column is added by this change's schema migration)
- **THEN** the banner SHALL elevate to a red variant:
  `"Your Google Health consent is about to expire. Re-grant scopes to avoid an ingestion gap."`
- **AND** SHALL link directly to the re-consent flow for `scope_set=health`

## Source References

- `dashboard-google-accounts` (base spec)
- `google-multi-account-oauth` (OAuth flow contracts; scope-set registry; test-mode metadata column)
- `connector-google-health` (status endpoint data source)
- `module-google-health` (consumer of granted scopes)
