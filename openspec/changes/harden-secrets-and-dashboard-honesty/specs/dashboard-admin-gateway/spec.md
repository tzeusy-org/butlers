## ADDED Requirements

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

## REMOVED Requirements

### Requirement: Legacy Raw-Secret Reveal Endpoint

**Reason**: `GET /api/butlers/{name}/secrets/{key}/reveal` returned the raw stored secret value (`{"key": key, "value": value}`) over an unguarded GET on the legacy butler-scoped router, contradicting the doctrine that credentials must never appear in dashboard payloads (`about/heart-and-soul/security.md`) and the anti-pattern against logging/returning full credential values (`about/craft-and-care/security-and-secrets.md`). This endpoint predated the passport-book `/api/secrets/...` (`secrets_v2`) surface.

**Migration**: There is no replacement on the legacy `/api/butlers/...` router. Value reveal, where the product supports it, is served exclusively by the governed `secrets_v2`/passport surface defined in the `butler-secrets` capability (see its Evidence-Over-Value Affordance Contract, under which explicit reveal actions ship on credential pages that support them). Callers of the removed path receive HTTP 404. The removal is guarded against reintroduction by the route-introspection contract test under the "No Raw Secret Reveal on the Legacy Butler-Scoped Router" requirement.

> Note: this requirement is removed from this capability's contract as an explicit named entry to record the deletion; the legacy reveal endpoint was never an enumerated requirement under the prior `dashboard-admin-gateway` spec (it lived in code at `src/butlers/api/routers/secrets.py:158-176`). The REMOVED block documents the contractual intent that no such route exists going forward.

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance — the owner owns credentials/data; protecting them is the core threat model) — `about/heart-and-soul/vision.md:60-63`
- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; must be testable, debuggable, predictable — grounds the route-introspection contract test and the honest auth-status indicator) — `about/heart-and-soul/vision.md:80-84`
- Credentials must never appear in session logs or tool call payloads sent to the dashboard; "Logging full credential values" listed as an anti-pattern — `about/heart-and-soul/security.md:138-140, 293`
- Deployment Security: localhost (`127.0.0.1`) port binding + Tailscale serve as the primary trust boundary (API-key auth is defense-in-depth, not fail-closed) — `about/heart-and-soul/security.md:241-243, 272`
- Do not log raw secrets/refresh tokens; do not add casual env-var fallbacks — `about/craft-and-care/security-and-secrets.md:9, 12`
- Governed reveal surface (passport-book Evidence-Over-Value Affordance Contract; explicit reveal where supported) — `openspec/specs/butler-secrets/spec.md` (§Evidence-Over-Value Affordance Contract, §No Prototype Tweaks Chrome)
- Standard error envelope (`ApiResponse`/`ErrorResponse`) — RFC 0007 §Response Envelope
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
