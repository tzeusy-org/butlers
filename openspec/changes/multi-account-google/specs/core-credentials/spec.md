## MODIFIED Requirements

### Requirement: Google OAuth Credential Lifecycle
Google credentials are split across two stores: app credentials (client_id, client_secret, scope) in `butler_secrets` under the `google` category, and the refresh token in `shared.entity_info` on the account's companion entity (resolved via `shared.google_accounts`). The `GoogleCredentials` Pydantic model validates non-empty fields. Secret values (client_secret, refresh_token) are redacted in `__repr__` and `__str__`.

#### Scenario: Store full Google credentials
- **WHEN** `store_google_credentials(store, client_id, client_secret, refresh_token, scope, account=<email_or_id>)` is called
- **THEN** app credentials are upserted in `butler_secrets` with keys `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`
- **AND** the `account` parameter is resolved to a `google_accounts` row and its companion `entity_id`
- **AND** the refresh token is upserted in `shared.entity_info` on the companion entity

#### Scenario: Store credentials with account=None (primary)
- **WHEN** `store_google_credentials(store, ..., account=None)` is called
- **THEN** the primary account is resolved from `shared.google_accounts WHERE is_primary = true`
- **AND** credentials are stored against the primary account's companion entity

#### Scenario: Load Google credentials with account selector
- **WHEN** `load_google_credentials(store, account="work@gmail.com")` is called and all required keys exist
- **THEN** app credentials are loaded from `butler_secrets` (shared across accounts)
- **AND** the refresh token is loaded from `entity_info` on the companion entity for the specified account
- **AND** a `GoogleCredentials` model is returned

#### Scenario: Load Google credentials with account=None (primary)
- **WHEN** `load_google_credentials(store, account=None)` is called
- **THEN** the primary account's refresh token is loaded
- **AND** behavior is identical to the pre-multi-account code for single-account deployments

#### Scenario: Partial credentials are an error
- **WHEN** some but not all required Google credential fields exist in the store
- **THEN** `InvalidGoogleCredentialsError` is raised listing missing fields

#### Scenario: No credentials stored
- **WHEN** none of the required Google credential keys exist
- **THEN** `load_google_credentials()` returns `None`

#### Scenario: Store app credentials (partial)
- **WHEN** `store_app_credentials(store, client_id, client_secret)` is called
- **THEN** only `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are stored
- **AND** any existing refresh tokens (on any account) are preserved

#### Scenario: Delete Google credentials for specific account
- **WHEN** `delete_google_credentials(store, account="work@gmail.com")` is called
- **THEN** the refresh token entity_info row for the specified account's companion entity is deleted
- **AND** app credentials in `butler_secrets` are NOT deleted (shared across accounts)
- **AND** the `google_accounts` row status is updated to `'revoked'`

#### Scenario: Delete all Google credentials
- **WHEN** `delete_google_credentials(store, account=None, delete_all=True)` is called
- **THEN** all refresh tokens across all account companion entities are deleted
- **AND** app credentials in `butler_secrets` are deleted
- **AND** all `google_accounts` rows are updated to status `'revoked'`

### Requirement: Resolve Google Credentials (DB-Only)
`resolve_google_credentials(store, caller, account=None)` loads credentials from DB only. Raises `MissingGoogleCredentialsError` if not available. The `account` parameter selects which Google account's refresh token to use.

#### Scenario: Credentials available for specific account
- **WHEN** Google credentials are stored for `account = "work@gmail.com"`
- **THEN** `resolve_google_credentials(store, caller="calendar", account="work@gmail.com")` returns a valid `GoogleCredentials` model with that account's refresh token

#### Scenario: Credentials available for primary (default)
- **WHEN** `resolve_google_credentials(store, caller="calendar")` is called without `account`
- **THEN** the primary account's refresh token is used

#### Scenario: Specified account not found
- **WHEN** `resolve_google_credentials(store, caller="gmail", account="nonexistent@gmail.com")` is called
- **AND** no `google_accounts` row exists for that email
- **THEN** `MissingGoogleCredentialsError` is raised with a message indicating the account is not connected

#### Scenario: No primary account exists
- **WHEN** `resolve_google_credentials(store, caller="calendar", account=None)` is called
- **AND** no account has `is_primary = true`
- **THEN** `MissingGoogleCredentialsError` is raised with a message directing the user to connect a Google account

## ADDED Requirements

### Requirement: Account Resolution Helpers

New helper functions SHALL provide account-to-entity resolution for credential operations.

#### Scenario: Resolve account entity by email

- **WHEN** `resolve_google_account_entity(pool, email="alice@gmail.com")` is called
- **THEN** the companion entity_id for the specified account is returned
- **AND** if no account exists for that email, `None` is returned

#### Scenario: Resolve primary account entity

- **WHEN** `resolve_google_account_entity(pool, email=None)` is called
- **THEN** the companion entity_id for the primary account is returned
- **AND** if no primary account exists, `None` is returned

#### Scenario: List all account entities

- **WHEN** `list_google_account_entities(pool)` is called
- **THEN** a list of `(account_id, email, entity_id, is_primary)` tuples is returned for all active accounts
