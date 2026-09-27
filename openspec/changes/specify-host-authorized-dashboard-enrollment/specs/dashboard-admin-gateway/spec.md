## MODIFIED Requirements

### Requirement: Defense-in-Depth API-Key Authentication (Opt-In, Not Fail-Closed)

- Before the adopted owner-auth cutover, dashboard API-key authentication via `ApiKeyMiddleware` SHALL be a defense-in-depth layer, not the primary trust boundary. The primary control is network isolation: all Docker port mappings bind to `127.0.0.1` only, with external access mediated by Tailscale serve under tailnet-level authentication (`about/heart-and-soul/security.md`). Accordingly, the daemon SHALL NOT fail startup when `DASHBOARD_API_KEY` is unset.
- Before that cutover, when `DASHBOARD_API_KEY` is set, the middleware SHALL enforce it on every `/api/*` route except the public health method/path pairs (`/api/health`, `/health`): a request missing a matching `X-API-Key` header SHALL receive HTTP 401 in the standard error envelope, and header comparison SHALL use a constant-time compare. When `DASHBOARD_API_KEY` is unset, the middleware SHALL be a no-op pass-through so deployments relying solely on network isolation are unaffected.
- After the adopted successor cutover, `dashboard-owner-auth` supersedes the
legacy optional-header behavior above: network isolation remains mandatory and
Tailnet membership never authenticates the owner. Missing `DASHBOARD_API_KEY`
permits healthy `keyless_unenrolled` or `keyless_enrolled` startup, never public
dashboard-data admission. Host approval is bound to one browser intent; ordinary
passkey login after expiry/logout requires no host command. Only exact D4
method/path exemptions apply, including bounded public ceremonies and probes;
there is no path-prefix bypass. Browser configured-key submission is HTTPS-only,
structurally excluded from body logging and never retained by JavaScript.
- The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

ID: REQ-dashboard-admin-gateway-001
Source: dashboard-owner-auth successor design D1-D9; existing dashboard-admin-gateway behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

#### Scenario: Auth enforced when key is set

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests an `/api/*` route other than a public health method/path pair without a matching `X-API-Key` header or a valid owner session
- **THEN** the API responds with HTTP 401 in the standard error envelope (`code: UNAUTHORIZED`)
- **AND** a request carrying the correct `X-API-Key` header is allowed through
- **AND** the exact D4 ceremony/status surfaces instead apply their own key-submission or content-blind status contract
- **AND** after establishment a valid owner session SHALL be allowed through without resending the key
- **AND** a valid cookie session SHALL be allowed through, with unsafe methods additionally requiring the `dashboard-owner-auth` CSRF contract

#### Scenario: Health paths bypass auth

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests `GET /api/health` or `GET /health` without an `X-API-Key` header or owner session
- **THEN** the request is allowed through (health/readiness probes are always public)
- **AND** that result SHALL NOT create or imply owner authentication

#### Scenario: Unset key is a no-op pass-through, startup succeeds

- **WHEN** `DASHBOARD_API_KEY` is unset and authoritative auth state is healthy
- **THEN** the daemon starts successfully (NOT fail-closed)
- **AND** the legacy `ApiKeyMiddleware` passes every request through without an auth check only before successor cutover; after cutover the central boundary authenticates every protected request
- **AND** the dashboard relies on the localhost-binding + Tailscale network-isolation boundary as the primary network control, alongside mandatory central owner authentication after cutover
- **AND** the independent owner-auth boundary SHALL keep dashboard API data and controls unavailable in `keyless_unenrolled` except for health/readiness, content-blind auth status, and the exact D4 bounded ceremony surface
- **AND** the first or same-origin visitor SHALL receive no owner session until a trusted host-local action authorizes that browser intent and verified registration commits

### Requirement: Honest Auth-Status Health Indicator

- The dashboard SHALL surface an auth-status health indicator that honestly reports the system's security posture so the operator can see it without inspecting configuration. The indicator SHALL report at minimum: (1) whether API-key authentication is enabled (i.e. `DASHBOARD_API_KEY` is set), and (2) whether the data-export signing secret is operating on the known-insecure default. The indicator SHALL NOT reveal any secret value — it reports posture booleans only, consistent with the determinism contract (`about/heart-and-soul/vision.md` Rule 4) and the never-leak-credentials constraint.
- After successor cutover the API-key boolean SHALL remain an accurate key-mode
indicator, never a claim that owner authentication is disabled. The indicator
SHALL also report central owner-auth state using the exact closed state vocabulary
in `dashboard-owner-auth`; the public `/api/auth/owner/status` response remains
its separate exact D4 allowlist. Keyless enrolled sessions are protected even
when `api_key_auth_enabled` is false. No additional auth record, identifier or
credential count is exposed. Existing export-signer posture semantics remain.

ID: REQ-dashboard-admin-gateway-002
Source: dashboard-owner-auth successor design D1-D9; existing dashboard-admin-gateway behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

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
