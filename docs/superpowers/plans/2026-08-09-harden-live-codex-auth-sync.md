# Live Codex Auth Synchronization Implementation Plan

**Goal:** Make a dashboard-refreshed Codex credential effective for the next
daemon-side invocation without restarting a service or modifying a running
session.

**Boundary:** This change affects only Codex auth propagation. It does not
retry historical sessions, alter model failover/catalog behavior, restart
containers, mutate live secrets, or change Tailnet/Traefik ingress.

## Design

1. In schema topology, `CredentialStore.load()` is local-first but dashboard
   CLI auth is public/shared. Codex therefore explicitly reads and writes the
   shared pool when one exists; flat deployments retain local authority.
2. A preflight reconciles the authority to the canonical auth file before
   freshness checks, isolated HOME creation, speculative prewarm, or spawn.
   The file writer is same-directory atomic and always yields `0600`.
3. Preflight and the immediate pre-spawn revalidation return exact authority
   snapshots. Each completed operation finalizes once against the last
   captured snapshot in a shared-store compare-and-set. A newer dashboard
   refresh wins instead of being overwritten by an old session or prewarm.
4. A known local rotation is conditionally flushed before a new preflight
   reads/replaces it. Store failures preserve the working local file. A
   missing, malformed, or unavailable authority is never treated as an
   implicit insert permission after a runtime operation; dashboard auth is the
   explicit credential bootstrap path.
5. Dashboard post-login prewarm is bracketed by preflight/finalization so a
   prewarm rotation is persisted safely. Runtime and dashboard probe health
   results are fenced to the credential actually used; the dashboard probe
   reconciles and verifies its canonical `auth.json` before recording status,
   and conditionally finalizes a status-command rotation without attaching the
   old probe's health, history, or audit result.
   Its health, probe-log, and audit writes share the credential-row
   transaction; Passport and runtime value changes clear old health state
   atomically, and passport reads suppress retained history until the current
   credential is probed.
   Calendar quick-add supplies its known shared public credential store
   explicitly.
6. Scheduled and split-topology standalone direct dispatchers do not guess
   that a model or cursor pool is public; creating a lifecycle-managed shared
   authority for those callers is tracked in `bu-ih90b`.
7. A fresh process with no launch-bound local baseline conservatively restores
   shared authority rather than guessing an orphaned file rotation is valid;
   durable cross-process provenance is tracked in `bu-gg4fo`.
8. Credential-store loads and conditional writes have bounded best-effort
   waits. One invocation-wide allowance is shared by every Codex preflight,
   on-path prewarm, refresh-lock acquisition, pre-spawn revalidation, and
   finalization, and is declared outside the unchanged catalog provider
   timeout at both Spawner and direct-dispatch guards. A blocked authority
   path preserves local auth and cannot consume the session runtime budget.

## TDD Evidence

- RED: `uv run pytest tests/adapters/test_codex_auth_sync.py -q --override-ini='addopts='`
  failed at collection before `reconcile_codex_auth` existed.
- Focused GREEN during implementation:

  ```bash
  uv run pytest tests/adapters/test_codex_auth_sync.py tests/config/test_credential_store.py tests/adapters/test_codex_refresh_lock.py tests/daemon/test_startup_coverage_gaps.py tests/cli/test_cli_auth.py -q --override-ini='addopts='
  ```

## Required Regression Coverage

- shared authority bypasses a stale schema-local Codex row;
- changed, matching, missing, malformed, unavailable, and write-failure auth
  documents preserve safety and do not leak test tokens;
- local completed rotation flushes before preflight replacement;
- an older post-launch rotation cannot overwrite a dashboard refresh;
- unavailable or absent/revoked authority cannot recreate a shared credential
  or attach old health state;
- a late prewarm cannot overwrite a newer dashboard refresh;
- a retry persists its final auth rotation, and a stale refresh failure cannot
  mark a dashboard replacement failing;
