# Dashboard Administrative Gateway

## Purpose

The dashboard serves as the primary administrative gateway for the butler system. Several critical operations -- credential management, OAuth bootstrap, approval decisions, and connector monitoring -- are frontend-gated and cannot be performed through any other interface. The dashboard is the sole surface where the operator establishes the identity and capabilities of their butler fleet: adding API keys, bootstrapping OAuth tokens via browser redirect, reviewing and deciding on approval-gated actions, monitoring connector health, and configuring operator preferences. Without the dashboard, the system has no credentials, no OAuth tokens, no human-approved actions, and no operational visibility into the connector fleet.

## ADDED Requirements

### Requirement: Secrets and Credentials Management

The Secrets page (`/secrets`) is the operator's primary surface for provisioning and managing all credentials consumed by butler daemons and connectors. Secrets are organized by target (shared defaults vs. per-butler overrides) and by category (core, telegram, email, google, gemini, general). The page combines a template-driven schema of known secret requirements with the actual resolved state from the database, presenting a unified view of what is expected, what is configured, and what is missing.

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
- **THEN** the following templates are defined: `ANTHROPIC_API_KEY` (core), `OPENAI_API_KEY` (core), `GOOGLE_API_KEY` (core), `GEMINI_API_KEY` (gemini), `BUTLER_TELEGRAM_TOKEN` (telegram), `BUTLER_TELEGRAM_CHAT_ID` (telegram), `USER_TELEGRAM_TOKEN` (telegram), `TELEGRAM_API_ID` (telegram), `TELEGRAM_API_HASH` (telegram), `TELEGRAM_USER_SESSION` (telegram), `BUTLER_EMAIL_ADDRESS` (email), `BUTLER_EMAIL_PASSWORD` (email), `USER_EMAIL_ADDRESS` (email), `USER_EMAIL_PASSWORD` (email), `GOOGLE_OAUTH_CLIENT_ID` (google), `GOOGLE_OAUTH_CLIENT_SECRET` (google)
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

