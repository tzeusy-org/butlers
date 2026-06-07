# butler-secrets

## ADDED Requirements

### Requirement: Owner-Default Inventory Surfaces Primary Google Account

The owner-default `/secrets` inventory (`GET /api/secrets/inventory` without `?identity=`) SHALL include the primary Google account's `google_oauth_refresh` credential entry in the `user` array. This entry SHALL be present whenever at least one Google account with `is_primary = true` and `status != 'revoked'` exists in `public.google_accounts`. This includes `status = 'expired'` accounts so the owner can reach the scope-set picker and reauth CTA without needing a manual `?identity=` parameter.

Including the primary Google account credential in the owner-default inventory makes the scope-set picker (including `Google Health`) reachable at `/secrets?focus=u:google` WITHOUT requiring the owner to first discover or manually specify an `?identity=<entity_id>` parameter.

The behavioral outcome MUST be that `u:google` appears in the spine and `PageGoogleAccounts` is reachable from the owner-default `/secrets` view, regardless of whether the primary account's status is `active` or `expired`. The backend join strategy (how to resolve the credential from `public.google_accounts` and `public.entity_info`) is an implementation detail owned by bead `bu-2kejb`.

This requirement is **co-owned** with the `dashboard-google-accounts` spec (§Multi-Account Leak Prevention), which binds the leak-prevention invariant: the owner-default projection SHALL surface ONLY the primary account's credential. Implementation is owned by bead `bu-2kejb`.

#### Scenario: Owner-default inventory includes primary Google account credential

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** a primary active Google account exists in `public.google_accounts`
- **THEN** the response `user` array SHALL contain a `google_oauth_refresh` entry corresponding to the primary account's companion entity
- **AND** the spine at `/secrets` SHALL render a `u:google` row without requiring any `?identity=` parameter
- **AND** the owner can navigate to `/secrets?focus=u:google` and reach `PageGoogleAccounts` with the scope-set picker and Google Health status card

#### Scenario: No Google account connected — no google_oauth_refresh in owner-default

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** no Google account exists in `public.google_accounts` (or none with `is_primary = true AND status != 'revoked'`)
- **THEN** the response `user` array SHALL NOT contain a `google_oauth_refresh` entry
- **AND** the spine at `/secrets` SHALL NOT render a `u:google` row in the owner-default view

#### Scenario: Only the primary account appears in owner-default — non-primary excluded

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** multiple Google accounts exist (at least one primary, at least one non-primary with `is_primary = false`)
- **THEN** the response `user` array SHALL contain exactly one `google_oauth_refresh` entry (the primary account's)
- **AND** non-primary accounts' `google_oauth_refresh` entries SHALL NOT appear in the owner-default response
- **AND** the owner MUST use `?identity=<non_primary_entity_id>` to access a non-primary account's credential details

## MODIFIED Requirements

### Requirement: Projection-Lens Identity Switcher

The identity switcher in the page header SHALL be a **projection lens** over the owner's view of household-member credential data. Switching identity SHALL re-project the User-tab credentials associated with the selected member entity, but every action (rotate, reauthorize, disconnect, probe, set, override, revoke) SHALL run with owner privilege. The backend MUST NOT enforce identity-scoped access in v1.

This matches the existing single-owner doctrine in `about/heart-and-soul/security.md:7-8, 18-20` ("user-federated. One user. One instance.", "no access control within the system that restricts the owner"). A future RFC under `about/legends-and-lore/` may introduce a household-member privilege tier; this surface is forward-compatible because the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.

The identity switcher chip SHALL include connected Google accounts as selectable identity lenses, in addition to household-member entities. Selecting a Google account entity in the switcher SHALL re-project the User-tab credentials to show that account's `google_oauth_refresh` entry (and any other credentials anchored on that companion entity). This enables the owner to access non-primary Google account credentials through the same projection-lens affordance without navigating to a separate management screen.

#### Scenario: Identity switch re-projects view

- **WHEN** the owner clicks the identity chip and selects a household member entity
- **THEN** the URL updates to `/secrets?identity=<member-id>`
- **AND** the User-tab portion of the spine re-renders to show only credentials associated with that member entity
- **AND** the CLI and System groups in the spine remain unchanged (those families are not identity-scoped)
- **AND** any mutation triggered from the page (rotate, reauthorize, etc.) is dispatched with owner privilege regardless of `?identity=` state

#### Scenario: Identity switch to a non-primary Google account

- **WHEN** the owner clicks the identity chip and selects a Google account entity (e.g. the companion entity for `tzeuse@gmail.com`)
- **THEN** the URL updates to `/secrets?identity=<google_account_entity_id>`
- **AND** the User-tab spine re-renders to show the `google_oauth_refresh` credential for that non-primary account
- **AND** `PageGoogleAccounts` renders with that account's scope-set picker and connector health data
- **AND** the CLI and System groups remain unchanged

#### Scenario: Backend ignores identity-scoped access enforcement

- **WHEN** any `/api/secrets/*` mutation endpoint receives a request with `?identity=<member-id>`
- **THEN** the endpoint validates the credential exists and mutates it
- **AND** the endpoint does NOT check whether the caller has permission to act on the member's credential (no member-level authorization in v1)

#### Scenario: Single-identity scope hides chip

- **WHEN** no household-member entities have user credentials registered
- **AND** at most one Google account is connected (zero or one)
- **THEN** there is no alternative identity lens available beyond the owner-default view
- **AND** the identity chip SHALL be hidden from the page header
- **AND** the `?identity=` URL parameter is ignored if present
- **AND** the spine renders the User group as if no switcher exists

#### Scenario: Identity chip visible when multiple Google accounts connected

- **WHEN** two or more Google accounts are connected (regardless of household-member entities)
- **THEN** the identity chip SHALL be visible in the page header
- **AND** the chip dropdown SHALL list all connected Google account entities as selectable identity lenses
- **AND** the owner-default view (no `?identity=`) SHALL show only the primary account's credentials per §Owner-Default Inventory Surfaces Primary Google Account

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) — `about/heart-and-soul/vision.md:60-63`
- Security model — single-owner doctrine — `about/heart-and-soul/security.md:7-8, 18-20`
- `_fetch_user_secrets` owner-default join (current behavior being extended) — `src/butlers/api/routers/secrets_v2.py:701-721`
- `{google_account}` companion entity model and exclusion from entity resolution — `openspec/specs/google-account-registry/spec.md:71-89`
- Primary account is_primary constraint — `openspec/specs/google-account-registry/spec.md:32-33`
- Co-owning leak-prevention invariant — `openspec/changes/google-health-secrets-surface/specs/dashboard-google-accounts/spec.md:§Multi-Account Leak Prevention`
- Original Projection-Lens Identity Switcher requirement being modified — `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md:84-105`
- Deep-link focus routing (`?focus=u:google`) — `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md:107-126`
- Implementation bead for backend join — `bu-2kejb`
- Systemic auth_status taxonomy (cross-link, NOT re-specced here) — `openspec/changes/add-connector-oauth-scope-surface/proposal.md:43-72`
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
