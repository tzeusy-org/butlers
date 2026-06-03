# secrets passport parity restoration — human-review report

**Date:** 2026-06-04
**Epic:** bu-ayp6v (secrets passport parity)
**Author:** bu-ayp6v.14 (human-review report worker)
**Status:** ready for owner sign-off

---

## 1. Executive Summary

When the passport-book redesign landed on 2026-05-25–26 (18 PRs, RPT-1), the
`/secrets` surface shipped the new visual design — spine + page IA, passport-book
layout, Dispatch typography, evidence-over-value affordances — but was **not yet
functional**. Two critical gaps made it a read-only dossier:

1. **No live data.** `DirectionPassport` read from a static `MOCK_INVENTORY`
   fixture; the fully-implemented `/api/secrets/inventory` backend went uncalled.
2. **No reauthorize action.** Every OAuth commit-pill button on `PageUser` had no
   `onClick` handler; the backend endpoint (`POST /api/secrets/user/<p>/reauthorize`)
   existed but was unreachable from the UI.

The epic `bu-ayp6v` was opened specifically to restore full mutation parity —
**without abandoning the passport design.** Thirteen child beads over two weeks
restored all 55 capabilities of the original 3-tab surface, wired every commit-pill
to a live backend endpoint, and verified the result with 58 Playwright E2E tests.
Zero dead controls remain.

**Recommendation: the epic is ready for owner sign-off and closure.**

---

## 2. What Was Lost → What Each Bead Restored

The passport's initial state (RPT-1, 2026-05-26) left these gaps relative to the
old 3-tab shell. The table below maps each gap to the bead that closed it.

### Critical gaps (page non-functional)

| Gap (what was missing post-RPT-1) | Restoring bead | PR |
|---|---|---|
| API client layer for all mutation endpoints (no `client.ts` calls) | **bu-ayp6v.1** | #2093 |
| TanStack Query mutation hooks (`useRotateUserSecret`, `useProbeSystemSecret`, etc.) | **bu-ayp6v.2** | #2094 |
| PageUser commit actions wired (reauthorize, probe, rotate, disconnect) | **bu-ayp6v.3** | #2095 |
| PageSystem commit actions wired (set/rotate, override, probe, reveal, delete) | **bu-ayp6v.4** | #2096 |
| PageCli commit actions + Claude api-key management | **bu-ayp6v.5** | #2097 |
| PassportAddPanel — credential add entry point (system / user / provider) | **bu-ayp6v.6** | #2098 |
| Google multi-account management (per-account reauth / set-primary / disconnect / scope grants) | **bu-ayp6v.7** | #2099 |
| Home Assistant, OwnTracks, Steam provider-config drawers | **bu-ayp6v.8** | #2100 |
| Spotify, WhatsApp provider-config drawers | **bu-ayp6v.9** | #2101 |
| CLI reauthorize backend bridge (`POST /api/secrets/cli/<id>/reauthorize`) | **bu-ayp6v.10** | #2102 |
| Live probes — Home Assistant token verify, Steam API key verify, OwnTracks HMAC format verify | **bu-ayp6v.11** | #2103 |
| Audit `?key=` deep-link filter backend (already on main when epic began) | **bu-ayp6v.12** | — |
| E2E parity verification (58 Playwright tests, all 55 capabilities + dead-control assertions) | **bu-ayp6v.13** | #2105 |
| Gen-1 terminal reconciliation (55/55 parity + zero dead controls audit) | **bu-ayp6v.15** | #2104 |
| Live inventory fetch wired (D1: MOCK_INVENTORY → live endpoint) | **bu-nrgk9** | #1981 |
| Reauthorize button wired (D2: dead button → `POST .../reauthorize`) | **bu-f1loa** | #1980 |

---

## 3. Before / After Capability Table

### Before (post-RPT-1, 2026-05-26)

The passport had shipped with the new IA but in a degraded state:

| Capability family | Old 3-tab surface | Passport state at RPT-1 |
|---|---|---|
| View credential inventory | SecretsTable (3 tabs) | Rendered from MOCK_INVENTORY — non-live |
| Evidence affordances (fingerprint, scopes, audit) | `••••••••` + eye-toggle only | Rendered, but static fixture data |
| OAuth reauthorize | Settings OAuth cards | Commit-pill rendered, no onClick — **dead button** |
| System secret set/rotate | SecretsTable row-edit | Commit-pill rendered, no hook — **dead button** |
| CLI device-code connect | CLIAuthCard | Commit-pill rendered, no hook — **dead button** |
| Google multi-account management | Settings Google card | Not present in passport |
| Provider drawers (HA, OwnTracks, Steam, Spotify, WhatsApp) | Settings bespoke cards | Not present in passport |
| Add credential | Implicit via Settings | Not present in passport |
| Live probes | None | Not present |
| E2E coverage | None | None |

