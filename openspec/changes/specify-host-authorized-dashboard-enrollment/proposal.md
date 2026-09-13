# Specify host-authorized first-owner dashboard enrollment

## Why

At baseline `d8b1924635a04f3159dffa5e3e47e83633e79693`, the default Compose
configuration leaves `DASHBOARD_API_KEY` empty. `ApiKeyMiddleware` therefore
passes every request through, while the narrower `require_dashboard_owner_control`
dependency refuses sensitive routes with `503`. The browser has no supported way
to satisfy that dependency: it sends no `X-API-Key`, and several active capability
changes add more owner-only routes on top of the same missing boundary.

The owner has already applied `owner-auth-keyless-bootstrap` Choice A in
`bu-pb6oy`: a configured-key browser session uses a server-managed, expiring,
revocable `HttpOnly; Secure; SameSite=Strict` cookie with CSRF protection;
non-browser callers retain `X-API-Key`; the raw key is never stored by JavaScript
or bundled into frontend assets; and same-origin reachability is not
authentication. That decision did not choose how an instance with no configured
key proves that its first browser belongs to the host operator.

This change supplies the missing contract. It makes host authorization, not
arrival order, the root of keyless owner enrollment and leaves the concrete
host-proof transport as an explicit owner decision. It changes no source,
configuration, credential, runtime, or deployment state.

## What Changes

- Define two dashboard authentication modes: configured-key and keyless. A
  configured key remains valid through `X-API-Key` for non-browser clients and
  can establish the approved server-managed browser session. An absent key no
  longer implies that the first request, a loopback address, or same-origin
  reachability is the owner.
- Define a mechanism-neutral first-owner state machine rooted in an explicit
  host-local operator action. Any adopted mechanism must use expiring,
  single-use host authority, atomically consume it with session issuance, reject
  replay and concurrent duplicate claims, and remain content-blind.
- Define durable session, enrollment-epoch, expiry, revocation, restart,
  recovery, malformed-state, key-rotation, and rollback semantics.
- Fix the already-approved configured-key browser contract: an opaque session
  cookie is server-managed and inaccessible to JavaScript; unsafe cookie-backed
  requests require an independent synchronizer CSRF token and exact allowed
  origin; raw dashboard keys never enter browser persistence, logs, telemetry,
  audit, URLs, or built assets.
- Inventory the implemented and already-specified owner-gated browser routes.
  The central authentication boundary must run before every route-specific
  owner/contact assertion, body read, pool acquisition, or protected-state
  observation.
- Amend the whole canonical `Defense-in-Depth API-Key Authentication` requirement
  without dropping any baseline scenario. The API-key middleware remains an
  opt-in layer; when it is a no-op in keyless mode, the independent owner-session
  boundary still fails closed.
- Record the remaining owner choices without selecting one: transferable
  one-time host code versus host-side approval of a browser challenge, and the
  HTTPS entry point required for a `Secure` cookie in the default Compose flow.
- Name future API/session, Compose, browser, real-PostgreSQL concurrency, CSRF,
  restart, rollback, and content-blind security evidence. No mock-only or
  source-text-only result can prove the trust boundary.

## Capabilities

### New Capabilities

- `dashboard-owner-auth`: server-managed single-owner browser sessions and a
  host-authorized, mechanism-neutral keyless first-owner enrollment contract.

### Modified Capabilities

- `dashboard-admin-gateway`: preserve configured-key header clients while
  accepting the approved browser session and ensuring that an unset API key does
  not bypass the independent owner-authentication boundary.
- `dashboard-relationship`: preserve every owner-role and PII endpoint gate while
  allowing an adopted non-development keyless deployment to start only in
  protected `keyless_unenrolled`, never in absent-key pass-through.

## Impact

- Future API/auth seams: dashboard auth middleware, `require_dashboard_owner_control`,
  server-side session/enrollment storage, CSRF validation, and content-blind auth
  status/session routes.
- Future frontend seams: a shell-level authentication gate and the already
  existing Models, Spend, Home presence, prompt-overlay, conversation-recovery,
  terminal-action, relationship, briefing, and System owner-only surfaces.
- Future deployment seams: default and hot-reload Compose HTTPS reachability,
  server-held auth state, restart, configured-key rotation, and guarded rollback.
- Existing configured-key `X-API-Key` automation remains supported. Existing
  unauthenticated clients in keyless deployments are intentionally not promised
  compatibility after cutover; they must configure the key or use an explicitly
  adopted successor credential contract.
- No implementation, key generation, credential access, provisioning, browser
  enrollment, database/schema change, merge, queue action, deployment, restart,
  or runtime exercise is authorized by this draft. Owner adoption of an exact
  independently reviewed artifact is separate, and adoption still does not
  authorize implementation or release.
