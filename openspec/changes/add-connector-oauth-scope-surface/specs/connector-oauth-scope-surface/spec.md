# Connector OAuth Scope Surface

## Purpose

Defines the per-connector contract for declaring required OAuth scopes,
observing currently-granted scopes from providers, detecting drift,
surfacing scope state on the connector-detail dashboard page (the
`ReauthCallout` band and `ScopeList` panel), gating reauthorization through
the Approvals module, and auditing scope rotations.

This capability unblocks
`POST /api/ingestion/connectors/{type}/{identity}/reauth`, which is
deliberately stubbed with HTTP 503 in `connector-lifecycle-ceremony` until
this spec exists (per
`openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40`).

It extends `connector-base-spec` (`openspec/specs/connector-base-spec/spec.md`)
additively, depends on `module-approvals` for reauth gating, depends on the
existing `public.audit_log` infrastructure (per `dashboard-api/spec.md:530-541`
and `connector-lifecycle-ceremony/spec.md:90-101`) for audit emissions, and
re-uses the OAuth state-store infrastructure introduced by
`google-multi-account-oauth/spec.md:76-83`.

The capability applies non-uniformly: OAuth-bound connectors (Spotify,
Gmail, Google Calendar, Google Drive, Google Health, Discord, …) receive the
full scope surface; non-OAuth connectors (Telegram bot, Telegram user
client, OwnTracks, Home Assistant, WhatsApp, Steam) return a structured
`unsupported` shape that keeps the dashboard grid uniform but exposes the
alternative credential surface for each provider.

## ADDED Requirements

### Requirement: Connector module scope manifest

Each connector module that authenticates via OAuth SHALL declare its scope
requirements as a structured manifest constant (Python literal) in the
connector module package, accessible as `<connector_pkg>.SCOPES`. The
manifest SHALL contain three scope categories and a forward-only version
counter.

#### Scenario: Manifest schema shape

- **WHEN** a connector module declares its scope manifest
- **THEN** the manifest SHALL be a mapping with the keys: `version` (int,
  forward-only counter starting at 1), `required` (list of `ScopeDecl`),
  `optional` (list of `ScopeDecl`, may be empty), and `sensitive` (list of
  `ScopeDecl`, may be empty)
- **AND** each `ScopeDecl` SHALL contain at minimum: `name` (provider's scope
  string, e.g. `user-read-recently-played` or
  `https://www.googleapis.com/auth/calendar`), `serif_note` (single sentence,
  no trailing period, user-facing copy that explains the scope's purpose)
- **AND** `ScopeDecl` entries in the `sensitive` list SHALL additionally
  contain `approval_reason` (single sentence, user-facing copy explaining
  the elevated grant — written for the Approvals dialog)

#### Scenario: Forward-only version counter

- **WHEN** a connector author edits the `required` list
- **THEN** they SHALL increment the `version` counter
- **AND** decrementing the version counter or re-using a prior version number
  is a violation of this spec
- **AND** edits to `optional` or `sensitive` lists SHALL NOT bump the
  `version` counter (those changes do not constitute drift for existing
  connections)

#### Scenario: Non-OAuth connector exemption

- **WHEN** a connector authenticates via a non-OAuth credential model (static
  bearer, TDLib session, app password, API key, long-lived access token)
- **THEN** the module SHALL NOT declare a `SCOPES` manifest
- **AND** the per-connector applicability matrix (see
  §Per-connector applicability matrix) SHALL classify it as `unsupported`

#### Scenario: Manifest registry exposure

- **WHEN** the dashboard API resolves a connector's scope manifest
- **THEN** it SHALL look up the manifest via a typed registry (e.g.
  `butlers.connectors.oauth_scope_registry.get(connector_type)`) that maps
  `connector_type` strings to manifests
- **AND** unrecognized `connector_type` values SHALL resolve to `None`, which
  the API SHALL treat as `auth.status = unsupported`

### Requirement: Observed-scope storage on connector_registry

The `public.connector_registry` table SHALL gain four additive columns to
store observed scope state and the precomputed authentication rollup. NULL
default on all four; no data backfill required.

#### Scenario: Additive column schema

- **WHEN** the `public.connector_registry` schema is migrated for this
  capability
- **THEN** the following columns SHALL be added (additive, nullable, NO
  default values for backward compatibility):
  - `observed_scopes TEXT[] NULL` — granted scopes as last observed from the
    provider; NULL means never probed
  - `observed_scopes_fetched_at TIMESTAMPTZ NULL` — wall-clock timestamp of
    last successful observation
  - `required_scopes_version SMALLINT NULL` — manifest `version` integer
    captured at the most recent reauth completion for this row; used to
    detect rotation
  - `auth_status VARCHAR(32) NULL` — precomputed rollup; one of
    `ok | degraded | expired | rotation-needed | unsupported | unconfigured`

#### Scenario: NULL semantics on read

- **WHEN** a row has `observed_scopes IS NULL`
- **THEN** the dashboard API SHALL report `auth.status = unconfigured` for
  OAuth connectors that have not yet completed a token introspection
- **AND** SHALL report `auth.status = unsupported` for non-OAuth connectors
  (regardless of NULL observation state)

#### Scenario: No history table

- **WHEN** a scope observation supersedes a prior one
- **THEN** the prior observation SHALL be overwritten in place
- **AND** the historical record SHALL live in `public.audit_log` via the
  `connector.scope.observed` audit entry (see §Audit trail) — NOT in a
  separate `connector_scope_history` table

### Requirement: Re-introspection cadence and triggers

OAuth-bound connectors SHALL refresh `observed_scopes` and
`observed_scopes_fetched_at` opportunistically on every successful token
refresh AND on a configurable cadence (default every 6 hours) as a fallback
for long-lived tokens.

#### Scenario: Opportunistic refresh on token refresh

- **WHEN** an OAuth connector successfully refreshes its access token (per
  `connector-spotify/spec.md:111-135` for Spotify, equivalent helpers for
  other providers)
- **THEN** the connector SHALL parse the refresh response's `scope` field (or
  equivalent — Google's `tokeninfo`, Spotify's refresh-response `scope`)
