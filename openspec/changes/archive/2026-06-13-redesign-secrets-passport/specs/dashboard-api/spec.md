# dashboard-api

## ADDED Requirements

### Requirement: Secrets Inventory and Per-Credential Read Endpoints
The dashboard API SHALL expose a `/api/secrets/*` namespace that backs the passport-book `/secrets` page. All endpoints conform to the `ApiResponse<T>` envelope contract (RFC 0007 §Response Envelope); list/aggregate endpoints embed nested arrays inside `data`, never as top-level fields.

#### Scenario: Inventory endpoint shape
- **WHEN** `GET /api/secrets/inventory?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ cli: CliRuntime[], system: SystemSecret[], user: UserSecret[] }>` with `meta` containing severity counts and the `needs_hand_count` field
- **AND** the `?identity=` query parameter filters the `user` array to credentials associated with the specified entity (projection-lens semantics; see `butler-secrets`)
- **AND** when `?identity=` is omitted, the owner identity is used as the default
- **AND** every credential row includes `state`, `fingerprint` (sha256 first-8 hex, computed on-read, never persisted), and per-family identity (`provider` / `key` / `id`)
- **AND** the response does NOT include any raw secret values

#### Scenario: Per-credential read endpoints
- **WHEN** `GET /api/secrets/user/<provider>?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<UserSecret>` with the full evidence payload: `state`, `fingerprint`, `issued`, `expires`, `last_verified`, `last_used`, `scopes_required`, `scopes_granted`, `feeds`, `failure_tail`, `breaks[]`, `test` (most recent `TestResult`), `audit[]` (last 10), and `webhook` (when `kind=webhook`)
- **AND** `GET /api/secrets/system/<key>` returns `ApiResponse<SystemSecret>` with `key`, `category`, `row_state` (one of `shared` / `local` / `missing`), `fingerprint`, `description`, `source`, `target`, `last_verified`, `used_by[]`, `breaks[]`, `test`, `audit[]`
- **AND** `GET /api/secrets/cli/<id>` returns `ApiResponse<CliRuntime>` with `id`, `label`, `fingerprint`, `state`, `issued`, `expires`, `last_used`, `scopes_required`, `scopes_granted`, `test`
- **AND** none of these endpoints return raw secret values; values are returned only by explicit mutation endpoints in the specific cases defined below

#### Scenario: Probe-log LRU integration
- **WHEN** any per-credential read endpoint computes the `test` field
- **THEN** the field is sourced from the most recent row in `public.secret_probe_log` matching `(credential_scope, credential_key)` ordered by `recorded_at DESC`
- **AND** the `at` field is server-formatted to a human-friendly relative timestamp (e.g. `"14:21 today"`, `"yesterday 09:08"`) before serialization
- **AND** when no probe has ever been recorded for the credential, `test` is `null`

### Requirement: Secrets Mutation Endpoints
The `/api/secrets/*` namespace SHALL expose mutation endpoints for every action the passport page can dispatch. Every mutation SHALL write to `public.audit_log` (see `core-credentials` Audit Action Enum requirement) with an appropriate action value.

#### Scenario: User credential mutations
- **WHEN** `POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ redirect_url: str }>` and the redirect URL begins the OAuth dance with `page_of_origin=secrets` carried in the state token
- **AND** `POST /api/secrets/user/<provider>/rotate?identity=<uuid>` with body `{ value }` returns `ApiResponse<UserSecret>` (updated) and writes an audit row with action `rotated`
- **AND** `POST /api/secrets/user/<provider>/disconnect?identity=<uuid>` returns `ApiResponse<{ status: "disconnected" }>` and writes an audit row with action `disconnected`
- **AND** `POST /api/secrets/user/<provider>/probe?identity=<uuid>` returns `ApiResponse<TestResult>`, writes one row to `public.secret_probe_log`, and writes one audit row with action `verified` (on ok) or `failed` (on fail)