The Google OAuth section of the Secrets page provides the only mechanism to bootstrap Google OAuth tokens. The flow requires browser interaction (redirect to Google's consent screen) and cannot be performed through MCP tools or CLI. The frontend drives a two-leg authorization code flow: it initiates the redirect to Google, the backend handles the callback, exchanges the code for tokens, and persists credentials to the database.

#### Scenario: OAuth credential status display

- **WHEN** the Google OAuth section loads
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
- **AND** the backend generates a cryptographically random CSRF state token, stores it in an in-memory store with 10-minute TTL, and redirects to Google's authorization URL with parameters: client_id (from DB), redirect_uri (from env or default `http://localhost:40200/api/oauth/google/callback`), response_type=code, scope (gmail.readonly, gmail.modify, calendar, contacts, contacts.readonly, contacts.other.readonly, directory.readonly), access_type=offline, prompt=consent, state
- **AND** the "Connect Google" button is disabled until both client_id and client_secret are configured in the Secrets table

#### Scenario: OAuth callback processing

- **WHEN** Google redirects back to `/api/oauth/google/callback`
- **THEN** the backend validates the state parameter against the stored token (one-time-use, consumed on validation)
- **AND** exchanges the authorization code for tokens via Google's token endpoint
- **AND** extracts the refresh token (raises an error if absent)
- **AND** persists all credentials (client_id, client_secret, refresh_token, scope) to `butler_secrets` via the CredentialStore
- **AND** redirects to the dashboard URL if `OAUTH_DASHBOARD_URL` is configured, otherwise returns a JSON success payload
- **AND** secret material (client_secret, refresh_token) is never logged in plaintext

#### Scenario: OAuth callback error handling

- **WHEN** the callback encounters an error (provider error, missing code, missing state, invalid/expired state, token exchange failure, no refresh token returned)
- **THEN** the error is classified into a specific error code: `provider_error`, `missing_code`, `missing_state`, `invalid_state`, `token_exchange_failed`, `no_refresh_token`
- **AND** a sanitized user-facing message is returned (no raw provider error strings leaked)
- **AND** the state token is consumed even on error to prevent reuse

#### Scenario: Credential status probing

- **WHEN** the OAuth status endpoint (`GET /api/oauth/status`) is called
- **THEN** it resolves credentials from the shared credential store
- **AND** performs three ordered checks: (1) client_id/client_secret configured, (2) refresh_token present, (3) probe Google's token endpoint to validate refresh token and verify scope coverage
- **AND** returns a structured `OAuthCredentialStatus` with state, remediation text, and detail
- **AND** if the refresh token is valid but scope field is absent from Google's response, the token is treated as connected (not incorrectly flagged as missing_scope)

#### Scenario: Delete Google credentials with confirmation

- **WHEN** the operator clicks "Delete credentials" in the Danger Zone section
- **THEN** a confirmation dialog warns that all Google OAuth credentials will be permanently removed
- **AND** confirming calls `DELETE /api/oauth/google/credentials` which removes client_id, client_secret, refresh_token, and scope from the database
- **AND** the butler loses access to all Google services until credentials are re-configured and the OAuth flow is re-run

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

### Requirement: Approval Queue and Decision Workflow

The Approvals page (`/approvals`) is the operator's primary surface for reviewing, deciding on, and managing approval-gated actions. While approval decisions can also be made via MCP tools, the dashboard provides a richer experience with filtering, pagination, metrics, action detail inspection, and one-click approve/reject with optional standing rule creation.

#### Scenario: Approval metrics dashboard

- **WHEN** the Approvals page loads
- **THEN** a metrics bar displays five key indicators: total pending actions, approved today count (green), rejected today count (red), active rules count, and auto-approval rate (percentage)
- **AND** metrics are fetched from `GET /api/approvals/metrics`

#### Scenario: Action queue with filters and pagination

- **WHEN** the operator views the actions list
- **THEN** a filter card provides: tool name text filter, status select (all, pending, approved, rejected, expired, executed) defaulting to "pending", butler text filter, and clear/expire-stale buttons
- **AND** actions are displayed in a table with columns: tool name, status (badge), requested time (relative), agent summary, session ID (truncated)
- **AND** results are paginated with 20 items per page, showing range indicators and prev/next navigation
- **AND** auto-refresh is enabled to poll for new pending actions

#### Scenario: Action detail inspection

- **WHEN** the operator clicks an action row
- **THEN** a detail dialog opens showing: status badge, tool name (monospace), tool arguments (JSON formatted, monospace), agent summary, timestamps (requested_at, expires_at, decided_at with full date-time formatting), decided_by, session_id, approval_rule_id, and execution_result (JSON formatted) if available

#### Scenario: Approve a pending action

- **WHEN** the operator clicks "Approve" on a pending action's detail dialog
- **THEN** the frontend calls the approve API with the action ID and the `create_rule` flag
- **AND** the dialog closes on success and the action list is refreshed
- **AND** the approve button is only shown for actions with `pending` status

#### Scenario: Approve with standing rule creation

- **WHEN** the operator checks "Create standing rule from this action on approval" before approving
- **THEN** the approve request includes `create_rule: true`
- **AND** the backend creates a standing rule derived from the approved action's tool name and arguments
- **AND** future matching invocations will be auto-approved

#### Scenario: Reject a pending action with optional reason

- **WHEN** the operator clicks "Reject" on a pending action's detail dialog
- **THEN** the frontend sends the rejection with an optional reason text
- **AND** the dialog closes on success and the action list is refreshed
- **AND** the rejection reason textarea is available only for pending actions

#### Scenario: Expire stale pending actions

- **WHEN** the operator clicks "Expire Stale" in the filter bar
- **THEN** a browser confirmation prompt is shown
- **AND** confirming calls the expire API to transition all stale pending actions (past `expires_at`) to `expired` status
- **AND** the action list and metrics are refreshed

#### Scenario: Non-pending action review

- **WHEN** the operator opens a detail dialog for an already-decided action (approved, rejected, expired, executed)
- **THEN** the dialog shows all metadata and execution result in read-only mode
- **AND** the approve/reject buttons are replaced with a "Close" button

### Requirement: Approval Rules Management

The Approval Rules page (`/approvals/rules`) provides management of standing approval rules that enable automatic approval of repeatable safe invocations. Rules are the primary mechanism for reducing operator overhead on well-understood, low-risk tool calls.

#### Scenario: Rules listing with filters and pagination

- **WHEN** the operator views the rules page
- **THEN** a filter card provides: tool name text filter, active status select (all, active only, inactive only) defaulting to "active only", butler text filter, and clear button
- **AND** rules are displayed in a table with columns: tool name (monospace), description, status badge (active/inactive), use count (with max_uses if bounded), created date, and action column
- **AND** results are paginated with 20 items per page

#### Scenario: Rule detail inspection

- **WHEN** the operator clicks a rule row
- **THEN** a detail dialog opens showing: active/inactive status badge, rule ID (monospace), tool name (monospace), description, argument constraints (JSON formatted), created_at (full date-time), expires_at (if set), use count with max_uses or "(unlimited)", and created_from action ID (if derived from an action)

#### Scenario: Revoke a standing rule

- **WHEN** the operator clicks "Revoke" on an active rule (either in the table row or the detail dialog)
- **THEN** a browser confirmation prompt is shown
- **AND** confirming calls the revoke API to set the rule's `active` flag to false
- **AND** the rules list and approval metrics are refreshed
- **AND** the revoke button is only shown for active rules

#### Scenario: Rule use tracking display

- **WHEN** a rule row or detail is displayed
- **THEN** the use count shows current uses vs. maximum: `N / M` when `max_uses` is set, `N (unlimited)` when `max_uses` is null
- **AND** this provides visibility into how frequently auto-approval rules are being triggered

### Requirement: Ingestion and Connector Fleet Management

The Ingestion page (`/ingestion`) is the operator's unified control surface for source visibility, connector health monitoring, routing policy, and historical replay. It provides the only real-time view of connector fleet status that is not available through any other interface. The page is organized into four tabs: Overview, Connectors, Filters, and History.

#### Scenario: Ingestion page tab navigation

- **WHEN** the operator navigates to `/ingestion`
- **THEN** four tabs are available: Overview (default), Connectors, Filters, History
- **AND** the active tab is persisted in URL search params (`?tab=connectors`)
- **AND** omitting the tab param defaults to Overview
- **AND** switching tabs clears the period param so each tab uses its own fresh default

#### Scenario: Overview tab aggregate statistics

- **WHEN** the Overview tab is active
- **THEN** an aggregate stat row displays five cards: ingested (period count), failed/skipped (tier3 count), total processed (sum of all tiers), error rate (skipped/total as percentage), and active connectors count
- **AND** statistics are period-scoped from the `message_inbox` table via the ingestion overview API
- **AND** numbers use compact formatting (K/M suffixes for large values)

#### Scenario: Overview tab volume trend chart

- **WHEN** the Overview tab displays the volume trend
- **THEN** an area chart shows ingested and failed message counts over time with selectable period (24h, 7d, 30d)
- **AND** the chart uses dual gradient-filled areas: primary color for ingested, destructive color for failed
- **AND** tooltip shows formatted counts and human-readable bucket timestamps
- **AND** legend differentiates "Ingested" vs "Failed" series

#### Scenario: Overview tab tier breakdown donut

- **WHEN** the Overview tab displays the tier breakdown
- **THEN** a donut chart shows the distribution across T1 Full, T2 Metadata, and T3 Skip
- **AND** when real tier counts from `IngestionOverviewStats` are available, those are used
- **AND** when only `CrossConnectorSummary` is available, T1 is approximated as `ingested - failed`, T2 as 0, T3 as `failed`
- **AND** zero-value tiers are filtered from the chart

#### Scenario: Overview tab fanout matrix

- **WHEN** the Overview tab displays the fanout matrix
- **THEN** a table shows connector x butler routing distribution with message counts
- **AND** rows represent connectors (`connector_type:endpoint_identity`), columns represent target butlers
- **AND** the fanout period falls back to 7d when the overview period is 24h

#### Scenario: Overview tab connector health row

- **WHEN** the Overview tab displays connector health
- **THEN** a row of inline badges shows each connector's liveness state: `online` (default/green), `stale` (outline), `offline` (destructive)
- **AND** each badge is labeled with the connector identity (`type:endpoint_identity`)

#### Scenario: Connectors tab grid and summary

- **WHEN** the Connectors tab is active
- **THEN** a cross-connector summary bar shows: total connectors, online count (badge), stale count (badge), offline count (badge), total ingested, total failed, overall error rate
- **AND** a period selector (24h/7d/30d) controls the aggregation window
- **AND** a card grid displays one card per registered connector

#### Scenario: Connector card content

- **WHEN** a connector card is rendered
- **THEN** it displays: connector type (title), endpoint identity (monospace subtitle), liveness badge with health state, today's ingestion count, uptime percentage (when available), last heartbeat age (relative time)
- **AND** an "backfill active" badge appears when the connector has an active backfill job
- **AND** clicking the card navigates to the connector detail page (`/ingestion/connectors/:type/:identity`)

#### Scenario: Connector liveness badge states

- **WHEN** a connector's liveness is rendered as a badge
- **THEN** `online` maps to default variant (green), `stale` maps to outline variant, `offline` maps to destructive variant (red)
- **AND** when `showState` is enabled, a second badge shows the health state: `healthy` (secondary), `degraded` (outline), `error` (destructive)

#### Scenario: Connectors tab volume chart

- **WHEN** the Connectors tab displays the volume trend
- **THEN** the same area chart component is reused with period-scoped data
- **AND** the chart shows per-connector timeseries (using the first connector as representative when aggregate endpoint is unavailable)

#### Scenario: Connectors tab error log panel

- **WHEN** the Connectors tab displays the error log
- **THEN** a table shows connectors in degraded, error, or offline state
- **AND** columns: last seen (relative), connector identity (monospace), state badge (error=destructive, degraded=outline), error message (truncated)
- **AND** when no errors are present, a "No connector errors detected" message is shown

#### Scenario: Connector detail page

- **WHEN** the operator navigates to `/ingestion/connectors/:type/:identity`
- **THEN** the page displays: back navigation to connectors tab, connector type heading, endpoint identity (monospace), status card with liveness/state badges and uptime percentage, error message (if present), metadata table (last seen, first seen, registered via, checkpoint cursor)
- **AND** lifetime counters card showing: ingested, failed, API calls, dedupe accepted, checkpoint saves
- **AND** period summary card (when stats available) showing: ingested, failed, error rate, avg/hour
- **AND** volume trend chart with period selector (24h/7d/30d)

#### Scenario: Connector detail data freshness

- **WHEN** connector detail data is fetched
- **THEN** detail data refreshes every 30 seconds, statistics data every 60 seconds
- **AND** connector list data refreshes every 60 seconds, cross-connector summary every 60 seconds, fanout every 120 seconds

#### Scenario: Period selector interaction

- **WHEN** the operator selects a time period (24h, 7d, or 30d)
- **THEN** the period is persisted in URL search params (`?period=7d`)
- **AND** all period-dependent data (volume charts, statistics, fanout) refreshes for the new window
- **AND** button styling distinguishes the active period (secondary variant) from inactive (ghost variant)

### Requirement: Connectors Tab Backfill Integration

The Connectors tab integrates backfill job status into the connector card grid, providing at-a-glance visibility into which connectors are currently running historical replay jobs.

#### Scenario: Active backfill indicator on connector cards

- **WHEN** connector cards are rendered
- **THEN** each card checks whether the connector has an active backfill job by querying the backfill jobs API with `status=active`
- **AND** matching connectors display a "backfill active" badge (secondary variant)
- **AND** the match key is `connector_type:endpoint_identity`

### Requirement: Settings as Operator Preferences

The Settings page (`/settings`) manages local browser-scoped preferences. These settings affect only the current browser session and are not persisted to the server. Settings provide operator comfort controls that do not affect butler behavior.

#### Scenario: Theme preference control

- **WHEN** the operator visits the Settings page
- **THEN** an appearance card provides a theme selector with three options: System (follows OS preference), Light, Dark
- **AND** the active resolved theme is displayed below the selector
- **AND** theme changes take effect immediately and persist in localStorage

#### Scenario: Live refresh default configuration

- **WHEN** the operator configures live refresh defaults
- **THEN** the auto-refresh toggle controls whether pages with live data (Sessions, Timeline) poll for updates
- **AND** the refresh interval is configurable (default 10 seconds)
- **AND** settings are persisted in localStorage and read by the `useAutoRefresh` hook

#### Scenario: Command palette history management

- **WHEN** the operator views the command palette section
- **THEN** the count of saved recent searches is displayed
- **AND** a "Clear recent searches" button removes all stored search history from localStorage
- **AND** the button is disabled when the count is zero

#### Scenario: Settings are browser-local only

- **WHEN** any setting is changed
- **THEN** the change is persisted to localStorage only
- **AND** no API calls are made to the backend
- **AND** settings do not affect other browsers, devices, or operators

### Requirement: Query Key Strategy and Cache Sharing

The dashboard implements a hierarchical query key strategy across all admin gateway surfaces to enable warm cache reuse, targeted invalidation, and efficient background refetching.

#### Scenario: Secrets query key hierarchy

- **WHEN** secrets data is managed
- **THEN** the key hierarchy is: `["secrets"]` (all), `["secrets", "credentials"]` (Google credential status), `["secrets", "oauth-status"]` (OAuth health), `["secrets", "generic", butlerName]` (all secrets for a target), `["secrets", "generic", butlerName, "list", category]` (filtered secrets)
- **AND** mutations (upsert, delete) invalidate the butler-specific key, which cascades to all list queries for that butler
- **AND** Google credential mutations invalidate the entire `["secrets"]` key prefix

#### Scenario: Approvals query key hierarchy

- **WHEN** approvals data is managed
- **THEN** the key hierarchy is: `["approvals"]` (all), `["approvals", "actions", params]` (action list), `["approvals", "action", id]` (single action), `["approvals", "executed", params]` (executed actions), `["approvals", "rules", params]` (rule list), `["approvals", "rule", id]` (single rule), `["approvals", "metrics"]` (metrics)
- **AND** approve/reject mutations invalidate the entire `["approvals"]` prefix
- **AND** rule mutations (create, revoke) invalidate both `["approvals", "rules"]` and `["approvals", "metrics"]`

#### Scenario: Ingestion query key hierarchy

- **WHEN** ingestion data is managed
- **THEN** the key hierarchy is: `["ingestion"]` (all), `["ingestion", "connectors-list"]` (connector summaries), `["ingestion", "connectors-summary", period]` (cross-connector aggregate), `["ingestion", "ingestion-overview", period]` (inbox-based overview), `["ingestion", "fanout", period]` (fanout matrix), `["ingestion", "connector-detail", type, identity]` (single connector), `["ingestion", "connector-stats", type, identity, period]` (connector timeseries)
- **AND** the connector list and summary keys are shared between Overview and Connectors tabs so switching tabs reuses warm cache

### Requirement: Frontend-Gated Operation Safety

Several operations are intentionally restricted to the dashboard frontend as a safety measure, ensuring that credential provisioning and sensitive decisions require deliberate human interaction through a visual interface.

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
- **AND** the approve-with-rule-creation workflow is a dashboard-exclusive UX pattern (combining approval with standing rule creation in one step)
- **AND** while MCP tools also expose approve/reject, the dashboard is the intended primary decision surface

#### Scenario: Connector monitoring is dashboard-exclusive

- **WHEN** the operator needs to assess connector fleet health
- **THEN** the only visual surface for liveness badges, volume trends, fanout matrices, error logs, and tier breakdowns is the dashboard
- **AND** there is no MCP tool equivalent for the aggregated visual monitoring provided by the ingestion pages