- a dashboard replacement or winning rotation atomically clears old health
  fields regardless of row-lock ordering;
- a Passport token save uses that same atomic health reset, and a dashboard
  Codex probe reconciles the canonical file before it records only a
  still-current transactionally-associated result; retained prior probe
  history is not shown for the replacement, while a status-probe rotation is
  finalized through a B-bound CAS and yields to a concurrent C replacement;
- hung credential-store reads, writes, and local synchronization queueing
  fail open without preventing a local-auth Codex spawn or consuming its
  provider execution deadline in either Spawner or direct dispatch;
- on-path `login status` prewarm and refresh-lock contention share that same
  allowance, cannot consume the provider deadline, and clean up a cancelled
  prewarm subprocess;
- full-file fingerprint detects tail-only changes with unchanged mtime;
- speculative/on-path prewarm and dashboard post-login prewarm see reconciled
  auth and safely persist any resulting rotation;
- startup restore uses the atomic writer and `0600`.

## Final Verification

```bash
openspec validate harden-live-codex-auth-sync --strict
uv run ruff check src/butlers/credential_store.py src/butlers/cli_auth/persistence.py src/butlers/core/runtimes/base.py src/butlers/core/runtimes/_codex_auth_sync.py src/butlers/core/runtimes/codex.py src/butlers/core/spawner.py src/butlers/api/routers/cli_auth.py src/butlers/api/routers/secrets_v2.py src/butlers/connectors/discretion_dispatcher.py src/butlers/api/calendar/quick_add.py tests/config/test_credential_store.py tests/adapters/test_codex_auth_sync.py tests/adapters/test_codex_refresh_lock.py tests/adapters/test_codex_adapter.py tests/core/test_core_spawner.py tests/daemon/test_startup_coverage_gaps.py tests/cli/test_cli_auth.py tests/api/test_secrets_v2_cli_mutations.py tests/api/test_secrets_v2_inventory.py tests/api/test_secrets_v2_per_credential.py tests/connectors/test_discretion_dispatcher.py tests/api/test_calendar_quick_add.py
uv run ruff format --check src/butlers/credential_store.py src/butlers/cli_auth/persistence.py src/butlers/core/runtimes/base.py src/butlers/core/runtimes/_codex_auth_sync.py src/butlers/core/runtimes/codex.py src/butlers/core/spawner.py src/butlers/api/routers/cli_auth.py src/butlers/api/routers/secrets_v2.py src/butlers/connectors/discretion_dispatcher.py src/butlers/api/calendar/quick_add.py tests/config/test_credential_store.py tests/adapters/test_codex_auth_sync.py tests/adapters/test_codex_refresh_lock.py tests/adapters/test_codex_adapter.py tests/core/test_core_spawner.py tests/daemon/test_startup_coverage_gaps.py tests/cli/test_cli_auth.py tests/api/test_secrets_v2_cli_mutations.py tests/api/test_secrets_v2_inventory.py tests/api/test_secrets_v2_per_credential.py tests/connectors/test_discretion_dispatcher.py tests/api/test_calendar_quick_add.py
uv run pytest tests/adapters/test_codex_auth_sync.py tests/adapters/test_codex_adapter.py tests/adapters/test_codex_refresh_lock.py tests/config/test_credential_store.py tests/cli/test_cli_auth.py tests/api/test_secrets_v2_cli_mutations.py tests/api/test_secrets_v2_inventory.py tests/api/test_secrets_v2_per_credential.py tests/connectors/test_connector_codex_auth_restore.py tests/connectors/test_discretion_dispatcher.py tests/connectors/test_whatsapp_user_client.py tests/connectors/test_telegram_user_client.py tests/connectors/live_listener/test_connector_health_state.py tests/core/test_core_spawner.py tests/core/test_spawner_speculative_prewarm.py tests/daemon/test_startup_coverage_gaps.py tests/api/test_app_lifespan_supervision.py tests/api/test_calendar_quick_add.py -q --override-ini='addopts='
```
