# Google Health: Discoverable & Durable End-to-End — Epic Completion Report

**Date:** 2026-06-07
**Bead:** bu-as0vs (epic report, human-review deliverable)
**Epic:** bu-lmrzg (Google Health: discoverable & durable end-to-end)
**Spec change:** `openspec/changes/google-health-secrets-surface` (PR #2134)

---

## §0 — Executive Summary

Epic bu-lmrzg is **substantially complete**. The Google Health connector went from silently
crashing on every discovery cycle (ingestion zero since ~2026-05-26) to a fully wired,
owner-discoverable, end-to-end scope-grant flow backed by a passing integration test.

Five code PRs and one spec change closed six child beads. The system is now in a state where
an owner can open `/secrets`, see the primary Google account, grant Health scopes, and have
the connector report `healthy` — all without any manual `?identity=` parameter or
undiscoverable settings page.

**One remaining owner action:** bead bu-k5l35.6 (Google Cloud Console restricted-scope
verification) is **open at priority 4 (backlog)**. Production verification removes the 7-day
OAuth test-mode expiry, which is the root cause of ingestion lapsing. It is an external/async
action requiring owner engagement with Google — no code change is pending.

**One open harmonization:** the `add-connector-oauth-scope-surface` OpenSpec change owns the
systemic `auth_status` enum and durable reauth CTA (reauth endpoint currently 503-bricked).
When that change ships, the Google Health status card and associated FE wiring must be
re-evaluated for field-name alignment. This is a forward cross-link, not a gap.

---

## §1 — What Was Fixed and Shipped

### 1.1 Crash Fix — PR #2127 (bu-yggk4, commit 0d3f9e445)

**Root cause:** The connector runs as `python -m butlers.connectors.google_health` (module
loaded as `__main__`). Every poll cycle, `_resolve_owner_and_scopes` triggered a lazy import
`from butlers.connectors.google_health import GOOGLE_HEALTH_SCOPES`. Because the module was
already loaded as `__main__`, that import re-executed the module body under its real package
name, re-registering all Prometheus metrics and raising:

```
ValueError: Duplicated timeseries in CollectorRegistry: ...
```

The exception was swallowed as non-fatal, so the connector parked in `degraded` mode
indefinitely with zero ingestion.

**Fixes (defence-in-depth):**
1. Pass `GOOGLE_HEALTH_SCOPES` explicitly at the call site — the lazy import never fires.
2. Wrap module-level Prometheus metrics in a `_metric()` get-or-create helper
   (`src/butlers/connectors/google_health/__init__.py`) so any future re-execution reuses
   existing collectors rather than crashing.
3. Add a `docker-compose` healthcheck for the connector container (parity with sibling
   Google connectors), so a dead or hung connector is no longer silently `Up`.

**Tested by:** A reload-idempotency regression test and a scope-set pin assertion were added
in the same PR.

### 1.2 Discoverability Spec Delta — PR #2134 (bu-9i5uo)

**Problem:** The `/secrets` passport owner-default view projected the `{owner}` entity, which
holds no Google OAuth credential (refresh tokens live on separate `{google_account}` companion
entities per `google-account-registry`). There was no discoverable path to the scope-set
picker or Google Health status card without a manual `?identity=<uuid>` URL parameter.

**Spec change:** `openspec/changes/google-health-secrets-surface` amends two existing specs:

- `openspec/specs/dashboard-google-accounts/spec.md` — adds the normative route-binding
  statement (scope-set picker + Health card rendered at `/secrets?focus=u:google` in
  `PageGoogleAccounts`, not a standalone settings page) and the new
  `Scenario: multi-account leak prevention`.
- `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` — adds the
  requirement that the owner-default `/secrets` inventory MUST surface the primary Google
  account companion entity's credentials so the Health grant CTA is reachable without any
  explicit `?identity=` parameter.

**Normative filter:** the inventory join filters `status != 'revoked'` — active AND expired
accounts surface (so the reauth CTA is reachable for expired accounts); revoked accounts are
excluded.

**Leak-prevention guarantee:** the `Scenario: multi-account leak prevention` mandates that
ONLY the primary Google account appears in the owner-default projection. Non-primary accounts
(e.g. a secondary `tzeuse@` account) appear only under an explicit `?identity=<entity_id>`
lens.

### 1.3 Backend — PR #2137 (bu-2kejb)

**Change:** `_fetch_user_secrets` in `src/butlers/api/routers/secrets_v2.py` (lines 724–787)
extends the owner-default branch with a `UNION ALL` that joins
`public.google_accounts (is_primary = true AND status != 'revoked')` to include the primary
Google account companion entity's secured credentials (`google_oauth_refresh`) alongside the
owner entity's own credentials.

**Security invariant:** the `is_primary = true` guard in the `UNION ALL` clause ensures only
the designated primary account surfaces. The partial unique index
`ix_google_accounts_primary_singleton` (documented in
`src/butlers/google_account_registry.py:29–32`) enforces at most one `is_primary=true` row
table-wide at the DB level, so only that account's credentials can appear in the owner-default
view.

**UndefinedTableError fallback:** the owner-default branch catches `UndefinedTableError` and
falls back to the owner-only query, so the endpoint does not break on installations where the
`google_accounts` migration has not yet run.

### 1.4 Frontend — PR #2139 (bu-3gekd)

Three gaps filled after the backend (bu-2kejb) surfaced the primary Google account:

1. **Health grant URL:** `ScopeSetPicker.handleGrant` now calls `getGoogleOAuthStartUrl` with
   `scope_set=health`, `force_consent=true`, and `account_hint=<primary email>`, so the OAuth
   consent flow lands on the correct Google account
   (`frontend/src/components/secrets/passport/pages.tsx:477–485`).

2. **Owner-default spine discoverability:** `DirectionPassport.spineIdentityIds` is now
   computed as `inventory.identities.map(i => i.id)` when `identityParam === null`, so ALL
   returned identities (including the primary Google account companion entity) contribute spine
   entries in the owner-default view
   (`frontend/src/components/secrets/passport/DirectionPassport.tsx:113–116`).

3. **Empty-state connect CTA:** rendered when no Google account has been connected yet.

### 1.5 Frontend — PR #2140 (bu-hh875)

**Google Health status card:** added to the owner's Google credential page
(`ScopeSetPicker` / `PageGoogleAccounts`) sourced from
`GET /api/connectors/google-health/status`. The card is **hidden** when the primary account
has no health scopes granted (`hasHealthScopes(grantedScopes) === false` at
`pages.tsx:297–315`, used at `pages.tsx:716–818`).

**Test-mode expiry banner:** amber warning when the token is ≥ 6 days old (< 24 h remaining),
red `expired` banner when ≥ 7 days. Derived purely from existing signals: `status.test_mode`
and `status.last_token_refresh_at`. No new backend persistence required.

**Boundary:** the durable `expired`/`requires_reauth` status persistence, reauth CTA endpoint,
and systemic `auth_status` enum are owned by `add-connector-oauth-scope-surface`. This bead
delivers v1 health-specific warnings from existing signals only and is the designated
harmonization point when that change ships.

### 1.6 E2E Proof — PR #2142 (bu-fodms)

**File:** `tests/api/test_google_health_grant_flow.py`

Integration test proving the full server-side state transition (5 phases):

1. Inventory (no `?identity=`) surfaces the primary Google account's `google_oauth_refresh`
   credential.
2. OAuth start URL for `scope_set=health` contains all three `googlehealth.*` scopes.
3. Connector status reports `degraded` before the grant (no health scopes on account).
4. Mocked OAuth callback (`_exchange_code_for_tokens` + `_fetch_google_userinfo` patched) writes
   health scopes to `public.google_accounts.granted_scopes` via the real handler logic.
5. Connector status reports `healthy` after the grant.

**Mock scope:** external HTTPS calls and `asyncpg` pool are mocked hermetically. Real handler
logic exercised: OAuth state store (CSRF), `_update_account_refresh_token` write path,
`_derive_state` in the Google Health router, and all FastAPI route handlers (via
`httpx.AsyncClient + ASGITransport`).

### 1.7 Ops — bead bu-3qf5e (CLOSED)

Rebuilt `butlers-app:latest` from clean `origin/main` (including PR #2127) and recreated the
`connector-google-health` container. Verified: zero `Duplicated timeseries` warnings; account
discovery succeeds (primary `uniquosity@` resolved); container health reports `healthy` via the
new `compose` healthcheck; `/api/connectors/google-health/status` reachable (state=`degraded`,
correct — scopes not yet granted at time of ops close).

---

## §2 — Spec-Compliance Matrix

| Bead | PR | Spec section satisfied | Status |
|---|---|---|---|
| bu-yggk4 | #2127 | `openspec/specs/connector-google-health/spec.md` §Reliability — connector must survive transient errors and not park permanently degraded | CLOSED |
| bu-9i5uo | #2134 | `openspec/specs/dashboard-google-accounts/spec.md` — normative route-binding (picker + Health card at `/secrets?focus=u:google`); new `Scenario: multi-account leak prevention`; `openspec/changes/redesign-secrets-passport/.../butler-secrets/spec.md` — owner-default inventory MUST surface primary Google account | CLOSED |
| bu-2kejb | #2137 | `openspec/changes/google-health-secrets-surface` — owner-default inventory projection requirement (`status != 'revoked'` filter, `is_primary = true` security guard, `UNION ALL` implementation) | CLOSED |
| bu-3gekd | #2139 | `openspec/specs/dashboard-google-accounts/spec.md` — scope-set picker rendered in `/secrets?focus=u:google`; Health grant URL carries `scope_set=health + force_consent=true + account_hint`; owner-default spine discoverability without manual `?identity=` | CLOSED |
| bu-hh875 | #2140 | `openspec/specs/dashboard-google-accounts/spec.md` — Google Health status card from `GET /api/connectors/google-health/status`; card hidden when primary lacks health scopes; test-mode 7-day-expiry warning from existing signals | CLOSED |
| bu-fodms | #2142 | `openspec/changes/google-health-secrets-surface` §Acceptance — wiring-audit discipline: integration test proves inventory→grant URL→granted-state→connector status transition; no dead FE→BE chain | CLOSED |
| bu-3qf5e | (ops) | Production-stack liveness: crash fix live in container; healthcheck active; discovery clean | CLOSED |

---

## §3 — End-to-End Repro (Owner Steps)

The following numbered steps describe the complete owner flow from first open to connector
reporting healthy. Steps marked **[TEST]** are covered by `test_google_health_grant_flow.py`.
Steps marked **[LIVE]** require live verification against the running stack.

1. **[LIVE]** Open `/secrets` with no URL parameters (owner-default projection).
   — The spine now includes the primary Google account (`u:google`) because
   `DirectionPassport` passes all returned identities to `buildSpineEntries` when
   `identityParam === null`.

2. **[LIVE]** Click the `u:google` spine entry (or navigate to `/secrets?focus=u:google`).
   — `PageGoogleAccounts` renders. The `ScopeSetPicker` shows Calendar, Drive, and Health
   tiles. Health shows `Grant` (not yet granted).
   — If no Google account is connected yet, the empty-state CTA is shown instead.

3. **[TEST, step 1]** Inventory endpoint `GET /api/secrets/inventory` (no `?identity=`) returns
   `google_oauth_refresh` in the `user[]` array.
   — Proved by Phase 1 of `test_google_health_grant_flow_scope_transition`.

4. **[TEST, step 2]** Click `Grant` on the Health tile.
   — `handleGrant("health")` calls `getGoogleOAuthStartUrl({scopeSet: "health", forceConsent: true, accountHint: <primary email>})`.
   — The resulting URL sent to `window.location.assign` contains `scope_set=health`,
   `force_consent=true`, and `account_hint=<primary email>`.
   — Proved by Phase 2 of the integration test: all three `googlehealth.*` scopes appear in
   the `authorization_url`.

5. **[LIVE]** Browser redirects to Google's consent page, pre-selected on the primary account.
   Owner grants the three `googlehealth.*` scopes.

6. **[TEST, steps 3–4]** Google redirects back to `/api/oauth/google/callback?code=...&state=...`.
   — The real callback handler validates CSRF state, calls (mocked in test, live in prod)
   `_exchange_code_for_tokens` and `_fetch_google_userinfo`, then calls
   `_update_account_refresh_token` which UPDATEs `public.google_accounts.granted_scopes` with
   all three health scope URLs.
   — Proved by Phase 4 of the integration test: `state.ga_scopes_updated is True` and
   `GOOGLE_HEALTH_SCOPE_URLS ⊆ state.ga_granted_scopes`.

7. **[LIVE]** Browser lands on `/secrets?toast=connected` (or the page of origin).
   The `?toast=connected` param triggers a toast notification and is stripped from the URL.

8. **[TEST, step 5]** `GET /api/connectors/google-health/status` now returns `state: "healthy"`.
   — Before the grant: `state: "degraded"` (proved by Phase 3).
   — After the grant: `state: "healthy"` (proved by Phase 5, with an active heartbeat row in
   the switchboard pool mock).
   — `scopes_granted` in the response is a superset of `GOOGLE_HEALTH_SCOPE_URLS`.

9. **[LIVE]** The Google Health status card (previously hidden) is now visible on
   `PageGoogleAccounts`, showing connection state, `last_ingest_at`, and 7-day counts.

10. **[LIVE]** The connector polls `https://health.googleapis.com/v4/` on its next cycle and
    emits `ingest.v1` envelopes into the `wellness/google_health` channel.

---

## §4 — Test-Mode / Durability Posture and Boundary

`googlehealth.*` are Google RESTRICTED scopes. In OAuth test mode (the current state), refresh
tokens expire every 7 days. This is the systemic cause of ingestion lapsing ~2026-05-26.

**V1 signals (this epic, bu-hh875):** The test-mode expiry warning banner in `PageGoogleAccounts`
is derived from `status.test_mode` and `status.last_token_refresh_at` (thresholds: amber ≥ 6 days,
red ≥ 7 days). It requires no new backend persistence and no new spec.

**What this epic does NOT own:**
- The durable `expired` / `requires_reauth` status persistence.
- The systemic `auth_status` enum (`ok | degraded | expired | rotation-needed`).
- The reauth CTA endpoint (`POST /api/ingestion/connectors/{type}/{identity}/reauth`), which
  currently returns HTTP 503 with `reason = "reauth_spec_pending"`.

These are owned by the active OpenSpec change `add-connector-oauth-scope-surface`
(`openspec/changes/add-connector-oauth-scope-surface/`). When that change archives, the
Google Health status card and the `PageGoogleAccounts` FE wiring must be evaluated for
`auth_status` field-name alignment. The cross-link is explicit in both proposals
(`openspec/changes/google-health-secrets-surface/design.md:67`).

---

## §5 — Remaining / Owner Actions

### 5.1 OPEN: bu-k5l35.6 — Submit Restricted-scope verification package to Google Cloud Console

**Status:** Open, priority 4 (backlog). **This is an external/owner action.**

Google requires an approved privacy and security verification package for OAuth clients
requesting `googlehealth.*` RESTRICTED scopes. Without it, the OAuth app remains in test mode
and refresh tokens expire every 7 days, requiring the owner to re-grant consent weekly.

**Owner steps (when ready):**
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → OAuth consent screen.
2. Complete the verification questionnaire and submit the privacy policy + security assessment.
3. Monitor Google's response (typically 4–6 weeks for restricted scopes).
4. Once approved, existing tokens will refresh with long-lived TTLs and the test-mode banner
   (bu-hh875) will no longer trigger.

**Note (2026-04-25 decision):** Owner explicitly decided to remain in test mode. The 7-day TTL
is acceptable for a single-owner deployment. This bead is parked as backlog; do not pursue
until the owner revisits.

### 5.2 FORWARD: Harmonize with `add-connector-oauth-scope-surface`

When the `add-connector-oauth-scope-surface` OpenSpec change ships (reauth endpoint unblocked,
`auth_status` enum ratified), the following must be evaluated:

- Does `PageGoogleAccounts` need a `status.auth_status` field wired into the Health card in
  place of or alongside the current `state` / `test_mode` signals?
- Does the test-mode expiry banner (bu-hh875) remain correct or does it get superseded by
  the `requires_reauth` state from the new enum?

No action is required today. The forward dependency is tracked in
`openspec/changes/google-health-secrets-surface/design.md` and
`openspec/changes/add-connector-oauth-scope-surface/proposal.md`.

---

## §6 — Quality Notes

This is a documentation-only bead. No product code was changed. No test runs were performed.
Verification methodology:

- Git log and `git show` on each PR commit to confirm commit message, stat, and bead reference.
- Read of `src/butlers/api/routers/secrets_v2.py:681–800` to verify `UNION ALL` shape,
  `is_primary = true AND status != 'revoked'` guard, and `UndefinedTableError` fallback.
- Read of `src/butlers/google_account_registry.py:29–32` to verify
  `ix_google_accounts_primary_singleton` index documentation.
- Read of `frontend/src/components/secrets/passport/pages.tsx` (lines 297–320, 460–490,
  570–585, 621–818) to verify `hasHealthScopes`, `handleGrant` `account_hint` wiring, and
  `TestModeExpiryBanner` threshold logic.
- Read of `frontend/src/components/secrets/passport/DirectionPassport.tsx:108–125` to verify
  owner-default `spineIdentityIds = inventory.identities.map(i => i.id)`.
- Read of `tests/api/test_google_health_grant_flow.py` (all 497 lines) to verify the 5-phase
  acceptance structure matches the claimed bead scope.
- `bd show` queries on bu-yggk4, bu-9i5uo, bu-2kejb, bu-3gekd, bu-hh875, bu-fodms, bu-3qf5e,
  and bu-k5l35.6 to confirm closed/open status and close reasons.
- Read of `openspec/changes/google-health-secrets-surface/proposal.md` and `design.md` to
  confirm `status != 'revoked'` normative filter and cross-link to `add-connector-oauth-scope-surface`.

---

*Report generated by bu-as0vs — epic completion report worker.*
