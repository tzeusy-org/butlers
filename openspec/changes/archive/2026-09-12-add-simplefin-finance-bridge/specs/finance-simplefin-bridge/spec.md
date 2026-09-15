## ADDED Requirements

### Requirement: Safe SimpleFIN Bridge configuration and request boundary

Finance SHALL resolve only `SIMPLEFIN_ACCESS_URL` through
`CredentialStore.resolve(..., env_fallback=False)` and SHALL treat it as
secret-bearing authentication material.  The deterministic sync SHALL require
a usable HTTPS Access URL before creating an HTTP request, use finite timeouts,
and request the SimpleFIN v2 `/accounts` resource with bounded date parameters.
It SHALL never return, log, persist, or include in an exception the Access URL,
userinfo, authorization material, upstream response body, or raw upstream error
text.

#### Scenario: Missing credential is an honest no-request result
- **WHEN** `SIMPLEFIN_ACCESS_URL` is absent from the Finance credential store
- **THEN** the sync SHALL return a sanitized `not_configured` result
- **AND** it SHALL make no HTTP request, write no ledger row, and leave
  `accounts.last_synced_at` unchanged

#### Scenario: Malformed credential is rejected before HTTP
- **WHEN** the resolved Access URL is not a usable HTTPS URL
- **THEN** the sync SHALL return a sanitized `not_configured` result with an
  invalid-configuration reason
- **AND** it SHALL make no HTTP request, write no ledger row, and expose no
  credential-derived string

#### Scenario: Revoked or failed upstream access stays sanitized
- **WHEN** a validly shaped Access URL receives a timeout, transport failure, or
  non-2xx `/accounts` response such as `403`
- **THEN** the sync SHALL return a sanitized degraded result with a stable
  reason such as `upstream_auth_failed` or `upstream_unavailable`
- **AND** it SHALL not write ledger rows or advance `accounts.last_synced_at`

#### Scenario: Settled-only bounded v2 request
- **WHEN** a configured sync starts
- **THEN** it SHALL issue one HTTPS `GET` to the Access URL's `/accounts`
  resource with `version=2`, `start-date`, and `end-date`
- **AND** it SHALL use finite client timeouts and omit any pending-inclusion
  parameter

### Requirement: Safe one-account provisioning and exact provider binding

The SimpleFIN Bridge SHALL support one safe first-run provisioning path. When no
Finance account is already bound to SimpleFIN, it SHALL validate exactly one
remote v2 account and its matching connection before creating one Finance
account with the exact non-secret `conn_id` and `account_id` provider metadata.
After provisioning, it SHALL match the sole returned remote account only by that
pair and SHALL never infer an existing local account from a display name,
institution, currency, or transaction text.

#### Scenario: First validated response provisions one exact account
- **WHEN** no local Finance account has
  `metadata.provider.name="simplefin"`
- **AND** the complete v2 response has exactly one valid account and one matching
  connection
- **THEN** the sync SHALL create one Finance account with type `other`, the
  remote connection/account labels and currency, and provider metadata
  containing the exact `conn_id` and `account_id`
- **AND** account creation SHALL happen only after complete response validation
- **AND** the successful result SHALL report `account_created=true`

#### Scenario: Ambiguous or invalid local binding does not fetch
- **WHEN** more than one local Finance account claims a SimpleFIN provider
  binding, or one claimed binding lacks a non-empty `conn_id` or `account_id`
- **THEN** the sync SHALL return a sanitized not-configured or invalid-binding
  result
- **AND** it SHALL make no HTTP request and write no ledger row

#### Scenario: One matching remote account is accepted
- **WHEN** one local account binding exists and the complete v2 response has
  exactly one account with the same `conn_id` and `account_id`
- **THEN** the sync SHALL bind incoming transactions to that local account
- **AND** it SHALL not use any account display-name comparison

#### Scenario: Empty, multiple, or mismatched remote accounts fail closed
- **WHEN** the v2 response contains zero accounts, multiple accounts, or a
  different provider metadata pair for an existing binding
- **THEN** the sync SHALL return a sanitized invalid-response result
- **AND** it SHALL not create an account, write transactions, or advance
  `accounts.last_synced_at`

### Requirement: Complete validated response precedes idempotent settled recording