### After (2026-06-04 — all bu-ayp6v child beads merged)

| # | Capability | Status | Evidence |
|---|---|---|---|
| C01 | Spine inventory (all credential families, grouped, sorted) | **SHIPPED** | `Spine.tsx`, `spine-builder.ts` |
| C02 | Needs-hand pinned group (severity-first) | **SHIPPED** | `secrets-fe5.test.tsx` (6 tests) |
| C03 | Spine sort: severity / recency / alpha | **SHIPPED** | `DirectionPassport.tsx:154` |
| C04 | Spine search/filter by label | **SHIPPED** | `Spine.tsx` |
| C05 | Identity switcher (projection-lens, owner-privileged) | **SHIPPED** | `IdentityChip.tsx:55` |
| C06 | `?focus=<key>` deep-link (u:/s:/c:) | **SHIPPED** | `constants.ts:parseFocus` |
| C07 | `?sort=<mode>` URL state | **SHIPPED** | `DirectionPassport.tsx:129` |
| C08 | `?identity=<id>` URL state | **SHIPPED** | `DirectionPassport.tsx:103` |
| C09 | Live inventory from `GET /api/secrets/inventory` | **SHIPPED** | `use-secrets-inventory.ts:453` (bu-nrgk9) |
| C10 | PageUser — credential evidence (fingerprint, KV, scopes, WhatBreaks, probe, audit) | **SHIPPED** | `pages.tsx:773–1181` |
| C11 | PageUser — re-authorize → OAuth redirect | **SHIPPED** | `pages.tsx:681–695`, `client.ts:5223` (bu-f1loa) |
| C12 | PageUser — connect (never_set → reauthorize) | **SHIPPED** | `pages.tsx:1136–1144` |
| C13 | PageUser — probe | **SHIPPED** | `use-secrets-mutations.ts:117` (bu-ayp6v.3) |
| C14 | PageUser — rotate | **SHIPPED** | `use-secrets-mutations.ts:67` (bu-ayp6v.3) |
| C15 | PageUser — disconnect | **SHIPPED** | `use-secrets-mutations.ts:93` (bu-ayp6v.3) |
| C16 | PageSystem — credential evidence | **SHIPPED** | `pages.tsx:1196–1785` (bu-ayp6v.4) |
| C17 | PageSystem — set value / rotate | **SHIPPED** | `use-secrets-mutations.ts:150` (bu-ayp6v.4) |
| C18 | PageSystem — override per butler | **SHIPPED** | `pages.tsx:1279–1289` (bu-ayp6v.4) |
| C19 | PageSystem — probe + 429 rate-limit hint | **SHIPPED** | `use-secrets-mutations.ts:172` (bu-ayp6v.4) |
| C20 | PageSystem — reveal value | **SHIPPED** | `use-secrets-mutations.ts:278` (bu-ayp6v.4) |
| C21 | PageSystem — delete / remove override | **SHIPPED** | `use-secrets-mutations.ts:193` (bu-ayp6v.4) |
| C22 | PageCli — credential evidence | **SHIPPED** | `pages.tsx:1906–2426` (bu-ayp6v.5) |
| C23 | PageCli — rotate (copy-once panel) | **SHIPPED** | `use-secrets-mutations.ts:219` (bu-ayp6v.5) |
| C24 | PageCli — revoke (danger confirm) | **SHIPPED** | `use-secrets-mutations.ts:235` (bu-ayp6v.5) |
| C25 | PageCli — reveal token | **SHIPPED** | `pages.tsx:1984–1994` (bu-ayp6v.5) |
| C26 | PageCli — test | **SHIPPED** | `pages.tsx:2003–2013` (bu-ayp6v.5) |
| C27 | PageCli — device-code connect / re-auth | **SHIPPED** | `use-cli-auth.ts:157` (bu-ayp6v.5) |
| C28 | PageCli — api-key save / update / delete | **SHIPPED** | `use-cli-auth.ts:81` (bu-ayp6v.5) |
| C29 | Google multi-account (reauth / set-primary / disconnect) | **SHIPPED** | `pages.tsx:290–430` (bu-ayp6v.7) |
| C30 | Google add-another-account (forceConsent + selectAccount) | **SHIPPED** | `pages.tsx:551–557` (bu-ayp6v.7) |
| C31 | Google scope-set picker + Health selective revoke | **SHIPPED** | `pages.tsx:439–528` (bu-ayp6v.7) |
| C32 | Home Assistant drawer (configure + disconnect) | **SHIPPED** | `ProviderConfigDrawer.tsx` (bu-ayp6v.8) |
| C33 | OwnTracks drawer (generate token, copy webhook URL) | **SHIPPED** | `ProviderConfigDrawer.tsx` (bu-ayp6v.8) |
| C34 | Steam drawer (connect SteamID / API key, disconnect) | **SHIPPED** | `ProviderConfigDrawer.tsx` (bu-ayp6v.8) |
| C35 | Spotify drawer (configure client_id, OAuth PKCE, disconnect) | **SHIPPED** | `ProviderConfigDrawer.tsx` (bu-ayp6v.9) |
| C36 | WhatsApp drawer (QR pairing start/poll/cancel, disconnect) | **SHIPPED** | `ProviderConfigDrawer.tsx` (bu-ayp6v.9) |
| C37 | PassportAddPanel — family chooser | **SHIPPED** | `pages.tsx:2488–3001` (bu-ayp6v.6) |
| C38 | PassportAddPanel — system secret creation | **SHIPPED** | `pages.tsx:2520–2548` (bu-ayp6v.6) |
| C39 | PassportAddPanel — user credential creation | **SHIPPED** | `use-secrets-mutations.ts:295` (bu-ayp6v.6) |
| C40 | PassportAddPanel — connect provider | **SHIPPED** | `pages.tsx:2607–2980` (bu-ayp6v.6) |
| C41 | Live probe — Google OAuth token exchange + userinfo | **SHIPPED** | `secrets_v2.py:_OAUTH_VERIFY_PROVIDERS["google"]` |
| C42 | Live probe — Home Assistant long-lived token | **SHIPPED** | `secrets_v2.py:_verify_home_assistant_credential` (bu-ayp6v.11) |
| C43 | Live probe — Steam API key verify | **SHIPPED** | `secrets_v2.py:_verify_steam_credential` (bu-ayp6v.11) |
| C44 | WhatBreaks catalogue render | **SHIPPED** | `secrets_v2.py:1526`, `WhatBreaks.tsx` |
| C45 | Audit history inline + `/audit-log` deep-link | **SHIPPED** | `secrets_v2.py:1387`, `pages.tsx:954` |
| C46 | Audit deep-link `?key=<canonical-key>` filter | **SHIPPED** | `audit.py:220–254`, `credential_keys.py` |
| C47 | Cross-page reauth: page_of_origin through OAuth state | **SHIPPED** | `oauth.py:458–477` |
| C48 | `?toast=connected` + `?oauth_error=<e>` landing bookkeeping | **SHIPPED** | `SecretsPage.tsx:37–57` (bu-f1loa/bu-nrgk9) |
| C49 | CLI reauthorize backend bridge | **SHIPPED** | `secrets_v2.py:3585–3719` (bu-ayp6v.10) |
| C50 | State color / severity visual hierarchy | **SHIPPED** | `StateLabel.tsx:36–41`, `Sliver.tsx` |
| C51 | Fingerprint replaces masked-value blob | **SHIPPED** | `secrets_v2.py:347–357`, `FingerprintRow.tsx` |
| C52 | + verify cmd expander (hard-coded shell literal) | **SHIPPED** | `FingerprintRow.tsx` |
| C53 | No-LLM-Narration invariant (ESLint rule) | **SHIPPED** | `frontend/eslint.config.js:35–56` |
| C54 | Inventory + per-credential GET reads fully wired | **SHIPPED** | `secrets_v2.py:922,1252,1292,1321` |
| C55 | Live probe — OwnTracks HMAC token format verify | **SHIPPED** | `secrets_v2.py:_verify_owntracks_token_format` (bu-ayp6v.11) |

