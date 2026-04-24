# Dashboard Google Accounts

## Purpose

Provides dashboard REST API endpoints for managing connected Google accounts, including listing accounts, setting primary account, disconnecting accounts, and querying per-account credential status.

## ADDED Requirements

### Requirement: Google Accounts Management API

The dashboard SHALL expose REST endpoints for managing connected Google accounts.

#### Scenario: List connected accounts

- **WHEN** `GET /api/oauth/google/accounts` is called
- **THEN** the response SHALL return an array of account objects with: `id`, `email`, `display_name`, `is_primary`, `status`, `granted_scopes`, `connected_at`, `last_token_refresh_at`
- **AND** accounts SHALL be ordered by `is_primary DESC, connected_at ASC`
- **AND** no credential material (refresh tokens, client secrets) SHALL be included

#### Scenario: Set primary account

- **WHEN** `PUT /api/oauth/google/accounts/<id>/primary` is called
- **THEN** the specified account SHALL become primary and all others SHALL have `is_primary = false`
- **AND** the response SHALL return the updated account object
- **AND** if the account ID does not exist, a 404 SHALL be returned

#### Scenario: Disconnect account

- **WHEN** `DELETE /api/oauth/google/accounts/<id>` is called
- **THEN** the account SHALL be disconnected following the full disconnect flow (revoke token, clean up entity_info, update status)
- **AND** the response SHALL confirm the disconnection
- **AND** if the account was primary, the auto-promotion SHALL be reported in the response

#### Scenario: Disconnect with force delete

- **WHEN** `DELETE /api/oauth/google/accounts/<id>?hard_delete=true` is called
- **THEN** the `google_accounts` row and companion entity SHALL be fully deleted (not just status updated)

### Requirement: Per-Account Credential Status

#### Scenario: Account-level credential status

- **WHEN** `GET /api/oauth/google/accounts/<id>/status` is called
- **THEN** the response SHALL include:
  - `has_refresh_token`: boolean
  - `has_app_credentials`: boolean (shared across all accounts)
  - `granted_scopes`: array of scope strings
  - `missing_scopes`: array of scopes required by configured modules but not granted
  - `token_valid`: boolean (result of a test token refresh)
  - `last_token_refresh_at`: timestamp or null

### Requirement: Account-Aware Credential Status Endpoint

The existing `/api/oauth/status` endpoint SHALL be updated to report per-account status.

#### Scenario: Multi-account status response

- **WHEN** `GET /api/oauth/status` is called with multiple accounts connected
- **THEN** the response SHALL include an `accounts` array with per-account status
- **AND** the top-level `state` SHALL reflect the worst-case status across all accounts (e.g., if any account has expired credentials, the top-level state is `degraded`)
- **AND** backward compatibility: if only one account exists, the response structure SHALL include the legacy flat fields alongside the new `accounts` array

### Requirement: Per-Account Scope Set Picker

The Google Accounts settings page SHALL introduce a scope-set picker on each connected Google account card.

#### Scenario: Picker visibility

- **WHEN** the owner views a connected Google account card
- **THEN** the card SHALL display one row per available scope set (at minimum: `Calendar`, `Drive`, `Google Health`)
- **AND** each row SHALL show the current grant state, derived by checking whether `public.google_accounts.granted_scopes` contains all of that scope set's scope URLs
- **AND** each row SHALL render a toggle or button that initiates consent or revocation

#### Scenario: Granting a scope set

- **WHEN** the owner activates the toggle for `Google Health` on an account without those scopes
- **THEN** the UI SHALL call `GET /api/oauth/google/start?scope_set=health&force_consent=true&account_hint=<account_email>`
- **AND** on successful callback, the card SHALL re-render showing `Google Health` as granted
- **AND** a Google Health connector status card SHALL appear

#### Scenario: Revoking a scope set

- **WHEN** the owner deactivates the toggle for `Google Health`
- **THEN** the UI SHALL call `DELETE /api/connectors/google-health/disconnect`
- **AND** SHALL confirm via modal: `"This revokes Google Health access only. Calendar and Drive remain connected."`

### Requirement: Google Health Connector Status Card

The dashboard SHALL render a status card for the Google Health connector when the primary account has granted the Google Health scope set.

#### Scenario: Status card contents

- **WHEN** the primary Google account has Google Health scopes granted
- **THEN** the dashboard SHALL display a Google Health status card with: connection state, last ingest timestamp, 7-day ingest counts, token expiry estimate, and rate-limit headroom (hidden when no rate-limit header is available)

#### Scenario: Status data source

- **WHEN** the card loads
- **THEN** it SHALL call `GET /api/connectors/google-health/status` for its contents
- **AND** SHALL poll every 30 seconds while the page is visible

#### Scenario: Health-card state when scopes absent

- **WHEN** the primary account does NOT have Google Health scopes granted
- **THEN** the status card SHALL NOT render
- **AND** the scope-set picker row for `Google Health` SHALL surface a CTA to connect

### Requirement: Test-Mode Pre-Verification Warning

#### Scenario: Test-mode banner

- **WHEN** `metadata.google_health_test_mode = true` on the Google account row
- **THEN** the Google Health status card SHALL render an orange banner warning that consent expires every 7 days until production-mode verification completes

#### Scenario: Approaching refresh expiry

- **WHEN** `last_token_refresh_at` on a test-mode account is older than 5 days 6 hours
- **THEN** the banner SHALL elevate to a red variant warning that consent is about to expire
- **AND** SHALL link directly to the re-consent flow for `scope_set=health`
