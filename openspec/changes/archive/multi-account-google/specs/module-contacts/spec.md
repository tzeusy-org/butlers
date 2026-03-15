## MODIFIED Requirements

### Requirement: ContactsModule Configuration and Scaffold

The module is configured under `[modules.contacts]` in `butler.toml` with `providers` (list of provider configs, required), `include_other_contacts` (bool, default false), and `sync` sub-config (`enabled`, `run_on_startup`, `interval_minutes` default 15, `full_sync_interval_days` default 6).

Each entry in `providers` is a table with `type` (required, e.g. `"google"`, `"telegram"`), `account` (optional, email string — Google account to use for `type = "google"`), plus provider-specific keys. The legacy single-provider `provider` string field is accepted for backward compatibility and interpreted as `providers = [{type = "<value>"}]`.

Example multi-provider config with multiple Google accounts:
```toml
[modules.contacts]
include_other_contacts = false

[[modules.contacts.providers]]
type = "google"
account = "personal@gmail.com"

[[modules.contacts.providers]]
type = "google"
account = "work@gmail.com"

[[modules.contacts.providers]]
type = "telegram"
```

#### Scenario: Valid multi-provider config with multiple Google accounts

- **WHEN** `ContactsConfig` is provided with `providers = [{type = "google", account = "personal@gmail.com"}, {type = "google", account = "work@gmail.com"}, {type = "telegram"}]`
- **THEN** each provider type is normalized to lowercase and trimmed
- **AND** sync defaults are applied (15-minute incremental, 6-day full sync)
- **AND** a `ContactsProvider` instance is created for each entry
- **AND** the sync state key for each Google provider includes the account email: `("google", "personal@gmail.com")` and `("google", "work@gmail.com")`

#### Scenario: Legacy single-provider config

- **WHEN** `ContactsConfig` is provided with `provider = "google"` (no `providers` list)
- **THEN** it is treated as `providers = [{type = "google"}]` with `account = None` (primary Google account)
- **AND** behavior is identical to the multi-provider form

#### Scenario: Duplicate provider types with distinct accounts

- **WHEN** `providers` contains two entries with `type = "google"` and distinct `account` values
- **THEN** startup SHALL succeed
- **AND** each entry is treated as an independent provider instance with its own sync state

#### Scenario: Duplicate provider types without account disambiguation

- **WHEN** `providers` contains two entries with `type = "google"` and neither has an `account` field
- **THEN** startup SHALL raise a `RuntimeError` indicating that multiple Google providers require distinct `account` fields

#### Scenario: Unsupported provider at startup

- **WHEN** a provider entry has a `type` not in the supported set (`{"google", "telegram"}`)
- **THEN** startup raises a `RuntimeError` with a descriptive message listing supported providers

#### Scenario: Sync disabled

- **WHEN** `sync.enabled = false`
- **THEN** the module skips runtime startup and logs a message
- **AND** MCP tools return a clear error indicating the sync runtime is not running

### Requirement: Google OAuth Credential Resolution

Credentials are resolved from the DB-backed credential store (`butler_secrets`), not environment variables. Each Google provider entry resolves credentials for its configured account.

#### Scenario: Credentials resolved for specific account

- **WHEN** on_startup is called with a Google provider entry with `account = "work@gmail.com"`
- **THEN** `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` are resolved from `butler_secrets`
- **AND** the refresh token is resolved from `shared.entity_info` on the companion entity for the `work@gmail.com` Google account
- **AND** if the account is not connected or credentials are missing, a `RuntimeError` is raised directing the user to the dashboard OAuth flow for that account

#### Scenario: Credentials resolved for primary account (default)

- **WHEN** on_startup is called with a Google provider entry without `account`
- **THEN** the primary Google account's credentials are resolved
- **AND** behavior is identical to pre-multi-account deployments

#### Scenario: No credential store provided

- **WHEN** on_startup is called without a credential store
- **THEN** a `RuntimeError` is raised (env fallback is not supported for contacts)

#### Scenario: Account scope validation

- **WHEN** on_startup resolves credentials for a Google account
- **THEN** the account's `granted_scopes` SHALL be checked for `contacts.readonly` (or `contacts`)
- **AND** if required scopes are missing, a descriptive error SHALL direct the user to re-authorize

### Requirement: Multi-Provider Sync Runtime

When multiple providers are configured, the `ContactsSyncRuntime` manages independent sync loops for each provider.

#### Scenario: Independent provider sync loops with account keying

- **WHEN** the runtime starts with providers `[{type: "google", account: "personal@gmail.com"}, {type: "google", account: "work@gmail.com"}, {type: "telegram"}]`
- **THEN** each provider runs its own sync loop with the shared `interval_minutes` and `full_sync_interval_days` schedule
- **AND** sync state (cursors, timestamps, errors) is tracked independently per provider via `ContactsSyncStateStore` keyed by `(provider, account)` where `account` is the email for Google or `"default"` for providers without account selection

#### Scenario: Provider failure isolation

- **WHEN** the Google provider for `work@gmail.com` sync fails (e.g., expired OAuth token)
- **THEN** the Google provider for `personal@gmail.com` and Telegram provider syncs continue unaffected
- **AND** the failed provider's `last_error` is recorded in its own sync state
- **AND** MCP tools report per-provider, per-account status

#### Scenario: contacts_sync_now with provider and account filter

- **WHEN** `contacts_sync_now` is called with `provider = "google"` and `account = "work@gmail.com"`
- **THEN** only the Google provider sync for `work@gmail.com` runs
- **AND** if `provider` is omitted, all configured providers sync
- **AND** if `provider = "google"` is specified without `account`, all Google provider instances sync

#### Scenario: contacts_sync_status multi-provider multi-account

- **WHEN** `contacts_sync_status` is called with multiple Google accounts configured
- **THEN** the response includes per-provider, per-account sync state (cursor age, last sync, last error, contact count)