- **AND** SHALL update `connector_registry.observed_scopes` and
  `observed_scopes_fetched_at` for the row keyed by
  `(connector_type, endpoint_identity)`
- **AND** SHALL recompute and persist `auth_status` (see
  §Auth status computation)
- **AND** SHALL emit a `connector.scope.observed` audit entry
  (see §Audit trail)

#### Scenario: Missing scope field on refresh response

- **WHEN** a token refresh succeeds but the provider omits the `scope` field
  (Google in particular omits it when scopes are unchanged — see
  `src/butlers/api/routers/oauth.py:1607-1620`)
- **THEN** the connector SHALL NOT update `observed_scopes` (preserve the
  prior observation)
- **AND** SHALL update `observed_scopes_fetched_at` (the freshness probe
  succeeded; only the field was absent)
- **AND** SHALL NOT flip `auth_status` to `drift` on the basis of the missing
  field

#### Scenario: Fallback cadence task

- **WHEN** an OAuth connector has not refreshed its token within
  `CONNECTOR_SCOPE_REINTROSPECT_INTERVAL_S` seconds (default 21600 = 6 hours)
- **THEN** the connector SHALL invoke its provider-specific introspection
  endpoint (Google `https://oauth2.googleapis.com/tokeninfo?access_token=...`,
  Spotify `GET /me` as a side-effect probe, Discord `/users/@me`)
- **AND** SHALL update `observed_scopes` and `observed_scopes_fetched_at`
  identically to the opportunistic path

#### Scenario: Introspection failure handling

- **WHEN** the introspection call fails with a network error or transient
  5xx response
- **THEN** the connector SHALL log a warning and SHALL NOT modify
  `observed_scopes` or `auth_status` (keep last-known-good state)
- **AND** the failure SHALL NOT crash the connector or block its main poll
  loop

#### Scenario: Introspection failure with token rejection

- **WHEN** the introspection call fails with HTTP 401 or HTTP 400
  `invalid_grant`
- **THEN** the connector SHALL set `auth_status = expired` on the row
- **AND** SHALL emit a `connector.scope.observed` audit entry with
  `result = expired`
- **AND** SHALL set its heartbeat status to `error` with
  `error_message = "oauth_token_expired"` (consistent with
  `connector-spotify/spec.md:119-123`)

### Requirement: Drift taxonomy and per-scope status

The dashboard API SHALL compute a per-scope status for every entry in the
connector's `required` manifest list and SHALL classify the connector-level
drift state. Five classes apply.

#### Scenario: Per-scope status values

- **WHEN** the API computes the per-scope status for a single scope entry
- **THEN** the status SHALL be one of: `ok` (scope is present in
  `observed_scopes`), `missing` (scope is in `required` but absent from
  `observed_scopes`), or `extra` (scope is in `observed_scopes` but absent
  from `required` — applies only to entries projected via the "extras"
  channel; see scenario "Extras projection")

#### Scenario: Sensitive-scope orthogonal flag

- **WHEN** a scope is marked `sensitive` in the manifest AND is present in
  `observed_scopes`
- **THEN** the API SHALL set `sensitive_granted = true` on that scope's row
  in the `scopes[]` block
- **AND** the per-scope `status` SHALL remain `ok` (the sensitivity flag is
  orthogonal to grant status)

#### Scenario: Connector-level drift classification

- **WHEN** the API computes the connector-level drift class from `required`
  and `observed_scopes`
