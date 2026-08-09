## Why

Refreshing Codex through the dashboard updates the shared `cli-auth/codex`
credential, but a running daemon can retain a separate stale local
`~/.codex/auth.json` until it restarts. A subsequent session can therefore
fail with a revoked refresh token and consume same-tier failover candidates
despite a valid replacement credential already being stored.

## What Changes

- Reconcile the database-backed Codex device-auth file immediately before a
  new Codex subprocess launches when its adapter is supplied the shared
  credential authority, so a dashboard refresh takes effect on the next
  invocation without a daemon restart.
- Make shared/public `cli-auth/codex` authoritative in schema topology so a
  stale per-butler row cannot shadow a dashboard refresh.
- Make changed auth-file writes atomic and mode-restricted, serialize local
  reconciliation, and use a credential-store compare-and-set so an old
  completed session cannot overwrite a newer dashboard refresh.
- Treat unavailable, malformed, and absent authority as non-writable for a
  completed runtime operation, so a revoked credential cannot be recreated
  from a stale local auth file.
- Finalize both speculative and on-path prewarm with the authority snapshot
  captured before the operation, including Codex rotations caused by the
  dashboard's own post-login prewarm.
- Let direct dispatchers accept an explicit authority, wire calendar quick-add's
  known shared API pool, and fence runtime auth-health writes to that same
  credential value. Dashboard Codex probes reconcile the canonical file they
  actually test and fence their health, probe-log, and audit result to those
  bytes in one credential-row transaction; Passport value changes reset prior
  health atomically whenever credential bytes change.
- Bound credential-authority synchronization as a short best-effort operation
  so a blocked pool checkout, row lock, or local sync queue cannot consume the
  session runtime budget or prevent a locally authenticated Codex invocation.
  One invocation-wide allowance (including on-path prewarm and refresh-lock
  acquisition) is explicitly outside the provider execution deadline in both
  Spawner and credential-wired direct-dispatch paths.
- Finalize an auth rotation caused by the dashboard's Codex status probe using
  the probe's captured authority snapshot, while withholding that probe's
  health/history/audit result and allowing a concurrent dashboard refresh to
  win the conditional write.
- Add focused behavior tests for shared authority, stale writers, changed,
  unchanged, malformed, failing, and concurrent reconciliation paths.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `core-credentials`: CLI device-auth credentials become live runtime inputs
  for subsequent Codex launches, not startup-only restored files.
- `core-spawner`: a credential-wired Codex adapter invocation reconciles its
  device-auth file before subprocess launch while retaining environment
  isolation and existing session/failover semantics.

## Impact

- Affected code: `src/butlers/credential_store.py`,
  `src/butlers/cli_auth/persistence.py`,
  `src/butlers/core/runtimes/base.py`,
  `src/butlers/core/runtimes/_codex_auth_sync.py`,
  `src/butlers/core/runtimes/codex.py`, `src/butlers/core/spawner.py`, the
  dashboard CLI-auth callback and probe endpoint, and the Passport CLI
  rotation endpoint.
- Affected direct-dispatch integration:
  `src/butlers/connectors/discretion_dispatcher.py` plus calendar quick-add's
  known public credential pool.
- Affected tests: focused adapter, Spawner/direct-dispatch timeout, CLI-auth
  persistence, and Passport inventory/detail coverage.
- No migration, new dependency, service restart, credential value exposure,
  historical-session retry, model-catalog change, or ingress change.
- Scheduled and split-topology standalone discretion callers without an
  injected shared credential authority are tracked separately in `bu-ih90b`
  rather than being allowed to infer that a local/cursor pool is public.