#### Scenario: System credential mutations
- **WHEN** `POST /api/secrets/system/<key>` is called with body `{ value, target: "shared" | "<butler>" }`
- **THEN** the response is `ApiResponse<SystemSecret>` (updated)
- **AND** when `target = "shared"` the value is written to the switchboard's `butler_secrets` table; when `target = "<butler>"` an override row is created in that butler's `butler_secrets` table
- **AND** an audit row is written with action `set` (first-time create), `rotated` (existing key), or `overrode` (new override)
- **AND** `POST /api/secrets/system/<key>/probe` returns `ApiResponse<TestResult>` and writes to probe-log + audit as in the User probe
- **AND** `DELETE /api/secrets/system/<key>?target=<butler|shared>` removes the row and writes an audit row with action `disconnected` (or `revoked` for override removal)

#### Scenario: CLI runtime mutations
- **WHEN** `POST /api/secrets/cli/<id>/rotate` is called
- **THEN** the response is `ApiResponse<{ fingerprint: str, value: str }>` and the raw value is returned **once** in the response body (so the owner can copy it to their local config)
- **AND** an audit row is written with action `rotated`
- **AND** `POST /api/secrets/cli/<id>/revoke` returns `ApiResponse<{ status: "revoked" }>` and writes an audit row with action `disconnected`

#### Scenario: Mutation endpoints ignore `?identity=` for authorization
- **WHEN** any `/api/secrets/*` mutation is called with `?identity=<member-id>`
- **THEN** the endpoint validates that the credential exists for the given identity and mutates it
- **AND** the endpoint does NOT enforce that the caller has permission to act on the member's credential (v1 single-owner; projection-lens semantics)

### Requirement: Audit-History and Breaks-Catalogue Endpoints
The `/api/secrets/*` namespace SHALL expose two read-side endpoints supporting the StampRow audit display and the WhatBreaks affordance.

#### Scenario: Audit history endpoint
- **WHEN** `GET /api/secrets/audit/<scope>/<key>?limit=50` is called (where `scope ∈ {user, system, cli}`)
- **THEN** the response is `ApiResponse<AuditEvent[]>` with the most recent audit rows filtered to the credential
- **AND** each `AuditEvent` includes `ts` (server pre-formatted relative timestamp), `actor`, `action`, `note` (serif-italic; verbatim stored note, never LLM-generated)
- **AND** the default `limit` is 10; max is 50
- **AND** the response includes a `meta.deep_link` field pointing to `/audit-log?key=<canonical-key>` for the full reel

#### Scenario: Breaks-catalogue endpoint
- **WHEN** `GET /api/secrets/breaks-catalogue?provider=<p>` is called
- **THEN** the response is `ApiResponse<BreakEntry[]>` reading from `public.provider_feature_catalogue`
- **AND** each `BreakEntry` includes `butler`, `feature`, `severity` (one of `high` / `medium` / `low`), `required_scopes` (jsonb array)
- **AND** when `?provider=` is omitted, the endpoint returns the full catalogue keyed by provider in `meta.by_provider`

### Requirement: OAuth Per-Provider Generalisation
The existing `/api/oauth/*` namespace (currently Google-only per `src/butlers/api/routers/oauth.py:156-1893`) SHALL be generalised to accept a `<provider>` path segment. Provider scope-sets SHALL be resolved from each butler's `butler.toml` declaration. The `/api/oauth/google/*` endpoints SHALL continue to function unchanged (path generalisation is additive; existing routes resolve via `provider=google`).

#### Scenario: Generalised begin endpoint
- **WHEN** `GET /api/oauth/<provider>/start?redirect_uri=<uri>&account_hint=<hint>&force_consent=<bool>&page_of_origin=<page>` is called
- **THEN** the response is `ApiResponse<{ authorization_url: str }>`
- **AND** the `state` token carries the `page_of_origin` value so the callback can route the user back appropriately
- **AND** for `provider=google`, the response is identical to the pre-change behaviour of `/api/oauth/google/start`