**Summary: 55/55 capabilities shipped. 0 dead controls.**

Source: `docs/reports/secrets-parity-reconciliation-2026-06-04.md §1` (bu-ayp6v.15
terminal reconciliation audit — static analysis of all 64 PillBtns in `pages.tsx`
and 33 PillBtns in `ProviderConfigDrawer.tsx`).

---

## 4. E2E Evidence

**Source:** `docs/reports/secrets-parity-e2e-2026-06-04.md`
**Spec file:** `frontend/tests/e2e/secrets-passport-parity.spec.ts`

| Metric | Value |
|---|---|
| Total capabilities covered | 55 / 55 |
| Total tests | 58 |
| Passing | 58 |
| Failing | 0 |
| Dead-control assertions | 3 (one per page type: user / system / CLI) |
| Error-path tests | 3 (C17 set-value error, C15 disconnect error, C24 revoke error) |

The E2E suite uses route-mocked Playwright (no real backend required). Every
commit-pill button is asserted to trigger an observable UI response:

- **Re-authorize:** redirecting state appears on expired/never-set credential pages
- **Set value / rotate:** panel opens and closes on success
- **Probe:** test-result UI appears
- **Disconnect / revoke / delete:** confirm-panel fires then mutation completes
- **Device-code connect:** auth panel with device code appears; cancel hides it
- **Reveal:** revealed-value panel appears

