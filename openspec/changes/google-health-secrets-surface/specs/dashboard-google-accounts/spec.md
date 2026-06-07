# dashboard-google-accounts

## ADDED Requirements

### Requirement: Google Health Scope Surface Route Binding

The per-account scope-set picker (§Per-Account Scope Set Picker) and the Google Health Connector Status Card (§Google Health Connector Status Card) SHALL be rendered INSIDE the `/secrets` passport at the route `/secrets?focus=u:google`, rendered as the `PageGoogleAccounts` page component. They are NOT a standalone settings page and SHALL NOT render as a full-page route outside the `/secrets` passport.

NOTE: The base `dashboard-google-accounts` spec (§Per-Account Scope Set Picker) uses the phrase "Google Accounts settings page" as a colloquial label for this surface. When this change is archived, that phrase SHALL be amended to "the `/secrets` passport at `/secrets?focus=u:google` (`PageGoogleAccounts`)" to eliminate the contradiction. The archive task in this change's tasks.md covers this reconciliation.

The `/secrets?focus=u:google` deep-link SHALL be the canonical, linkable surface for the owner to view connected Google accounts, grant scope sets (including `Google Health`), and inspect connector health. Any in-app cross-link to the Google account management surface (e.g. from `/ingestion/connectors`, from notification toasts, from the `/overview` page) SHALL target `/secrets?focus=u:google`.

For the systemic `auth_status` taxonomy (`ok | degraded | expired | rotation-needed`) and the durable reauth CTA endpoint, refer to the `add-connector-oauth-scope-surface` OpenSpec change. `PageGoogleAccounts` SHALL harmonize its `auth_status` field rendering with that change's contract when it archives.

#### Scenario: Scope-set picker is inside the /secrets passport

- **WHEN** the owner navigates to `/secrets?focus=u:google`
- **THEN** the `/secrets` passport renders `PageGoogleAccounts` in the right-page editorial area
- **AND** `PageGoogleAccounts` displays the per-account scope-set picker (one row per available scope set: at minimum `Calendar`, `Drive`, `Google Health`)
- **AND** `PageGoogleAccounts` displays the Google Health Connector Status Card when the primary account has `Google Health` scopes granted
- **AND** there is no separate settings page route that renders the same scope-set picker or Health status card outside the `/secrets` passport

#### Scenario: In-app cross-links target the passport route

- **WHEN** any dashboard page (e.g. `/ingestion/connectors`, `/overview`) renders a link or CTA directing the owner to manage Google account scopes or view Google Health connector status
- **THEN** that link SHALL href to `/secrets?focus=u:google`
- **AND** SHALL NOT href to any standalone settings route

### Requirement: Multi-Account Leak Prevention

The owner-default `/secrets` inventory projection SHALL surface ONLY the primary Google account's credential. Non-primary Google accounts SHALL NOT appear in the owner-default projection and SHALL be accessible ONLY under an explicit `?identity=<entity_id>` lens targeting that account's companion entity.

This requirement is a security invariant. It MUST hold regardless of how many Google accounts are connected or which account is designated primary at any given time.

#### Scenario: Owner-default inventory surfaces only the primary Google account

- **WHEN** `GET /api/secrets/inventory` is called without an `?identity=` parameter (owner-default projection)
- **AND** the system has two or more connected Google accounts (e.g. a primary `uniquosity@gmail.com` and a non-primary `tzeuse@gmail.com`)
- **THEN** the response SHALL include exactly one `google_oauth_refresh` entry in the `user` array
- **AND** that entry SHALL correspond to the primary account (`is_primary = true` on `public.google_accounts`)
- **AND** the non-primary account's `google_oauth_refresh` entry SHALL NOT appear in the response

#### Scenario: Non-primary account credential accessible under explicit identity lens

- **WHEN** `GET /api/secrets/inventory?identity=<non_primary_entity_id>` is called
- **AND** `<non_primary_entity_id>` is the companion entity ID of a non-primary Google account
- **THEN** the response SHALL include the `google_oauth_refresh` entry for that non-primary account
- **AND** the primary account's `google_oauth_refresh` entry SHALL NOT appear in this identity-scoped response

#### Scenario: Single Google account — no leak surface exists

- **WHEN** exactly one Google account is connected and it is primary
- **THEN** the owner-default inventory SHALL surface that account's `google_oauth_refresh` entry
- **AND** no `?identity=` parameter is needed to reach the scope-set picker at `/secrets?focus=u:google`

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) — `about/heart-and-soul/vision.md:60-63`
- Security model — single-owner doctrine — `about/heart-and-soul/security.md:7-8, 18-20`
- Deep-link focus routing contract (`?focus=u:google`) — `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md:107-126`
- `{google_account}` companion entity model — `openspec/specs/google-account-registry/spec.md:37-41, 71-89`
- Scope-set picker existing requirements — `openspec/specs/dashboard-google-accounts/spec.md:63-86`
- Google Health status card existing requirements — `openspec/specs/dashboard-google-accounts/spec.md:88-119`
- `_fetch_user_secrets` owner-default join — `src/butlers/api/routers/secrets_v2.py:701-721`
- Systemic auth_status taxonomy (cross-link, NOT re-specced here) — `openspec/changes/add-connector-oauth-scope-surface/proposal.md:43-72`
- Implementation bead for backend join — `bu-2kejb`
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
