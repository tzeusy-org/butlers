## Context

`finance_012` already admits `transactions.source = "aggregator"`, persists
`accounts.last_synced_at`, and preserves the `(account_id, external_id)`
deduplication key used by Finance reconciliation.  This change adds the missing
producer, not a new connector topology, schema, public MCP tool, or account
registry.  A SimpleFIN Access URL is a Tier-1 Finance secret: it is an
authentication-bearing URL and therefore must never be returned, logged, or
stored in transaction metadata.

The SimpleFIN v2 `/accounts` response carries connection and account IDs plus
posted transactions.  It can also carry structured `errlist` entries that mean
the returned data is incomplete.  A partial or untrusted response must not
advance freshness or write any ledger rows.

## Goals / Non-Goals

**Goals:**

- Run one deterministic Finance job for one explicitly configured SimpleFIN
  account, with bounded HTTPS HTTP and no LLM, Switchboard, or notification
  path.
- Use `CredentialStore.resolve("SIMPLEFIN_ACCESS_URL", env_fallback=False)`
  and produce stable, sanitized status values when configuration or upstream
  data is unusable.
- On the first fully validated one-account response, create one Finance account
  with exact provider metadata:
  `accounts.metadata.provider = {"name": "simplefin", "conn_id": ..., "account_id": ...}`;
  require that binding on later runs.
- Pre-validate the complete response before ledger writes, ingest settled posted
  rows with provider-ID idempotency and safe provenance, then update freshness.
- Hold a session advisory lock for the fetch/write interval so overlapping jobs
  cannot execute concurrently.

**Non-Goals:**

- No setup-token claiming, generic account-management surface, secret creation,
  live activation, migration, balance persistence, cursor/pagination,
  multi-account support, pending lifecycle handling, remote mutation/deletion,
  or deeper history backfill. The sole account-provisioning behavior is the
  first validated SimpleFIN response described below.
- No change to public Finance MCP signatures, existing manual/email/CSV/API
  ingestion, or reconciliation matching semantics.

## Decisions

### A Finance job, not a standalone connector

`run_simplefin_sync(pool)` lives under `roster/finance/jobs/` and is registered
as a `dispatch_mode = "job"` schedule.  It writes Finance-owned ledger data
directly, so `ingest.v1`, connector heartbeat/replay machinery, and an LLM
session would add unrelated transport complexity.  This follows the existing
Finance deterministic-job pattern.

### DB-only credential resolution and a safe URL boundary

The job resolves only the claimed Access URL from Finance's credential store;
it does not claim setup tokens or fall back to environment variables.  It first
validates a syntactically usable HTTPS URL and only then creates the HTTP
client.  Missing or malformed configuration makes zero requests.  A provider
revocation is observable only as a sanitized non-2xx response after one
request, so it is reported as an upstream authentication failure with no raw
provider error or URL disclosure.

### First-response provisioning, then exact local binding

When no Finance account claims the SimpleFIN provider, the job may make the
single bounded request and validate the required connection list, exactly one
remote account, and every settled transaction before writing anything. It then
creates one Finance account with type `other`, the remote connection/account
labels and currency, and the exact `(conn_id, account_id)` provider binding.
This is cardinality-based provisioning of a new account, not name-based
matching to an existing account.

On later runs, the job finds exactly one Finance account with that provider
metadata. The stored connection/account pair prevents name-based matching. More
than one claimed local binding, an invalid claimed binding, or a response with
zero, multiple, or mismatched accounts fails closed. The only no-binding case
allowed to fetch is the first-run provisioning path.

### Complete response validation before normal Finance recording

The job parses `errlist`, all required v2 Connection fields (`conn_id`, `name`,
`org_id`, and an HTTPS `sfin_url`), all required fields on the one Account
(including finite numeric `balance` and valid Unix `balance-date`), the ISO
currency, and every candidate settled transaction into validated values before
it calls the normal Finance recording seam. Non-empty/malformed error lists,
malformed response objects, and invalid settled rows fail the whole run before
any ledger write. Pending or unposted rows are deliberately ignored, not
promoted to a lifecycle state. The internal transaction helper gains a private
`source` argument; the public `record_transaction` signature remains unchanged
and continues to call it with its existing behavior.

### Window, provenance, and freshness semantics

The first run requests at most the previous 90 days.  A later run starts five
days before the bound account's last successful sync.  The request uses
SimpleFIN v2 `start-date`, `end-date`, and `version=2` parameters, omitting the
optional pending flag so the provider's settled-only default remains active.
Each recorded row uses `external_id` from the provider, `source="aggregator"`,
and metadata limited to the provider name plus non-secret remote connection and
account IDs.  The existing dedup key makes replays converge, and replayed rows
do not inflate the job's `recorded` count. Only a fully validated and completely
recorded run updates `last_synced_at`.

### Dedicated session advisory lock

After configuration is resolved, the job acquires a dedicated pool connection
and attempts a named session advisory lock.  A losing invocation returns
`skipped/already_running` before HTTP or writes.  The winning invocation holds
the connection through fetch, validation, record, and timestamp update, and
unlocks in `finally`.  A session lock is preferred over a transaction lock so
the HTTP/write interval cannot overlap.

## Risks / Trade-offs

- **Access URL accidentally reaches telemetry or an exception** → never log
  request URLs, headers, provider bodies, or raw exception text; return a small
  fixed status vocabulary and test it.
- **Provider sends incomplete transaction data** → reject non-empty `errlist`
  and malformed settled rows before writes; leave freshness unchanged.
- **A provider response is valid but is for a different account** → require one
  response account and exact `(conn_id, account_id)` metadata matching.
- **A replay overlaps the five-day window** → rely on the existing
  `(account_id, external_id)` dedup key and test a second run.
- **A process dies while holding the lock** → PostgreSQL releases a session
  advisory lock when its dedicated connection closes; normal completion also
  releases it explicitly.
- **A valid Access URL revoked by the provider needs HTTP to detect** → return
  a sanitized `upstream_auth_failed` result after the non-2xx response; do not
  assert impossible preflight revocation detection.

## Migration Plan

1. Deploy code and the disabled-by-absence schedule definition; no migration is
   required because `finance_012` is already the receiving schema.
2. An owner provisions the claimed Access URL in `/secrets` as a System secret
   named `SIMPLEFIN_ACCESS_URL` with target `finance`. Until then, runs report
   `not_configured` and make no HTTP request.
3. The first valid one-account response creates the exact provider-bound
   Finance account; later runs require that binding.
4. Roll back by disabling/removing the TOML schedule and revoking/removing the
   Finance secret.  Existing aggregator ledger history remains intact.

## Open Questions

None. The v1 packet deliberately fixes one-account first-response provisioning,
exact metadata binding, settled-only ingestion, and bounded windows; generic
account onboarding, multiple accounts, or pagination belong to a later change.