Three explicit sweep tests confirm no page type contains a silent (no-op) commit-pill:

1. `every commit-pill on user page triggers a visible response` — PASS
2. `every commit-pill on system page triggers a visible response` — PASS
3. `every commit-pill on CLI page (missing) triggers a visible response` — PASS

The suite runs in CI. All 58 tests are green.

---

## 5. Intentionally Descoped / Deferred Items

The following items are tracked as follow-up beads. None block the epic's parity
claim; each is an incremental improvement.

| Bead | Title | Priority | Rationale for deferral |
|---|---|---|---|
| **bu-xfq4r** | Spotify live probe | P4 | Spotify probe falls back to local-state check (credential exists = ok). Live probes for HA, Steam, and OwnTracks shipped in bu-ayp6v.11. Spotify PKCE token verification via `/me` is a follow-on improvement, not a parity gap. |
| **bu-3wg2l** | Wire frontend C10-bridge re-auth button to `/api/secrets/cli/<id>/reauthorize` | P2 | Backend endpoint fully shipped (bu-ayp6v.10). Frontend `PageCli` currently uses `deviceAuth.start` directly for both initial connect and re-auth (documented C10-BRIDGE comment at `pages.tsx:2324`). Functionally correct for v1; the backend bridge adds a richer re-auth flow when wired. |
| **bu-zpivp** | Wire `?key=` deep-link into AuditLogPage frontend rendering | P3 | Backend filter is fully wired (`audit.py:220–254`). The `ActionArrow` in `pages.tsx:954` emits the correct `/audit-log?key=u:<provider>` link. Frontend `AuditLogPage` does not yet consume the `?key=` param to pre-filter the displayed list. |
| **bu-vzwnl** | Guard PassportAddPanel `ownerEntityId` sentinel (G3) | P3 | The `"owner"` fallback string at `pages.tsx:2612` is technically reachable if inventory loads with no owner identity. The backend rejects non-UUID identity values with a 422. True risk is low (the add-panel is only rendered after a successful inventory fetch which must include an owner). The fix is a one-line guard: `if (!ownerEntityId) return`. |
| **bu-6hmny** | Hide probe for `plainValue` system credentials (G2) | P3 | System credentials with `plainValue=true` (boolean flags, enum values) have no meaningful live probe. The test button is currently shown and will return an existence-based `ok=true`. Fix: add `probeDisabled` field to `SystemCredential` and conditionally suppress the test button. |

---

## 6. Sign-Off

This section is the owner gate for epic bu-ayp6v.

### Assertion

All 55 parity capabilities from the original 3-tab `/secrets` surface (SecretsTable
+ SystemSecretsSection + CLIAuthCard + six bespoke provider Setup cards) have been
superseded by the passport-book design and are:

- **Implemented** with live backend wiring (hooks → client functions → real API
  endpoints), verified by static analysis (bu-ayp6v.15 terminal reconciliation)
- **Tested** with 58 passing Playwright E2E tests (bu-ayp6v.13), including explicit
  dead-control sweep assertions for all three page types
- **Lint-guarded** by an ESLint `no-restricted-imports` rule that enforces the
  No-LLM-Narration invariant on all `/secrets` surfaces (CI-enforced)
- **Zero dead controls** — no commit-pill button anywhere on the passport has a
  missing or no-op `onClick` handler

The five deferred items listed in §5 are non-blocking polish: three are P3/P4
follow-up improvements (live Spotify probe, audit deep-link frontend rendering,
plainValue probe suppression), one is a P2 frontend-wiring upgrade (C10-bridge
re-auth, functionally correct today via `deviceAuth.start`), and one is a P3
defensive guard (ownerEntityId sentinel).

### Recommendation

**Epic bu-ayp6v is ready for owner sign-off and closure.**

The passport-book `/secrets` surface is fully live, fully wired, and fully
verified. The redesign brief's intent — replace the read-only dossier with full
mutation parity while preserving the passport design — has been met.

---

*Report generated by bu-ayp6v.14 — human-review sign-off worker.*
