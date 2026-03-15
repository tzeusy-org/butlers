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