#### Scenario: Generalised callback endpoint
- **WHEN** `GET /api/oauth/<provider>/callback?code=<code>&state=<state>` is invoked
- **THEN** the callback exchanges the code for tokens, persists them to the correct authoritative store (`butler_secrets` for system, `public.entity_info` for per-account user credentials per `about/heart-and-soul/security.md:107-127`), writes a `connected` audit row, and redirects the browser based on `state.page_of_origin`:
  - `secrets` → `/secrets?focus=u:<provider>&toast=connected`
  - `ingestion` → `/ingestion/connectors`
  - (default / missing) → `/secrets?focus=u:<provider>&toast=connected`

#### Scenario: Provider scope resolution from butler.toml
- **WHEN** the OAuth begin endpoint is called for a provider whose scopes are declared in one or more `butler.toml` files
- **THEN** the resolved scope-set is the union of all scopes declared by butlers that consume the provider
- **AND** the resolved scope-set is the value passed to the OAuth authorization URL

## MODIFIED Requirements

### Requirement: Generic Butler Secrets CRUD (compatibility)
The existing `/api/butlers/{name}/secrets/*` CRUD endpoints in `src/butlers/api/routers/secrets.py` SHALL continue to function unchanged for direct programmatic access to `butler_secrets`. The new `/api/secrets/*` namespace defined above is the surface for the redesigned `/secrets` page and is additive; existing API consumers of `/api/butlers/{name}/secrets/*` are not broken by this change.

#### Scenario: Legacy endpoint unchanged
- **WHEN** `GET /api/butlers/{name}/secrets` is called
- **THEN** the response shape and behaviour are unchanged from the pre-change spec (`dashboard-api §Secrets Management`)
- **AND** the endpoint continues to return metadata only (no raw values)

### Requirement: Audit Log Filter by Credential Key
The existing `/api/audit-log` endpoint (`src/butlers/api/routers/audit.py:210`) SHALL be extended with a `?key=<credential-key>` query parameter that filters `public.audit_log` rows where `target` matches the normalised credential key.

The canonical credential-key format MUST match the focus-key format used by the `/secrets` page: `u:<provider>`, `s:<KEY>`, `c:<id>`. The endpoint SHALL apply a normalisation function (defined in `core-credentials`) to match against existing `target` values written by other writers (e.g. older audit rows that used non-canonical formats).

#### Scenario: Filter by canonical key
- **WHEN** `GET /api/audit-log?key=u:google&limit=50` is called
- **THEN** the response is `PaginatedResponse<AuditLogEntry>` filtered to rows whose normalised target equals `u:google`
- **AND** the existing `?since=`, `?actor=`, `?action=`, and `?limit=` query parameters remain functional and combinable with `?key=`
- **AND** the response uses the existing `PaginatedResponse<T>` envelope (RFC 0007), not the `ApiResponse<T>` envelope

#### Scenario: Unknown key returns empty page
- **WHEN** `GET /api/audit-log?key=u:does-not-exist` is called
- **THEN** the response is an empty `PaginatedResponse` with `meta.total = 0` and `meta.has_more = false`

## ADDED Requirements

### Requirement: Response Envelope Conformance for `/api/secrets/*` and `/api/oauth/*`
All endpoints under the new `/api/secrets/*` namespace and the generalised `/api/oauth/*` namespace SHALL conform to the `ApiResponse<T>` envelope contract defined in RFC 0007 §Response Envelope (and codified in the existing `dashboard-api §Response Envelope Pattern` requirement). Endpoints MUST NOT expose top-level data fields outside the `data` / `meta` / `error` envelope shape.

The `/api/audit-log` endpoint extension (key-filter) uses the existing `PaginatedResponse<T>` envelope per the existing spec; the envelope is unchanged.

#### Scenario: Envelope conformance check
- **WHEN** any endpoint under `/api/secrets/*` or `/api/oauth/*` returns a 2xx response
- **THEN** the response body has the shape `{ data: <T>, meta: <object> }` (or the standard error envelope for non-2xx responses)
- **AND** no array or scalar is returned at the top level of the response body
