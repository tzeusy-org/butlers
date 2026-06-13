# google-health-secrets-surface

## Why

The `dashboard-google-accounts` spec already mandates a per-account scope-set picker (including `Google Health`) and a Google Health status card, but no spec binds them to a concrete route. The implemented surface — the `/secrets` passport's `PageGoogleAccounts` rendered at `/secrets?focus=u:google` — only becomes reachable when the inventory is projected to a `{google_account}` entity via a manual `?identity=<uuid>` lens. The owner-default `/secrets` view projects the `{owner}` entity (which has NO Google credential; OAuth refresh tokens live on separate `{google_account}` entities per `google-account-registry`), so there is currently **no discoverable path** for the owner to reach the scope-set picker and grant Google Health scopes.

A secondary safety gap: a naive owner-default Google-account join could expose non-primary accounts (e.g. a second person's `tzeuse@` token) in the owner view — a security regression. The spec must explicitly prohibit this.

## What Changes

- **MODIFIED capability `dashboard-google-accounts`:** Bind the scope-set picker and Google Health status card to the `/secrets` passport at route `/secrets?focus=u:google` (rendered as `PageGoogleAccounts`). The picker and card are NOT a standalone settings page. Add the normative `Scenario: multi-account leak prevention` requiring owner-default projection to surface ONLY the primary Google account's credential.

- **MODIFIED capability `butler-secrets`** (via the in-flight `redesign-secrets-passport` change's spec delta): REQUIRE the owner-default `/secrets` inventory to surface the owner's linked primary Google account(s) so that the scope-set picker (including `Google Health`) is reachable without any manual `?identity=` parameter. Extend `Scenario: Single-identity scope hides chip` to clarify that even when the owner-default inventory surfaces a linked Google account, non-primary accounts are excluded from that default projection.

- **NO new capabilities.** This change is a pure requirement amendment — no new spec files, no new endpoints, no new DB tables.

- **Cross-link to `add-connector-oauth-scope-surface`:** The systemic `auth_status` taxonomy (`ok | degraded | expired | rotation-needed`) and the durable reauth CTA endpoint are owned by that change. This change cross-links to it for harmonization but does NOT re-spec that enum.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `dashboard-google-accounts`: Add route-binding normative statement (picker + Health card rendered in `/secrets?focus=u:google`), and add `Scenario: multi-account leak prevention`.
- `butler-secrets`: Amend the owner-default inventory requirement so the primary Google account's credential is included in the baseline projection, making the scope-set picker reachable without manual `?identity=`.

## Impact

- **Affected specs:**
  - `openspec/specs/dashboard-google-accounts/spec.md` — amended with route-binding and leak-prevention scenario.
  - `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` — amended with owner-default Google account surfacing requirement.
- **Affected code (implementation, NOT in this change):** `src/butlers/api/routers/secrets_v2.py` — `_fetch_user_secrets` owner-default branch must include the primary Google account's `google_oauth_refresh` entity_info row (filtering `is_primary = true AND status != 'revoked'` to expose both active and expired accounts). This is owned by downstream bead `bu-2kejb`.
- **No new tables, endpoints, or migrations.** Pure spec clarification.
- **Doctrine alignment:** Single-owner model preserved (`about/heart-and-soul/security.md:7-8, 18-20`). Non-primary accounts remain owner-accessible under explicit `?identity=` lens (owner privilege unchanged). Leak-prevention scenario is additive — it does not contradict the `butler-secrets` identity-switcher semantics.
- **Cross-change dependency:** `add-connector-oauth-scope-surface` owns the reauth endpoint and `auth_status` enum. This change only specifies the discoverability path and leak-prevention gate. Both changes must harmonize on `auth_status` field names surfaced by `PageGoogleAccounts`.

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) — `about/heart-and-soul/vision.md:60-63`
- Security model — single-owner doctrine — `about/heart-and-soul/security.md:7-8, 18-20`
- `_fetch_user_secrets` owner-default join — `src/butlers/api/routers/secrets_v2.py:701-721`
- `{google_account}` entity model (companion entity, `roles = ['google_account']`) — `openspec/specs/google-account-registry/spec.md:37-41, 71-89`
- Scope-set picker + Health card requirements (to be route-bound) — `openspec/specs/dashboard-google-accounts/spec.md:63-119`
- In-flight `butler-secrets` identity-switcher semantics — `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md:84-105`
- Deep-link focus routing contract (`?focus=u:google`) — `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md:107-126`
- Systemic reauth taxonomy (cross-link, NOT re-specced here) — `openspec/changes/add-connector-oauth-scope-surface/proposal.md:43-72`
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
