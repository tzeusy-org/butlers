## MODIFIED Requirements

### Requirement: Secrets Mutation Endpoints
The `/api/secrets/*` namespace SHALL expose mutation endpoints for every action the passport page can dispatch. Every mutation SHALL write to `public.audit_log` (see `core-credentials` Audit Action Enum requirement) with an appropriate action value.

#### Scenario: User credential mutations
- **WHEN** `POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>` is called
- **THEN** the response is `ApiResponse<{ redirect_url: str }>` whose `redirect_url` is an API-relative path (`/oauth/<provider>/start?...`, with no `/api` prefix — the dashboard API's mount point is deployment-specific, so the client prepends its own API base) that begins the OAuth dance with `page_of_origin=secrets` carried in the state token
- **AND** when the provider's OAuth app credentials are absent from the credential store the endpoint returns `503` naming the missing `*_OAUTH_CLIENT_ID` / `*_OAUTH_CLIENT_SECRET` keys and writes no `attempted` audit row, rather than handing back a start URL whose failure would render as a raw JSON page outside the dashboard
- **AND** `POST /api/secrets/user/<provider>/rotate?identity=<uuid>` with body `{ value }` returns `ApiResponse<UserSecret>` (updated) and writes an audit row with action `rotated`
- **AND** `POST /api/secrets/user/<provider>/disconnect?identity=<uuid>` returns `ApiResponse<{ status: "disconnected" }>` and writes an audit row with action `disconnected`
- **AND** `POST /api/secrets/user/<provider>/probe?identity=<uuid>` returns `ApiResponse<TestResult>`, writes one row to `public.secret_probe_log`, and writes one audit row with action `verified` (on ok) or `failed` (on fail)

#### Scenario: Spotify is excluded from generic User credential mutations
- **WHEN** a caller targets Spotify through a generic User credential mutation
- **THEN** `POST /api/secrets/user/spotify/reauthorize` SHALL NOT construct a
  generic OAuth redirect, state token, or callback journey
- **AND** generic User credential mutations SHALL NOT create, read, write,
  rotate, disconnect, or probe Spotify token material or a Spotify
  `public.entity_info` record
- **AND** those secured owner `public.entity_info` rows remain RFC 0006 Tier 2
  authority owned by the connector lifecycle and read through
  `resolve_owner_entity_info()`; excluding generic mutations does not make the
  Passport projection or `CredentialStore` a replacement secret authority
- **AND** the content-blind Spotify Passport projection SHALL delegate its
  connection and reauthorization action only to
  `POST /api/connectors/spotify/oauth/start` (with its connector-owned
  callback and lifecycle surfaces)

#### Scenario: System credential mutations
- **WHEN** `POST /api/secrets/system/<key>` is called with body `{ value, target: "shared" | "<butler>" }`
- **THEN** the response is `ApiResponse<SystemCredentialDetail>` (updated) — the same content-blind payload `GET /api/secrets/system/<key>` publishes for that row, built through the same explicit field-by-field projection of the router's internal read record, so a column added to the re-read query cannot reach a client without being consciously allowed through
- **AND** the response SHALL NOT contain a probe message (the cached `last_test_message` column or the probe row's free-text `message`), an audit note, or a `breaks[]` entry — a write that re-reads the row it just wrote MUST NOT republish evidence the read route on the same row already withholds
- **AND** audit evidence in that payload SHALL carry only `ts`, `actor`, and `action`; `breaks[]` is absent rather than published as an empty array, on the same grounds the read endpoints drop it (each entry would carry a free-text feature label and raw OAuth scopes, and nothing populates it)
- **AND** `key`, `category`, and `description` continue to be published, unchanged from the read route: they are the operator-authored naming of an infrastructure key, and withholding them from the write response alone would split the contract for one row across two endpoints without closing a leak
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
