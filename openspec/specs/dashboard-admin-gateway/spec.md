# Dashboard Administrative Gateway

## Purpose

The dashboard serves as the primary administrative gateway for the butler system. Several critical operations -- credential management, OAuth bootstrap, approval decisions, and connector monitoring -- are frontend-gated and cannot be performed through any other interface. The dashboard is the sole surface where the operator establishes the identity and capabilities of their butler fleet: adding API keys and bootstrapping OAuth tokens via browser redirect. Approval decisions are specified in `dashboard-approvals` and connector monitoring in `dashboard-ingestion-dispatch-console`; this spec owns the credential surfaces and the frontend-gating contract. Without the dashboard, the system has no credentials, no OAuth tokens, no human-approved actions, and no operational visibility into the connector fleet.

## Requirements

### Requirement: Secrets and Credentials Management

The Secrets page (`/secrets`) SHALL be the operator's primary surface for provisioning and managing all credentials consumed by butler daemons and connectors. Secrets SHALL be organized by target (shared defaults vs. per-butler overrides) and by category (core, telegram, email, google, gemini, general). The page SHALL combine a template-driven schema of known secret requirements with the actual resolved state from the database, presenting a unified view of what is expected, what is configured, and what is missing.

#### Scenario: Target-scoped secret listing

- **WHEN** the Secrets page loads
- **THEN** a target selector lists `shared` as the first entry followed by all registered butler names
- **AND** selecting a target fetches that target's local secrets from the API and merges inherited shared secrets for non-shared targets
- **AND** the display rows are a union of known secret templates (from `SECRET_TEMPLATES`) and resolved API entries, with templates providing expected-key scaffolding for keys not yet configured

#### Scenario: Three-state secret row rendering

