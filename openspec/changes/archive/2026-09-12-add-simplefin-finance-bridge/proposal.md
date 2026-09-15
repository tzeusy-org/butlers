## Why

Finance can reconcile email receipts only after an authoritative bank-feed row
exists.  The receiving schema and reconciliation surface already support an
aggregator feed, but no bounded, deterministic feed bridge currently supplies
those rows or advances account freshness truthfully.

## What Changes

- Add a Finance-owned SimpleFIN Bridge v2 scheduled job for exactly one bound
  remote account.
- Resolve the claimed Access URL from Finance's DB-backed credential store only;
  treat absent, malformed, revoked, incomplete, or failed upstream state as a
  sanitized no-write result.
- Create one exact provider-bound Finance account from the first fully
  validated one-account response, then record only settled transactions through
  the established Finance transaction path with `source="aggregator"`, stable
  provider IDs, and non-secret provenance.
- Advance `accounts.last_synced_at` only after a complete response has been
  validated and replayed safely; serialize overlapping sync attempts.
- Document owner setup, scheduled behavior, degraded mode, and the explicit v1
  limits without exposing any credential material.

## Capabilities

### New Capabilities

- `finance-simplefin-bridge`: Finance-owned, one-account SimpleFIN Bridge v2
  synchronization, including credential safety, account binding, response
  validation, idempotent settled-transaction recording, freshness semantics,
  and single-run concurrency control.

### Modified Capabilities

- `butler-finance`: Add the deterministic `simplefin-sync` schedule to the
  Finance scheduler inventory without changing its public MCP tool surface or
  direct-notify policy.

## Impact

- Affects Finance jobs, the internal transaction-recording seam, Finance's TOML
  scheduler configuration, the deterministic scheduler registry, focused tests,
  and `docs/butlers/finance.md`.
- Uses the existing Finance account registry,
  `finance.transactions.source='aggregator'`, account `last_synced_at`, and
  `(account_id, external_id)` deduplication support; no migration, new connector
  process, Switchboard routing, LLM session, or new dependency is introduced.