- **THEN** the classification SHALL be one of:
  - `ok` — `set(required) ⊆ set(observed_scopes)` AND no `required` entry
    is also in `sensitive` (i.e. the sensitive grant case is handled
    separately)
  - `extra` — `set(observed_scopes) ⊋ set(required)` AND `set(required) ⊆
    set(observed_scopes)` (granted has all required PLUS scopes beyond
    required)
  - `drift` — `set(required) ⊄ set(observed_scopes)` (at least one required
    scope is missing)
  - `expired` — provider rejected the token entirely (no observable granted
    set; see §Re-introspection cadence and triggers)
  - `unsupported` — non-OAuth connector

#### Scenario: Extras projection

- **WHEN** `observed_scopes` contains scope names not in `required`,
  `optional`, or `sensitive`
- **THEN** those scopes SHALL be projected into the API response's `scopes[]`
  block as additional rows with `status = extra`
- **AND** their `serif_note` SHALL be the static string "Granted beyond the
  declared requirement; harmless but visible for audit"
- **AND** they SHALL NOT cause `auth_status` to flip away from `ok`

### Requirement: Auth status computation

The `auth_status` enum on `connector_registry` SHALL be computed
deterministically from the drift classification, the manifest version
comparison, and the connector's credential type. Six values are defined.

#### Scenario: `ok` status

- **WHEN** the drift class is `ok` or `extra`
- **AND** `required_scopes_version` equals the manifest's current `version`
- **AND** no `optional` scope is missing
- **THEN** `auth_status = ok`

#### Scenario: `degraded` status

- **WHEN** the drift class is `ok` or `extra`
- **AND** `required_scopes_version` equals the manifest's current `version`
- **AND** at least one `optional` scope is missing
- **THEN** `auth_status = degraded`
- **AND** the connector continues to function in its primary role; the
  dashboard MAY surface this as an eyebrow only (no `ReauthCallout`)

#### Scenario: `expired` status

- **WHEN** the most recent introspection attempt produced an
  authentication-level failure (HTTP 401 or `invalid_grant`)
- **THEN** `auth_status = expired`
- **AND** the dashboard SHALL render the `ReauthCallout` with the copy
  variant "session expired"

#### Scenario: `rotation-needed` status

- **WHEN** the drift class is `drift` OR `required_scopes_version` is less
  than the manifest's current `version`
- **THEN** `auth_status = rotation-needed`
- **AND** the dashboard SHALL render the `ReauthCallout` with the copy
  variant "reauth required"

#### Scenario: `unsupported` status

- **WHEN** the connector type maps to a non-OAuth credential model (per
  the per-connector applicability matrix)
- **THEN** `auth_status = unsupported`
- **AND** the API response's `scopes[]` block SHALL be the empty list `[]`
- **AND** the `auth` block SHALL include an `alt_surface` sub-block (see
  §Non-OAuth connectors: alt-auth surface)

#### Scenario: `unconfigured` status

- **WHEN** the connector type is OAuth-bound AND `observed_scopes IS NULL`
  AND no token introspection has succeeded
- **THEN** `auth_status = unconfigured`
- **AND** the dashboard SHALL surface this as a "Connect" CTA, not as a
  `ReauthCallout`

#### Scenario: Computed on write

- **WHEN** any of the inputs to `auth_status` change (new observation,
  manifest version bump, credential type change, fresh introspection)
- **THEN** the writing code path SHALL recompute and persist `auth_status`
- **AND** reads of `connector_registry.auth_status` SHALL trust the stored
  value (no recomputation on read)

### Requirement: Dashboard API response shape for auth and scopes blocks

The connector-detail API response SHALL include an `auth` block and a `scopes` block under additive fields, with a stable shape across OAuth and non-OAuth connectors. The endpoint is `GET /api/ingestion/connectors/{type}/{identity}`, owned by `connector-base-spec` per spec.md:388-392.

#### Scenario: `auth` block for OAuth connectors

- **WHEN** the connector type is OAuth-bound and `observed_scopes` is not
  NULL
- **THEN** the response SHALL include:
  ```json
  {
    "auth": {
      "status": "ok | degraded | expired | rotation-needed",
      "type": "oauth",
      "note": "<provider-specific summary, e.g. 'oauth refresh · 364d expiry'>",
      "expires_at": "<iso8601 | null>",
      "required_scopes_version": <int>,
      "manifest_version": <int>
    }
  }
  ```
- **AND** the `note` field SHALL be the same shape as the bundle fixture
  (`pr/overview/ingestion-redesign/ingestion-connectors-data.jsx:102,128`)
- **AND** the `expires_at` field SHALL be the access token expiry timestamp
  when known, or `null` for refresh tokens with no expiry

#### Scenario: `auth` block for non-OAuth connectors