- **WHEN** a secret display row is rendered
- **THEN** each row is classified into exactly one of three states: `local` (value stored directly in the selected target's database), `inherited` (value resolved from the shared store, not overridden locally), or `missing` (key is expected by templates or modules but has no value anywhere)
- **AND** the state is derived from `SecretEntry.is_set` and `SecretEntry.source`: `is_set=false` maps to `missing`, `source` in `{database, local}` maps to `local`, all other sources map to `inherited`

#### Scenario: Category-grouped display with ordering

- **WHEN** secrets are displayed
- **THEN** rows are grouped by category with labeled section headers
- **AND** categories are sorted in a fixed priority order: core, telegram, email, google, gemini, general, followed by any unknown categories alphabetically
- **AND** within each category, rows are sorted alphabetically by key

#### Scenario: Source and status badge rendering

- **WHEN** a secret row is rendered
- **THEN** a status badge displays the human-readable state (`Local configured`, `Inherited from shared`, `Missing (null)`)
- **AND** a source badge displays the resolution origin (`local`, `shared`, `null`)
- **AND** local secrets use a primary badge variant, inherited secrets use secondary, missing secrets use outline

#### Scenario: Write-only value masking

- **WHEN** a secret's value column is rendered
- **THEN** values are never displayed in plaintext regardless of state
- **AND** local secrets show a masked placeholder with a reveal toggle that only confirms write-only semantics (no actual value retrieval)
- **AND** inherited secrets show a masked placeholder with "(inherited)" suffix
- **AND** missing secrets show an italic "null" indicator

#### Scenario: Secret templates and auto-suggestion

- **WHEN** a new secret is being created
- **THEN** the key input provides an autocomplete datalist populated from `SECRET_TEMPLATES`
- **AND** selecting or typing a known template key auto-fills the category and description fields
- **AND** typing an unknown key infers the category from key name heuristics (keys containing `TELEGRAM` map to telegram, `EMAIL`/`SMTP`/`IMAP` to email, `GOOGLE` to google, `GEMINI` to gemini, `ANTHROPIC`/`OPENAI` to core, default to general)
- **AND** keys are uppercased automatically on submission

#### Scenario: Known secret template definitions

- **WHEN** the Secrets page initializes its template set
- **THEN** the following templates are defined: `ANTHROPIC_API_KEY` (core), `OPENAI_API_KEY` (core), `GOOGLE_API_KEY` (core), `GEMINI_API_KEY` (gemini), `BUTLER_TELEGRAM_TOKEN` (telegram), `TELEGRAM_CHAT_ID` (telegram), `USER_TELEGRAM_TOKEN` (telegram), `TELEGRAM_API_ID` (telegram), `TELEGRAM_API_HASH` (telegram), `TELEGRAM_USER_SESSION` (telegram), `BUTLER_EMAIL_ADDRESS` (email), `BUTLER_EMAIL_PASSWORD` (email), `USER_EMAIL_ADDRESS` (email), `USER_EMAIL_PASSWORD` (email), `GOOGLE_OAUTH_CLIENT_ID` (google), `GOOGLE_OAUTH_CLIENT_SECRET` (google)
- **AND** each template includes a human-readable description and category assignment
- **AND** templates serve as scaffolding rows (shown with `missing` state) for keys not yet configured

#### Scenario: Create a new secret via modal

- **WHEN** the operator clicks "Add Secret" or "Set value" on a missing row
- **THEN** a modal dialog opens with fields for key (text input with template autocomplete), value (password input, write-only), category (select dropdown with all known categories), and description (optional text input)
- **AND** key is required and non-empty, value is required and non-empty
- **AND** submission calls the upsert API (`PUT /api/secrets/{butler}/{key}`) with the uppercased key, value, optional category, and optional description
- **AND** on success, the modal closes after a brief success indicator and the secrets list is refreshed

#### Scenario: Edit an existing local secret via modal

- **WHEN** the operator clicks the edit icon on a local secret row
- **THEN** the same modal opens in edit mode with the key field locked (disabled, pre-filled)
- **AND** the value field starts empty because values are write-only and cannot be pre-populated
- **AND** the category and description fields are pre-filled from the existing metadata
- **AND** the operator must provide a new value to update the secret

#### Scenario: Create a local override for an inherited secret

- **WHEN** the operator clicks "Override" on an inherited secret row
- **THEN** the create modal opens pre-filled with the inherited key, category, and description
- **AND** the operator provides a local value that takes precedence over the shared default for that target

#### Scenario: Delete a local secret with confirmation

- **WHEN** the operator clicks the delete icon on a local secret row
- **THEN** a confirmation dialog shows the key being deleted and warns the action is permanent
- **AND** confirming calls the delete API and refreshes the secrets list
- **AND** only local secrets (not inherited or missing) can be deleted; inherited secrets must be deleted from the shared target

#### Scenario: Shared-to-local inheritance resolution

- **WHEN** a non-shared butler target is selected
- **THEN** the frontend fetches both the local target's secrets and the shared target's secrets
- **AND** secrets present locally take precedence (local key set overrides shared key)
- **AND** shared secrets not overridden locally appear as inherited rows
- **AND** if the shared secrets API call fails, only local secrets are shown (graceful degradation)

#### Scenario: Secret categories as defined constants

- **WHEN** the secret category dropdown is rendered
- **THEN** the available categories are: core, telegram, email, google, gemini, general
- **AND** these categories are used for visual grouping, filtering, and template matching

### Requirement: Google OAuth Bootstrap Flow

The Secrets passport pages (`frontend/src/components/secrets/passport/`, with the Google app credentials form in `GoogleAppCredentials.tsx`) SHALL provide the primary mechanism to configure Google OAuth app credentials and bootstrap or refresh Google OAuth tokens. The passport pages are both the credential inventory/audit surface and the setup surface. The flow SHALL require browser interaction (redirect to Google's consent screen) and cannot be performed through MCP tools or CLI. The frontend SHALL drive a two-leg authorization code flow: it initiates the redirect to Google, the backend handles the callback, exchanges the code for tokens, and persists credentials to the database.

#### Scenario: OAuth credential status display

- **WHEN** the Google OAuth passport section loads
- **THEN** the credential status card displays presence indicators (boolean, never raw values) for: client_id configured, client_secret configured, refresh_token present
- **AND** an OAuth health badge shows the current connection state using color-coded variants: `connected` (default/green), `not_configured` (outline), `expired` (destructive), `missing_scope` (destructive), `redirect_uri_mismatch` (destructive), `unapproved_tester` (destructive), `unknown_error` (destructive)
- **AND** granted scopes are displayed when available
- **AND** remediation guidance with optional technical detail is shown when the health state is not `connected`

#### Scenario: OAuth health state enumeration

- **WHEN** the backend probes Google credential validity
- **THEN** the health state is one of: `connected` (refresh token valid, required scopes present), `not_configured` (client credentials or refresh token missing), `expired` (refresh token revoked or expired, `invalid_grant`), `missing_scope` (token valid but required scopes not granted), `redirect_uri_mismatch` (client credentials invalid, `invalid_client`), `unapproved_tester` (OAuth app in testing mode, user not approved, `access_denied`), `unknown_error` (network failure or unexpected response)

#### Scenario: Initiate OAuth authorization flow

- **WHEN** the operator clicks "Connect Google" (or "Re-connect Google" if already connected)
- **THEN** the browser navigates to the backend's `/api/oauth/google/start` endpoint
- **AND** the backend generates a cryptographically random CSRF state token, stores it in an in-memory store with 10-minute TTL, and redirects to Google's authorization URL with parameters: client_id (from DB), redirect_uri (from env or default `http://localhost:41200/api/oauth/google/callback`), response_type=code, scope (gmail.readonly, gmail.modify, calendar, contacts, contacts.readonly, contacts.other.readonly, directory.readonly), access_type=offline, prompt=consent, state
- **AND** the "Connect Google" button is disabled until both client_id and client_secret are configured in the Google app credentials form on the passport page

#### Scenario: OAuth callback processing

- **WHEN** Google redirects back to `/api/oauth/google/callback`
- **THEN** the backend validates the state parameter against the stored token (one-time-use, consumed on validation)
- **AND** exchanges the authorization code for tokens via Google's token endpoint
- **AND** extracts the refresh token (raises an error if absent)
- **AND** persists Google app credentials (`client_id`, `client_secret`, `scope`) to `butler_secrets` via the CredentialStore
- **AND** persists the account refresh token to the Google account companion entity's `entity_info`
- **AND** 302-redirects back to the frontend page that initiated the flow, built from the CSRF state (`connector_detail_path` deep-link, then `page_of_origin`, defaulting to `/secrets?focus=u:google&toast=connected`)
- **AND** when `OAUTH_DASHBOARD_URL` is configured it is treated as the frontend base URL and prefixed onto the server-built redirect path (for deployments where the dashboard UI is served from a different origin/path prefix than the API)
- **AND** secret material (client_secret, refresh_token) is never logged in plaintext

#### Scenario: OAuth callback error handling

- **WHEN** the callback encounters an error (provider error, missing code, missing state, invalid/expired state, token exchange failure, invalid token payload (`invalid_token_payload`), userinfo lookup failure (`userinfo_failed`), no refresh token returned)
- **THEN** the error is classified into a specific error code: `provider_error`, `missing_code`, `missing_state`, `invalid_state`, `token_exchange_failed`, `invalid_token_payload`, `userinfo_failed`, `no_refresh_token`
- **AND** provider errors 302-redirect back to the originating page with `?oauth_error=provider_error` when page context from the state or `OAUTH_DASHBOARD_URL` is available; all other error classes (and provider errors without any page context) return sanitized JSON error payloads
- **AND** a sanitized user-facing message is returned (no raw provider error strings leaked)
- **AND** the state token is consumed even on error to prevent reuse

#### Scenario: Credential status probing

- **WHEN** the OAuth status endpoint (`GET /api/oauth/status`) is called
- **THEN** it resolves credentials from the shared credential store
- **AND** performs three ordered checks: (1) client_id/client_secret configured, (2) refresh_token present, (3) probe Google's token endpoint to validate refresh token and verify scope coverage
- **AND** returns a structured `OAuthCredentialStatus` with state, remediation text, and detail
- **AND** if the refresh token is valid but scope field is absent from Google's response, the token is treated as connected (not incorrectly flagged as missing_scope)

#### Scenario: Delete Google credentials (API-only)

- **WHEN** a client calls `DELETE /api/oauth/google/credentials`
- **THEN** it removes client_id, client_secret, refresh_token, and scope from the database
- **AND** the butler loses access to all Google services until credentials are re-configured and the OAuth flow is re-run
- **AND** this delete-all operation is an API-only capability: the dashboard SHALL NOT expose a delete-all-Google-credentials control (no "Danger Zone" section or "Delete credentials" button)

#### Scenario: Disconnect a single Google account from the UI

- **WHEN** the operator disconnects a Google account on the passport page
- **THEN** the UI calls `DELETE /api/oauth/google/accounts/{account_id}` to revoke that one account
- **AND** other Google accounts and the shared app credentials are unaffected

#### Scenario: CSRF protection guarantees

- **WHEN** the OAuth flow is active
- **THEN** state tokens are generated with `secrets.token_urlsafe(32)` (cryptographically random)
- **AND** tokens are one-time-use (consumed on first callback validation)
- **AND** tokens expire after 10 minutes (TTL enforced by monotonic clock)
- **AND** expired tokens are evicted from the in-memory store on access
- **AND** the state store is process-local (multi-worker deployments require sticky sessions or shared state)

#### Scenario: Required OAuth scopes for butler functionality

- **WHEN** the OAuth flow requests Google scopes
- **THEN** the following scopes are requested: gmail.readonly, gmail.modify, calendar, contacts, contacts.readonly, contacts.other.readonly, directory.readonly
- **AND** the required minimum scopes for health-check validation are gmail.modify and calendar
- **AND** missing required scopes produce a `missing_scope` health state with remediation guidance

### Requirement: Frontend-Gated Operation Safety

Several operations SHALL be intentionally restricted to the dashboard frontend as a safety measure, ensuring that credential provisioning and sensitive decisions require deliberate human interaction through a visual interface.

#### Scenario: Credential provisioning requires dashboard

- **WHEN** a new butler or connector needs API keys, tokens, or credentials
- **THEN** the operator must use the Secrets page to add the credentials
- **AND** there is no MCP tool, CLI command, or automated path to provision secrets (by design)
- **AND** this ensures the human operator maintains full awareness and control of what credentials are active

#### Scenario: OAuth bootstrap requires browser

- **WHEN** Google OAuth tokens need to be obtained or refreshed
- **THEN** the flow requires browser-based redirect to Google's consent screen
- **AND** this is architecturally impossible without the dashboard frontend
- **AND** the backend's `/api/oauth/google/start` endpoint generates CSRF-protected redirect URLs that must be followed in a browser context

#### Scenario: Approval decisions via dashboard as primary surface

- **WHEN** a pending action requires human decision
- **THEN** the dashboard provides the richest decision context: full tool arguments, agent summary, timestamps, execution history
- **AND** after a successful approval the dashboard offers an inline, separately confirmed opportunity to create a standing rule for the approved action; approval itself never creates a rule
- **AND** while MCP tools also expose approve/reject, the dashboard is the intended primary decision surface

#### Scenario: Connector monitoring is dashboard-exclusive

- **WHEN** the operator needs to assess connector fleet health
- **THEN** the only visual surface for connector liveness, volume, errors, and routing is the dashboard's ingestion pages
- **AND** there is no MCP tool equivalent for the aggregated visual monitoring provided by the ingestion pages

### Requirement: No Raw Secret Reveal on the Legacy Butler-Scoped Router

The dashboard API SHALL NOT expose any mounted route that returns raw secret material on the legacy butler-scoped secrets router (`/api/butlers/{name}/secrets/...`). No GET (or any other method) on that router SHALL return a stored secret's plaintext value in its response body. This is a universal negative invariant: it holds regardless of whether dashboard API-key auth is enabled, because credentials must never appear in dashboard payloads (`about/heart-and-soul/security.md`) and raw-secret exposure is a doctrine anti-pattern (`about/craft-and-care/security-and-secrets.md`).

Value reveal, where the product supports it, SHALL be served exclusively by the governed `secrets_v2`/passport surface under the `butler-secrets` capability — never by the legacy `/api/butlers/...` router.

This requirement SHALL be enforced by a route-introspection contract test that enumerates the application's mounted API routes (via the FastAPI/Starlette route table) and FAILS if any legacy butler-scoped raw-secret-reveal route is mounted. The test SHALL be self-guarding against reintroduction: it asserts on the mounted route table, not on a hard-coded path string alone.

#### Scenario: Legacy reveal route is not mounted

- **WHEN** the dashboard application is constructed and its mounted route table is enumerated
- **THEN** no route matching the legacy raw-secret-reveal pattern `GET /api/butlers/{name}/secrets/{key}/reveal` is present
- **AND** no other route on the `/api/butlers/{name}/secrets/...` router returns a secret's plaintext value in its response body

#### Scenario: Requesting the removed legacy reveal path returns 404

- **WHEN** a client issues `GET /api/butlers/{name}/secrets/{key}/reveal` for any `name` and `key`
- **THEN** the API responds with HTTP 404 (route not found)
- **AND** no plaintext secret value is returned for any input

#### Scenario: Route-introspection contract test fails on reintroduction

- **WHEN** a developer reintroduces any mounted route on the legacy butler-scoped router that returns raw secret material
- **THEN** the route-introspection contract test SHALL FAIL by detecting the offending route in the mounted route table

### Requirement: Defense-in-Depth API-Key Authentication (Opt-In, Not Fail-Closed)

Dashboard API-key authentication via `ApiKeyMiddleware` SHALL be a defense-in-depth layer, not the primary trust boundary. The primary control is network isolation: all Docker port mappings bind to `127.0.0.1` only, with external access mediated by Tailscale serve under tailnet-level authentication (`about/heart-and-soul/security.md`). Accordingly, the daemon SHALL NOT fail startup when `DASHBOARD_API_KEY` is unset.

When `DASHBOARD_API_KEY` is set, the middleware SHALL enforce it on every `/api/*` route except the public health paths (`/api/health`, `/health`): a request missing a matching `X-API-Key` header SHALL receive HTTP 401 in the standard error envelope, and header comparison SHALL use a constant-time compare. When `DASHBOARD_API_KEY` is unset, the middleware SHALL be a no-op pass-through so deployments relying solely on network isolation are unaffected.

#### Scenario: Auth enforced when key is set

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests an `/api/*` route other than a public health path without a matching `X-API-Key` header
- **THEN** the API responds with HTTP 401 in the standard error envelope (`code: UNAUTHORIZED`)
- **AND** a request carrying the correct `X-API-Key` header is allowed through

#### Scenario: Health paths bypass auth

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests `/api/health` or `/health` without an `X-API-Key` header
- **THEN** the request is allowed through (health/readiness probes are always public)

#### Scenario: Unset key is a no-op pass-through, startup succeeds

- **WHEN** `DASHBOARD_API_KEY` is unset
- **THEN** the daemon starts successfully (NOT fail-closed)
- **AND** `ApiKeyMiddleware` passes every request through without an auth check
- **AND** the dashboard relies on the localhost-binding + Tailscale network-isolation boundary as the primary control

### Requirement: Honest Auth-Status Health Indicator

The dashboard SHALL surface an auth-status health indicator that honestly reports the system's security posture so the operator can see it without inspecting configuration. The indicator SHALL report at minimum: (1) whether API-key authentication is enabled (i.e. `DASHBOARD_API_KEY` is set), and (2) whether the data-export signing secret is operating on the known-insecure default. The indicator SHALL NOT reveal any secret value — it reports posture booleans only, consistent with the determinism contract (`about/heart-and-soul/vision.md` Rule 4) and the never-leak-credentials constraint.

#### Scenario: Indicator reports auth disabled

- **WHEN** `DASHBOARD_API_KEY` is unset and the operator reads the auth-status health indicator
- **THEN** the indicator reports API-key authentication as disabled
- **AND** it surfaces no secret value, only the posture boolean

#### Scenario: Indicator reports auth enabled

- **WHEN** `DASHBOARD_API_KEY` is set and the operator reads the auth-status health indicator
- **THEN** the indicator reports API-key authentication as enabled

#### Scenario: Indicator reports insecure export-secret default

- **WHEN** the data-export signing secret is unset (would otherwise use the insecure default) and the operator reads the auth-status health indicator
- **THEN** the indicator reports the export secret as using the insecure default
- **AND** when an explicit export secret is configured, the indicator reports the export secret as securely configured

### Requirement: Export Signer Refuses the Insecure Default

The data-export token signer SHALL NOT use the known-insecure `"dev-secret"` literal as the HMAC signing key outside an explicit development context. When `DASHBOARD_EXPORT_SECRET` is unset and no explicit dev context is in effect, the export surface SHALL refuse to operate (rather than silently signing forgeable tokens with the public default). Export tokens SHALL be signed only with an explicitly configured secret. This closes the doctrine anti-pattern of "casual env-var fallbacks" (`about/craft-and-care/security-and-secrets.md`) and prevents forgeable download tokens.

#### Scenario: Refuse export with insecure default outside dev

- **WHEN** `DASHBOARD_EXPORT_SECRET` is unset and no explicit dev context is in effect
- **THEN** the export surface SHALL refuse to mint or verify export tokens (it does not fall back to `"dev-secret"`)
- **AND** no forgeable token signed with the public default is issued

#### Scenario: Operate with explicitly configured secret

- **WHEN** `DASHBOARD_EXPORT_SECRET` is set to an explicit value
- **THEN** export tokens SHALL be signed and verified using that configured secret
- **AND** the export surface operates normally

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance — the owner owns credentials/data; protecting them is the core threat model) — `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; must be testable, debuggable, predictable — grounds the route-introspection contract test and the honest auth-status indicator) — `about/heart-and-soul/vision.md:80-84`
- Credentials must never appear in session logs or tool call payloads sent to the dashboard; "Logging full credential values" listed as an anti-pattern — `about/heart-and-soul/security.md:138-140, 293`
- Deployment Security: localhost (`127.0.0.1`) port binding + Tailscale serve as the primary trust boundary (API-key auth is defense-in-depth, not fail-closed) — `about/heart-and-soul/security.md:241-243, 272`
- Do not log raw secrets/refresh tokens; do not add casual env-var fallbacks — `about/craft-and-care/security-and-secrets.md:9, 12`
- Governed reveal surface (passport-book Evidence-Over-Value Affordance Contract; explicit reveal where supported) — `openspec/specs/butler-secrets/spec.md`
- Standard error envelope (`ApiResponse`/`ErrorResponse`) — RFC 0007 §Response Envelope
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
