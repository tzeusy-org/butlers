## MODIFIED Requirements

### Requirement: Defense-in-Depth API-Key Authentication (Opt-In, Not Fail-Closed)

Dashboard API-key authentication via `ApiKeyMiddleware` SHALL be a defense-in-depth layer, not the primary trust boundary. The primary control is network isolation: all Docker port mappings bind to `127.0.0.1` only, with external access mediated by Tailscale serve under tailnet-level authentication (`about/heart-and-soul/security.md`). Accordingly, the daemon SHALL NOT fail startup when `DASHBOARD_API_KEY` is unset.

When `DASHBOARD_API_KEY` is set, the middleware SHALL enforce it on every `/api/*` route except the public health paths (`/api/health`, `/health`): a request missing a matching `X-API-Key` header SHALL receive HTTP 401 in the standard error envelope, and header comparison SHALL use a constant-time compare. When `DASHBOARD_API_KEY` is unset, the middleware SHALL be a no-op pass-through so deployments relying solely on network isolation are unaffected.

The approved `dashboard-owner-auth` session is an additional way for a browser
to satisfy that middleware and every owner-control dependency; it does not
weaken network isolation or the configured header contract. The narrowly scoped
owner-session establishment and content-blind status surfaces are exempt from
presenting the header because establishment accepts the key once in a
structurally audit-exempt body over an exact allowed HTTPS Origin. They are not
exempt from their own authentication/origin contracts. The server SHALL never
expose or persist the submitted key in JavaScript.

When `DASHBOARD_API_KEY` is unset, the middleware remains the described no-op,
but the independent owner-auth boundary SHALL place the dashboard API in
`keyless_unenrolled` until fresh host authority completes the separately
adopted enrollment mechanism. Health/readiness, content-blind auth status, and
that minimal selected enrollment surface are the only unauthenticated API
exceptions after cutover. Same-origin, loopback, Tailscale reachability, and
first arrival SHALL NOT authenticate a visitor.

#### Scenario: Auth enforced when key is set

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests an `/api/*` route other than a public health path without a matching `X-API-Key` header
- **THEN** the API responds with HTTP 401 in the standard error envelope (`code: UNAUTHORIZED`)
- **AND** a request carrying the correct `X-API-Key` header is allowed through
- **AND** the narrow session establishment/status surfaces instead apply their own key-submission or content-blind status contract
- **AND** after establishment a valid owner session SHALL be allowed through without resending the key
- **AND** a valid cookie session SHALL be allowed through, with unsafe methods additionally requiring the `dashboard-owner-auth` CSRF contract

#### Scenario: Health paths bypass auth

- **WHEN** `DASHBOARD_API_KEY` is set and a client requests `/api/health` or `/health` without an `X-API-Key` header or owner session
- **THEN** the request is allowed through (health/readiness probes are always public)
- **AND** that result SHALL NOT create or imply owner authentication

#### Scenario: Unset key is a no-op pass-through, startup succeeds

- **WHEN** `DASHBOARD_API_KEY` is unset
- **THEN** the daemon starts successfully (NOT fail-closed)
- **AND** `ApiKeyMiddleware` passes every request through without an auth check
- **AND** the dashboard relies on the localhost-binding + Tailscale network-isolation boundary as the primary control
- **AND** the independent owner-auth boundary SHALL keep dashboard API data and controls unavailable in `keyless_unenrolled` except for health/readiness, content-blind auth status, and the later-selected minimal enrollment surface
- **AND** the first or same-origin visitor SHALL receive no owner session until a trusted host-local action authorizes enrollment

## Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): one user, one instance, full sovereignty.
- `about/heart-and-soul/security.md`: deployment network boundary and credential non-disclosure.
- RFC 0007 (`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`): Dashboard API surface and standard error envelope.
- RFC 0008 (`about/legends-and-lore/rfcs/0008-deployment-network-security.md`): loopback binding and external HTTPS termination.
- `dashboard-owner-auth` REQ-dashboard-owner-auth-001 through REQ-dashboard-owner-auth-005.