- **WHEN** the connector type is non-OAuth (per the applicability matrix)
- **THEN** the response SHALL include:
  ```json
  {
    "auth": {
      "status": "unsupported",
      "type": "<bearer | session-string | app-password | api-key | long-lived-token>",
      "note": "<provider-specific summary>",
      "alt_surface": {
        "kind": "session-validity | static-token | device-pairing",
        "validity_known": <bool>,
        "validity_expires_at": "<iso8601 | null>",
        "remediation_path": "<dashboard route>"
      }
    }
  }
  ```
- **AND** the `scopes` block SHALL be the empty list `[]`

#### Scenario: `scopes` block shape

- **WHEN** the connector type is OAuth-bound
- **THEN** the response SHALL include a `scopes` array where each entry has:
  ```json
  {
    "name": "<scope string, e.g. user-read-recently-played>",
    "category": "required | optional | sensitive | extra",
    "status": "ok | missing | extra",
    "sensitive_granted": <bool>,
    "granted_at": "<iso8601 | null>",
    "required_since": "<iso8601 | null>",
    "serif_note": "<single sentence, no trailing period>"
  }
  ```
- **AND** the array SHALL be ordered: `required` entries first (in manifest
  declaration order), then `optional`, then `sensitive`, then `extra`
- **AND** `granted_at` MAY be NULL when the historical grant time is not
  recoverable (the spec does not require backfilling this from audit log
  history for v1)
- **AND** `required_since` is the manifest version timestamp at which this
  scope became required; MAY be NULL for v1 manifests that do not track
  per-scope `since`

#### Scenario: No credentials in response

- **WHEN** any field of the `auth` or `scopes` block is serialized
- **THEN** the response SHALL NOT contain any access token, refresh token,
  client secret, bearer token, session string, app password, or API key