The sync SHALL validate the complete v2 response before recording any Finance
transaction. This includes every required Connection field (`conn_id`, `name`,
`org_id`, and an HTTPS `sfin_url`) and every required Account field (`id`,
`name`, `conn_id`, `currency`, finite numeric-string `balance`, and valid Unix
`balance-date`). A non-empty or malformed `errlist`, malformed account response,
or invalid settled transaction SHALL fail the run before ledger writes. It
SHALL skip pending or unposted entries, and each accepted entry SHALL be
recorded through the normal internal Finance recording seam with the provider
transaction ID as `external_id`, `source="aggregator"`, and metadata limited to
non-secret provider provenance.

#### Scenario: Provider error list or incomplete data prevents partial writes
- **WHEN** the response has a non-empty `errlist`, a malformed `errlist`, or an
  invalid settled transaction
- **THEN** the sync SHALL return a sanitized incomplete-or-invalid response
  result before calling the transaction recording seam
- **AND** no transaction is written and `accounts.last_synced_at` is unchanged

#### Scenario: Missing or malformed required v2 fields prevent partial writes
- **WHEN** a Connection omits or malforms `org_id` or `sfin_url`, or the Account
  omits or malforms `balance` or `balance-date`
- **THEN** the sync SHALL return a sanitized invalid-response result
- **AND** no account or transaction is written and
  `accounts.last_synced_at` is unchanged

#### Scenario: Settled transactions preserve normal Finance semantics
- **WHEN** a complete response contains valid posted, non-pending transactions
- **THEN** each accepted transaction SHALL use the remote transaction ID as
  `external_id`, the bound local account ID, `source="aggregator"`, and safe
  SimpleFIN provider metadata
- **AND** it SHALL retain normal Finance categorization, deduplication, ledger,
  SPO mirror, and reconciliation behavior

#### Scenario: Replay converges through provider-ID idempotency
- **WHEN** the same valid SimpleFIN transaction is returned in a later overlap
  window
- **THEN** the existing `(local_account_id, external_id)` deduplication path
  SHALL return the existing ledger row without creating a duplicate
- **AND** the sync result SHALL not count the replayed row as newly `recorded`

### Requirement: Window, freshness, and concurrency truthfulness

The first successful-sync attempt SHALL request no more than 90 days of data.
After a successful run, a later request SHALL start five days before that
account's `last_synced_at`.  The sync SHALL update `last_synced_at` only after
a full response was validated and all accepted records were processed.  It
SHALL use a dedicated-connection session advisory lock so concurrent invocations
cannot fetch or write together.

#### Scenario: First and retry windows are bounded
- **WHEN** the bound account has no `last_synced_at`
- **THEN** the request `start-date` SHALL be no earlier than 90 days before the
  request time
- **WHEN** the bound account has a previous successful sync timestamp
- **THEN** the request `start-date` SHALL be five days before that timestamp

#### Scenario: Failure never fabricates fresh data
- **WHEN** configuration, locking, upstream retrieval, response validation, or
  transaction processing does not complete successfully
- **THEN** the sync SHALL leave `accounts.last_synced_at` unchanged

#### Scenario: Concurrent invocation safely skips
- **WHEN** another SimpleFIN sync holds the dedicated advisory lock
- **THEN** the losing invocation SHALL return a sanitized
  `skipped/already_running` result before HTTP or ledger writes
- **AND** a later retry after the lock releases SHALL be able to converge through
  the normal idempotent path

### Requirement: Deterministic Finance scheduling and operator documentation

Finance SHALL register `simplefin_sync` as a daily, off-top-of-hour
`dispatch_mode="job"` schedule.  The handler SHALL not call Switchboard,
`notify`, or an LLM runtime.  Finance documentation SHALL describe the owner
setup location, daily feed behavior, degraded/no-credential behavior, rollback,
and v1 limitations without including a credential value.

#### Scenario: Scheduler resolves the deterministic handler
- **WHEN** the Finance scheduler dispatches the `simplefin-sync` TOML task
- **THEN** it SHALL resolve `simplefin_sync` to the Finance bridge handler
- **AND** the handler SHALL return a structured deterministic result without
  invoking an LLM, notification, or Switchboard route

#### Scenario: Documentation makes the operator boundary clear
- **WHEN** an operator reads the Finance documentation
- **THEN** it SHALL explain how to add `SIMPLEFIN_ACCESS_URL` through the
  existing System-secret form with target `finance`, without printing an Access
  URL
- **AND** it SHALL explain that the first fully validated one-account response
  creates the exact provider-bound Finance account automatically
- **AND** it SHALL state the one-account, settled-only, 90-day/five-day,
  no-pagination, no-balance, and no-remote-mutation v1 limits