- **AND** this requirement SHALL be enforced consistent with
  `connector-lifecycle-ceremony/spec.md:103-109` ("No credentials in
  lifecycle API responses")

#### Scenario: Additive field rule

- **WHEN** this capability is implemented
- **THEN** the addition of `auth` and `scopes` blocks SHALL be purely
  additive to the existing `ConnectorDetail` response model from
  `connector-base-spec/spec.md:388-392`
- **AND** no existing field shape SHALL change

### Requirement: Reauth endpoint contract for OAuth connectors

The reauth endpoint `POST /api/ingestion/connectors/{type}/{identity}/reauth` SHALL, when this capability is implemented, return a structured authorization URL and CSRF state token for OAuth-bound connectors. The endpoint is owned by `connector-lifecycle-ceremony` (currently stubbed with HTTP 503) and remains Approvals-gated per `connector-lifecycle-ceremony/spec.md:9-19`.

#### Scenario: OAuth provider response shape

- **WHEN** the endpoint is invoked for an OAuth-bound connector AND the
  Approvals gate has been satisfied
- **THEN** the response SHALL be HTTP 200 with body:
  ```json
  {
    "auth_url": "<provider authorization URL with scopes parameter>",
    "state": "<32-byte URL-safe random token>",
    "expires_in": 600
  }
  ```
- **AND** the `auth_url` SHALL include the union of the manifest's `required`
  scopes plus any currently-granted `optional` and `sensitive` scopes (so
  reauth preserves prior grants, consistent with
  `dashboard-spotify-setup/spec.md:94-99`)
- **AND** the `state` token SHALL be stored in the OAuth state store with a
  600-second TTL and SHALL be bound to the tuple
  `(connector_type, endpoint_identity, requesting_operator)`
- **AND** the response SHALL NOT contain any access token, refresh token, or
  client secret

#### Scenario: Force-consent semantics

- **WHEN** the manifest's `required` set has changed since the connector
  was last authorized (i.e. `required_scopes_version` is less than the
  manifest's current `version`)
- **THEN** the constructed `auth_url` SHALL include the provider's
  force-consent parameter (e.g. `prompt=consent` for Google per
  `google-multi-account-oauth/spec.md:69-74`, `show_dialog=true` for
  Spotify)
- **AND** this SHALL guarantee that the provider returns a fresh refresh
  token reflecting the current scope set

#### Scenario: Idempotent rapid re-initiation

- **WHEN** the endpoint is invoked while a prior state token issued for the
  same `(connector_type, endpoint_identity)` tuple is still within its TTL
  and unconsumed
- **THEN** the prior state token SHALL be revoked (deleted from the state
  store)
- **AND** a fresh state token SHALL be issued and returned
- **AND** the operator experience SHALL be that "Re-authorize" is safe to
  click multiple times — only the most recent click is valid

#### Scenario: Approval submission audit emission

- **WHEN** the endpoint submits the action to the Approvals module
- **THEN** an audit entry SHALL be written with
  `action = "connector.reauth.submit"`,
  `target = {connector_type, endpoint_identity}`,
  and the approval id in the entry metadata
- **AND** this SHALL satisfy the audit-emission requirement on the lifecycle
  ceremony gate matrix (`connector-lifecycle-ceremony/spec.md:90-101`)

#### Scenario: Approval resolution audit emission

- **WHEN** the Approval resolves
- **THEN** a second audit entry SHALL be written with
  `action = "connector.reauth.approved"` or
  `action = "connector.reauth.denied"`
- **AND** the original approval id SHALL be included in the entry metadata
- **AND** if denied, NO authorization URL is returned and NO state token is
  issued

### Requirement: Reauth callback contract

OAuth provider callback handlers SHALL, when this capability is implemented, accept the state token issued by the reauth endpoint, complete the token exchange, update `observed_scopes` and `auth_status` on the relevant `connector_registry` row, and emit completion audit entries. Callback handlers are per-provider (e.g. `/api/oauth/google/callback`, `/api/oauth/spotify/callback`).

#### Scenario: State validation

- **WHEN** the callback receives `state` and `code` query parameters
- **THEN** the state SHALL be looked up in the OAuth state store
- **AND** if the state is unknown, the callback SHALL return HTTP 400
  `{"error": "state_unknown"}`
- **AND** if the state has expired (TTL passed), the callback SHALL return
  HTTP 400 `{"error": "state_expired"}`
- **AND** if the state has already been consumed (one-use enforcement), the
  callback SHALL return HTTP 400 `{"error": "state_already_used"}`

#### Scenario: Successful reauth completion

- **WHEN** the state is valid AND the token exchange succeeds
- **THEN** the state SHALL be marked consumed in the state store
- **AND** the new refresh token SHALL be stored per the existing credential
  pipeline (`core-credentials/spec.md:51-99` for Google;
  `core-credentials/spec.md:200-223` for Spotify; equivalent for other
  providers)
- **AND** `connector_registry.observed_scopes`,
  `observed_scopes_fetched_at`, and `required_scopes_version` SHALL be
  updated for the row keyed by `(connector_type, endpoint_identity)`
- **AND** `auth_status` SHALL be recomputed and persisted
- **AND** an audit entry SHALL be written with
  `action = "connector.reauth.completed"`, the new observed scope list, and
  the new `required_scopes_version`
- **AND** if any granted scope is in the manifest's `sensitive` list, an
  additional audit entry SHALL be written with
  `action = "connector.scope.elevated_grant"` per §Audit trail
- **AND** the callback SHALL redirect the operator to the connector-detail
  page

#### Scenario: Failed reauth completion

- **WHEN** the state is valid but the token exchange fails (network error,
  provider-side rejection, malformed response)
- **THEN** the state SHALL be marked consumed (single-use enforcement does
  not depend on success)
- **AND** an audit entry SHALL be written with
  `action = "connector.reauth.failed"` and a structured `error_detail` field
- **AND** `auth_status` SHALL NOT be updated (preserve the prior status)
- **AND** the callback SHALL return an error page (or redirect to the
  connector-detail page with an error query param) — the spec does not
  dictate the operator-facing UX of the failure beyond the audit and
  status-preservation guarantees

### Requirement: Reauth endpoint contract for non-OAuth connectors

The reauth endpoint SHALL return HTTP 200 with a structured "unsupported" body for connectors whose `auth_status` is `unsupported` — NOT HTTP 503, NOT HTTP 404, and NOT HTTP 4xx.

#### Scenario: Non-OAuth response shape

- **WHEN** the endpoint is invoked for a connector whose
  `auth_status = unsupported`
- **THEN** the response SHALL be HTTP 200 with body:
  ```json
  {
    "error": "unsupported",
    "reason": "<single sentence explaining why reauth is N/A for this provider>",
    "remediation": "<dashboard route to the appropriate credential surface>"
  }
  ```
- **AND** the response SHALL NOT include a `Retry-After` header (no
  time-based recovery is meaningful — the connector simply does not have a
  reauth operation)
- **AND** the response SHALL NOT pass through the Approvals module (the
  request is rejected before approval submission)

#### Scenario: No audit entry on non-OAuth reauth attempt

- **WHEN** the endpoint returns the `unsupported` response
- **THEN** NO audit entry SHALL be written (consistent with the
  request-rejected-before-approval semantics)
- **AND** Prometheus MAY record a counter increment for observability, but
  audit log emissions are reserved for actions with operator-visible
  consequence

### Requirement: Per-connector applicability matrix

This spec SHALL maintain a normative matrix classifying every connector
type by credential model. Implementations SHALL include a typed registry
that maps `SourceProvider` enum values (per
`connector-base-spec/spec.md:107`) to entries in this matrix.

#### Scenario: Matrix entries (v1)

- **WHEN** the matrix is consulted
- **THEN** the following classifications SHALL apply:

  | `connector_type` | Credential model | `auth.status` domain | Reauth |
  |------------------|------------------|----------------------|--------|
  | `spotify` | OAuth 2.0 PKCE | `ok / degraded / expired / rotation-needed / unconfigured` | Yes |
  | `gmail` | Google OAuth | same | Yes |
  | `google_calendar` | Google OAuth | same | Yes |
  | `google_drive` | Google OAuth | same | Yes |
  | `google_health` | Google OAuth | same | Yes |
  | `discord` | OAuth 2.0 (planned) | same | Yes |
  | `telegram_bot` | Bot token (static) | `unsupported` | No |
  | `telegram_user_client` | TDLib session string | `unsupported` | No |
  | `owntracks` | Bearer token (static) | `unsupported` | No |
  | `home_assistant` | Long-lived access token | `unsupported` | No |
  | `whatsapp` | Meta business app | `unsupported` | No |
  | `steam` | API key | `unsupported` | No |
  | `live-listener` | Internal (no remote auth) | `unsupported` | No |
  | `filtered_events` | Internal (no remote auth) | `unsupported` | No |

- **AND** each `unsupported` row's `alt_surface.remediation_path` SHALL
  point at the dashboard route that owns the alternative credential
  surface for that provider

#### Scenario: Adding a new connector type

- **WHEN** a new connector type is added to the project
- **THEN** the connector author SHALL add an entry to this matrix in the
  same PR that adds the connector module
- **AND** unrecognized `connector_type` values at runtime SHALL default to
  `auth_status = unsupported` with `alt_surface.kind = "static-token"` as a
  fail-safe — but the spec violation SHOULD be caught by the matrix
  completeness test (see §Test obligations) before reaching production

### Requirement: Audit trail

All scope-surface state transitions SHALL emit `audit.append()` entries to
`public.audit_log` consistent with the existing audit infrastructure (per
`dashboard-api/spec.md:530-541`). Retention is indefinite per
`connector-lifecycle-ceremony/spec.md:91`.

#### Scenario: Audit action namespace

- **WHEN** this capability emits audit entries
- **THEN** the `action` values SHALL be drawn from this set:
  - `connector.scope.observed` — emitted on every successful introspection;
    metadata includes `observed_scopes`, drift class, prior observed_scopes
    (for diff convenience), and `result = ok | drift | expired`
  - `connector.scope.elevated_grant` — emitted when a reauth callback
    completes and the granted set includes any scope marked `sensitive` in
    the manifest; metadata includes the scope name, manifest version, and
    the `approval_reason` from the `ScopeDecl`
  - `connector.scope.required_changed` — emitted once per connector row
    when the daemon notices that the manifest's `version` exceeds
    `required_scopes_version`; metadata includes `from_version`,
    `to_version`, `newly_required`, `newly_dropped`
  - `connector.reauth.submit` — emitted on POST `.../reauth` Approvals
    submission (consistent with `connector-lifecycle-ceremony/spec.md:97-99`)
  - `connector.reauth.approved` / `connector.reauth.denied` — emitted on
    Approval resolution (consistent with
    `connector-lifecycle-ceremony/spec.md:100-101`)
  - `connector.reauth.completed` / `connector.reauth.failed` — emitted by
    the OAuth callback handler on success or failure

#### Scenario: Audit entry field shape

- **WHEN** any of the above entries is written
- **THEN** the entry SHALL include `actor` (operator id, always the owner in
  v1), `action` (string from the namespace above),
  `target = {connector_type, endpoint_identity}`, `reason` (operator-supplied
  free text from the dashboard or system-generated for non-operator entries),
  and `request_id` (the originating HTTP request id)
- **AND** entries with sensitive metadata (the `connector.scope.observed`
  entry's `observed_scopes` field) SHALL NOT contain token or credential
  values — scope strings are safe; tokens are not

#### Scenario: Idempotent `required_changed` emission

- **WHEN** the daemon scans rows after a deploy and finds multiple
  connectors whose `required_scopes_version` is behind the manifest version
- **THEN** ONE audit entry SHALL be emitted per `(connector_type,
  endpoint_identity)` pair per version transition
- **AND** subsequent scans within the same version delta SHALL NOT emit
  duplicate entries (idempotency key: `(connector_type, endpoint_identity,
  from_version, to_version)`)

### Requirement: State token security

OAuth state tokens issued by the reauth endpoint SHALL be CSRF-bound,
single-use, time-bounded, and resistant to replay.

#### Scenario: Token entropy and binding

- **WHEN** a state token is generated
- **THEN** it SHALL be 32 bytes of cryptographically random data, URL-safe
  base64-encoded
- **AND** SHALL be stored in the state store with the bound tuple
  `(connector_type, endpoint_identity, requesting_operator, issued_at)`

#### Scenario: TTL

- **WHEN** a state token is stored
- **THEN** its TTL SHALL be 600 seconds (10 minutes)
- **AND** lookups after expiry SHALL return `state_expired`

#### Scenario: One-use consumption

- **WHEN** a state token is consumed by the callback handler
- **THEN** it SHALL be marked consumed (deleted or flagged) atomically with
  the token-exchange initiation
- **AND** subsequent callbacks presenting the same token SHALL return
  `state_already_used`

#### Scenario: Revoke-on-reissue

- **WHEN** the reauth endpoint is invoked for a `(connector_type,
  endpoint_identity)` pair that already has a non-consumed state token
- **THEN** the prior token SHALL be revoked (deleted from the store) before
  the new token is issued
- **AND** a callback presenting the revoked token SHALL return
  `state_already_used` (semantically equivalent to consumed)

### Requirement: Test obligations

The capability's implementation SHALL include tests covering the matrix
completeness, drift classification, audit emission, and credential masking
guarantees.

#### Scenario: Matrix completeness test

- **WHEN** the test suite runs
- **THEN** a test SHALL assert that every value of the `SourceProvider`
  enum (per `connector-base-spec/spec.md:107`) has an entry in the
  per-connector applicability matrix
- **AND** the test SHALL fail with a clear message if a new
  `SourceProvider` value is added without a corresponding matrix entry

#### Scenario: Drift class coverage

- **WHEN** the test suite runs
- **THEN** unit tests SHALL cover each of the five drift classes (`ok`,
  `extra`, `drift`, `expired`, `unsupported`) with at least one test case
  each, using fixture manifests

#### Scenario: State token replay test

- **WHEN** the test suite runs
- **THEN** an integration test SHALL exercise the state token lifecycle:
  issue → callback success → second callback presentation → assert
  `state_already_used`
- **AND** a separate test SHALL exercise revoke-on-reissue: issue → re-issue
  → first callback presentation → assert `state_already_used`

#### Scenario: Credential-masking test

- **WHEN** the test suite runs
- **THEN** a test SHALL serialize a full `GET /api/ingestion/connectors/{type}/{identity}`
  response for a connected Spotify connector and assert that NO field in the
  response (recursively) contains a value matching a typical token shape
  (e.g. matches `^[A-Za-z0-9._-]{40,}$` AND key name contains `token` or
  `secret`)

#### Scenario: Audit emission test

- **WHEN** the test suite runs
- **THEN** an integration test SHALL exercise the full reauth flow
  (POST `.../reauth` → Approval submit → Approval approved → OAuth callback)
  and assert that exactly four audit entries are emitted with the correct
  `action` values: `connector.reauth.submit`,
  `connector.reauth.approved`, `connector.reauth.completed`, and (if a
  sensitive scope was granted) `connector.scope.elevated_grant`

### Requirement: Non-OAuth connectors: alt-auth surface

Non-OAuth connectors SHALL surface their alternative credential model in a
structured `alt_surface` block under the `auth` response field. The shape
SHALL be uniform regardless of provider.

#### Scenario: Static-token alt surface

- **WHEN** the connector uses a static bearer or app password (Telegram
  bot, OwnTracks, Home Assistant, Meta business)
- **THEN** `alt_surface.kind = "static-token"`
- **AND** `alt_surface.validity_known = false`
- **AND** `alt_surface.validity_expires_at = null`
- **AND** `alt_surface.remediation_path` SHALL point at the dashboard route
  that exposes the token-rotation UI for that provider (e.g.
  `/settings/secrets#owntracks` for OwnTracks)

#### Scenario: Session-validity alt surface

- **WHEN** the connector uses a session that may expire (Telegram user
  client TDLib session, WhatsApp)
- **THEN** `alt_surface.kind = "session-validity"`
- **AND** `alt_surface.validity_known = true` when the daemon has observed
  session expiry signals, else `false`
- **AND** `alt_surface.validity_expires_at` SHALL be the observed expiry
  timestamp when known, else `null`
- **AND** `alt_surface.remediation_path` SHALL point at the dashboard route
  for re-pairing or re-establishing the session

#### Scenario: Device-pairing alt surface

- **WHEN** the connector requires QR-code or device-code pairing (no v1
  connectors currently use this, but the kind is reserved)
- **THEN** `alt_surface.kind = "device-pairing"`
- **AND** the remaining fields follow the `static-token` defaults

## MODIFIED Requirements

### Requirement: Per-action lifecycle gate matrix (modifies `connector-lifecycle-ceremony`)

The system SHALL enforce the following gate matrix for connector lifecycle actions invoked via the dashboard or its API, superseding the prior `reauth` gate entry in `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:11-17`. The HTTP 503 block is lifted once the `connector-oauth-scope-surface` capability ratifies.

| Action | Gate |
|--------|------|
| `pause` | audit-log-only |
| `run-now` | audit-log-only (defined as "resume from pause") |
| `disconnect` | Approvals-gated |
| `rotate-token` | Approvals-gated; `is_sensitive=True` masking mandatory |
| `reauth` | Approvals-gated; delegates to `connector-oauth-scope-surface/spec` for behavior contract |

#### Scenario: Reauth delegates to scope surface

- **WHEN** an operator invokes the `reauth` action
- **THEN** the handler routes the request through the Approvals module per
  `connector-lifecycle-ceremony/spec.md:18-19`
- **AND** on approval, the handler SHALL invoke the OAuth reauth flow per
  `connector-oauth-scope-surface/spec`'s `§Reauth endpoint contract for
  OAuth connectors` requirement (returns `{auth_url, state, expires_in}`
  for OAuth connectors; returns `{error: "unsupported", ...}` for non-OAuth
  connectors per the same spec's non-OAuth requirement)
- **AND** the handler SHALL NOT return HTTP 503 (the blocking-pending-spec
  condition is no longer met once both capabilities are ratified)

#### Scenario: Audit emission for reauth

- **WHEN** `reauth` is invoked
- **THEN** audit emissions SHALL follow
  `connector-oauth-scope-surface/spec`'s `§Audit trail` requirement (which
  itself is consistent with the audit-pair pattern from
  `connector-lifecycle-ceremony/spec.md:97-101`)

### Requirement: Reauth scenario is no longer "blocked" (modifies `connector-lifecycle-ceremony`)

The reauth handler SHALL NOT return HTTP 503 once `connector-oauth-scope-surface/spec` is ratified; the prior "Reauth is blocked" scenario from `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:36-40` is removed. Replacement scenarios live in `connector-oauth-scope-surface/spec` under `§Reauth endpoint contract for OAuth connectors` and `§Reauth endpoint contract for non-OAuth connectors`.

#### Scenario: Reauth executes per scope surface contract

- **WHEN** an operator invokes the `reauth` action AND
  `connector-oauth-scope-surface/spec` is ratified
- **THEN** the handler SHALL NOT return HTTP 503
- **AND** the handler's behavior SHALL conform to the OAuth or non-OAuth
  contract defined in `connector-oauth-scope-surface/spec` as appropriate

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) —
  `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 5 (git-tracked-config is identity) —
  `about/heart-and-soul/vision.md:86-98`
- Non-Negotiable Rule 7 (transport is connector responsibility) —
  `about/heart-and-soul/vision.md:110-115`
- Security model — credential authority and credential masking —
  `about/heart-and-soul/security.md:96-147`
- v1 scope — dashboard OAuth credential configuration —
  `about/heart-and-soul/v1.md:103-110`
- Blocking dependency declaration that this spec resolves —
  `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:4,17,36-40`
- Audit-pair pattern reused by this spec —
  `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:90-101`
- Credential masking constraint —
  `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:103-109`
- UI ground truth — `ReauthCallout` —
  `pr/overview/ingestion-redesign/ingestion-connector-detail.jsx:70-101`
- UI ground truth — `ScopeList` —
  `pr/overview/ingestion-redesign/ingestion-connector-detail.jsx:216-245`
- Spotify fixture (auth.status, scopes shape) —
  `pr/overview/ingestion-redesign/ingestion-connectors-data.jsx:97-121`
- Design language for serif italic notes / mono labels / state colors —
  `pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md:30,108-140,217-232`
- Existing Google scope-set registry pattern this spec generalizes —
  `openspec/specs/google-multi-account-oauth/spec.md:84-145`
- Existing `granted_scopes` precedent on `public.google_accounts` —
  `openspec/specs/google-account-registry/spec.md:22,150-162`
- Reference token-introspection implementation —
  `src/butlers/api/routers/oauth.py:164,1547-1620`
- Connector base spec (extension target for additive columns + Pydantic) —
  `openspec/specs/connector-base-spec/spec.md:319-348,381-419`
- Spotify connector OAuth scope declaration —
  `openspec/specs/connector-spotify/spec.md:229-247`
- Spotify dashboard's `needs_reauth` pattern this spec generalizes —
  `openspec/specs/dashboard-spotify-setup/spec.md:84-99`
- Credential storage contract this spec must not contradict —
  `openspec/specs/core-credentials/spec.md:52-99,200-223`
- Non-OAuth connector references for applicability matrix —
  `openspec/specs/connector-telegram-bot/spec.md`,
  `openspec/specs/connector-telegram-user-client/spec.md:111-137`,
  `openspec/specs/connector-owntracks/spec.md:47-105`
- Dashboard audit log helper (re-used for audit emissions) —
  `openspec/specs/dashboard-api/spec.md:530-541`
- Approvals dependency — `openspec/specs/module-approvals/spec.md`
- OpenSpec footer rule — `openspec/config.yaml:9-15`
