## ADDED Requirements

### Requirement: Server-managed single-owner dashboard authentication

The dashboard SHALL authenticate one owner principal through either a matching
configured `DASHBOARD_API_KEY` in `X-API-Key` or a valid server-managed owner
session. Same-origin reachability, source address, request order, Host or
forwarded headers, frontend asset access, public status data, OAuth state,
connector callbacks, and the existence of an owner entity SHALL NOT establish
that principal.

When `DASHBOARD_API_KEY` is configured, non-browser callers SHALL retain the
exact header contract and constant-time comparison. A browser MAY submit that
key once as exact body `{api_key: string}` (`extra="forbid"`, bounded before
decode) to `POST /api/auth/owner/session` over an approved HTTPS origin to
establish a session, but the raw key
SHALL NOT be returned, placed in a URL, bundled, logged, audited, cached, sent
to telemetry, or stored in JavaScript-readable persistence.

A server session SHALL use an opaque token with at least 256 bits of entropy.
The server SHALL retain only its digest and bounded metadata. The cookie SHALL
use a `__Host-` name and `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, no
`Domain`, and an absolute non-sliding lifetime no greater than 12 hours. Session
issuance SHALL rotate any presented session identifier. `X-API-Key` SHALL never
be derivable from a session or session record.

ID: REQ-dashboard-owner-auth-001
Source: owner decision owner-auth-keyless-bootstrap Choice A; heart-and-soul/security.md; RFC 0007; RFC 0008; dashboard-admin-gateway Defense-in-Depth API-Key Authentication
Scope: v1-mandatory

#### Scenario: Configured key establishes a protected browser session

- **WHEN** a browser on an approved HTTPS origin submits the matching configured key to the session-establishment surface
- **THEN** the server SHALL compare it in constant time and issue an opaque server-managed owner session with the required cookie attributes and absolute expiry
- **AND** it SHALL clear or replace any caller-supplied session identifier
- **AND** neither the response nor browser persistence SHALL contain the raw key

#### Scenario: Configured-key header callers remain compatible

- **WHEN** a non-browser caller supplies the matching `X-API-Key`
- **THEN** the central dashboard and owner-control boundaries SHALL authenticate the same single owner principal without requiring a cookie or CSRF token
- **AND** missing or mismatched header authority SHALL retain the existing fixed unauthorized result

#### Scenario: Same-origin and first arrival are not authentication

- **WHEN** an unauthenticated request is same-origin, originates from loopback or an allowed network, or is the first request received by an unconfigured instance
- **THEN** none of those facts SHALL create an owner, session, enrollment authority, or authenticated principal
- **AND** protected data and actions SHALL remain unavailable

#### Scenario: Browser storage and built assets contain no owner key

- **WHEN** the frontend is built or a browser session is established, refreshed, expired, revoked, or rejected
- **THEN** no dashboard key SHALL appear in localStorage, sessionStorage, IndexedDB, query/service-worker cache, a URL, source map, built asset, response body, log, audit row, metric, or trace

### Requirement: Host-authorized keyless first-owner enrollment

When `DASHBOARD_API_KEY` is absent, the dashboard SHALL start in
`keyless_unenrolled` and SHALL issue no owner session until a trusted host-local
operator action creates or approves fresh enrollment authority. The concrete
host-authority transport SHALL remain disabled until the owner separately
selects and adopts one of the alternatives in this change's design. No HTTP
visitor SHALL be able to arm or approve its own authority.

Every adopted mechanism SHALL scope authority to one instance, one current
enrollment epoch, one completion, and an expiry no more than 10 minutes after
host authorization. A bearer proof, if selected, SHALL have at least 256 bits
of randomness and SHALL be stored only as a digest. Authority consumption,
durable consumed receipt, enrolled-state transition, and at most one session
issuance SHALL commit atomically. The consumed receipt SHALL survive restart and
proof expiry long enough to reject replay deterministically.

Malformed, expired, unapproved, wrong-instance, wrong-epoch, unknown, or
already-consumed authority SHALL create no owner or session and disclose no
authority detail. Concurrent equal claims SHALL produce at most one enrolled
transition and one session. Recovery SHALL require a fresh host-local action,
increment the epoch, and revoke all prior sessions and pending authority before
one new session can issue.

ID: REQ-dashboard-owner-auth-002
Source: heart-and-soul/security.md Trust Premise and Deployment Security; RFC 0008 Host Port Binding; design.md D3-D4 and remaining Choice E1
Scope: v1-mandatory

#### Scenario: Host-authorized first enrollment succeeds once

- **WHEN** an operator controlling the trusted host authorizes one fresh first-owner enrollment through the separately adopted mechanism and the intended browser completes it before expiry
- **THEN** the server SHALL atomically consume that authority, transition the current epoch to `keyless_enrolled`, and issue at most one owner session
- **AND** no subsequent completion of the same authority SHALL issue a session

#### Scenario: Arbitrary first visitor gains no authority

- **WHEN** an arbitrary visitor loads the frontend, reads public auth status, or submits the first HTTP enrollment-related request
- **THEN** the instance SHALL remain `keyless_unenrolled` unless a separate host-local operator action authorizes the selected mechanism
- **AND** the visitor SHALL receive no owner session, proof, approval, or protected data from arrival order or reachability alone

#### Scenario: Concurrent claims have one atomic winner

- **WHEN** two requests race to consume the same valid host authority
- **THEN** one atomic transaction SHALL create at most one enrolled transition, one consumed receipt, and one owner session
- **AND** every loser SHALL receive a fixed content-blind already-consumed or unauthorized result and no session

#### Scenario: Replay remains denied after restart

- **WHEN** consumed authority is submitted again before or after a dashboard process restart
- **THEN** the durable receipt SHALL reject it before owner/session creation
- **AND** restart SHALL NOT reopen or extend the authority

#### Scenario: Expired or malformed proof changes nothing

- **WHEN** authority is malformed, expired, unapproved, unknown, bound to another instance or epoch, or otherwise unverifiable
- **THEN** the server SHALL fail closed without changing enrollment/session state
- **AND** no response or evidence surface SHALL disclose the proof, digest, challenge secret, or validation detail

#### Scenario: Lost-session recovery returns to host authority

- **WHEN** all usable owner sessions are lost, expired, or revoked
- **THEN** no unauthenticated browser reset SHALL be available
- **AND** a fresh host-local authorization SHALL increment the enrollment epoch, revoke all prior sessions and pending authority, and permit at most one new session

### Requirement: Cookie-backed mutations require synchronizer CSRF protection

Every unsafe owner request authenticated through the session cookie SHALL
require an independent synchronizer token in `X-CSRF-Token` and an exact match
between the request `Origin` and a finite configured HTTPS allowlist. The CSRF
token SHALL be random, bound to one session, retained by the server only as a
digest, and held by the frontend only in page memory. It SHALL NOT be the owner
credential or enter persistent browser storage. Authenticated
`GET /api/auth/owner/csrf` MAY issue a replacement after page reload, with
`Cache-Control: no-store`. This endpoint is the sole bounded exception to exact
`Origin` validation because supported browsers do not reliably attach `Origin`
to a same-origin GET. It SHALL instead require the valid Strict session cookie,
an HTTPS request authority exactly matching one configured origin,
`Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: cors`, and
`Sec-Fetch-Dest: empty`; reject redirects; and emit no permissive CORS header.
Only explicitly trusted proxy metadata may determine effective scheme and
authority; arbitrary `Forwarded` or `X-Forwarded-*` values SHALL NOT. Its only
state effect SHALL be inserting or replacing a bounded CSRF digest; it SHALL
read no domain data and perform no owner action. Its response SHALL contain
only the new token and expiry. The server SHALL retain at most four active
CSRF-token digests per session, each expiring within 30 minutes and never later
than its session, so bounded concurrent tabs remain usable.

`SameSite=Strict`, CORS, and content type SHALL be defense in depth and SHALL NOT
replace token validation. On unsafe methods, a missing, `null`, wildcard, HTTP,
malformed, or mismatched Origin or a missing/mismatched token SHALL fail before
body buffering, pool access, or mutation. Safe cookie-backed reads require the
valid session; they require neither `Origin` nor a CSRF token because they
perform no mutation. The CSRF rehydration GET has the additional bounded
Fetch-Metadata and effective-HTTPS checks above because it issues a mutation
capability and updates its digest set. Header-authenticated `X-API-Key` requests
SHALL not require CSRF. Session establishment uses the matching key or adopted
host authority plus exact HTTPS Origin, never same-origin alone. Logout and
browser revocation SHALL require CSRF.

ID: REQ-dashboard-owner-auth-003
Source: owner decision owner-auth-keyless-bootstrap Choice A; craft-and-care/security-and-secrets.md; design.md D2
Scope: v1-mandatory

#### Scenario: Valid cookie mutation carries both CSRF proofs

- **WHEN** an owner session submits an unsafe request with the matching in-memory `X-CSRF-Token` and exact allowed HTTPS Origin
- **THEN** the central boundary SHALL admit it to the route's ordinary authorization and validation
- **AND** token success SHALL NOT bypass any route-specific check

#### Scenario: Cross-origin and token failures precede mutation

- **WHEN** a cookie-backed unsafe request has a missing, `null`, wildcard, HTTP, malformed, or mismatched Origin or a missing/mismatched CSRF token
- **THEN** the request SHALL be rejected before body buffering, database access, protected-state observation, audit attribution, or side effect

#### Scenario: Header callers do not inherit ambient-cookie CSRF rules

- **WHEN** a non-browser request authenticates with a matching `X-API-Key` and does not rely on a session cookie
- **THEN** it SHALL not require `X-CSRF-Token`
- **AND** ordinary route authorization, input validation, and audit rules SHALL still apply

#### Scenario: SameSite and CORS cannot substitute for the token

- **WHEN** a request would pass SameSite cookie handling or CORS policy but lacks the matching synchronizer token for an unsafe cookie-backed method
- **THEN** it SHALL be rejected without a mutation

#### Scenario: Reload rehydrates CSRF through one bounded GET exception

- **WHEN** a supported browser reloads an authenticated page and fetches `GET /api/auth/owner/csrf` without an `Origin` header
- **THEN** the request MAY succeed only with the valid Strict session cookie, exact configured HTTPS authority, and `Sec-Fetch-Site: same-origin`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Dest: empty`
- **AND** the response SHALL be no-store, contain only the replacement token and expiry, permit no redirect or permissive CORS response, and perform no domain read or owner action
- **AND** a cross-site navigation/fetch, untrusted forwarded authority, missing/wrong Fetch Metadata, or non-HTTPS request SHALL receive no token

### Requirement: Auth state, expiry, revocation, restart, and recovery fail closed

The server SHALL persist exactly one closed auth state from `configured_key`,
`keyless_unenrolled`, `keyless_enrolled`, or `unavailable`, plus a monotonic
authentication/enrollment epoch. Unknown values, duplicate active owner state,
inconsistent epochs, unreadable state, unavailable session storage, or an
internally contradictory configuration SHALL map to `unavailable`, never to an
authenticated or pass-through state.

An ordinary restart SHALL preserve unexpired, unrevoked sessions without
extending their absolute expiry and SHALL preserve consumed enrollment receipts.
Configured-key rotation SHALL increment the auth epoch and revoke sessions from
the prior key generation. Removing a configured key SHALL enter
`keyless_unenrolled` and SHALL NOT reactivate historical keyless sessions or
proofs. Adding a configured key SHALL revoke keyless sessions and enter
`configured_key` after restart.

An authenticated owner SHALL be able to revoke the current or all sessions. The
selected host-authority mechanism SHALL provide emergency all-session
revocation without a browser session. Public status MAY expose only the closed
state, whether the requesting browser is authenticated, and that browser's own
expiry. It SHALL expose no credential/proof/token/digest, owner/contact identity,
visitor history, session count, configuration path, or failure tail.

The browser session surfaces SHALL be `GET /api/auth/owner/status`, `POST` and
`DELETE /api/auth/owner/session`, `GET /api/auth/owner/csrf`, and
`DELETE /api/auth/owner/sessions`. The two DELETE routes SHALL accept either a
valid `X-API-Key` or cookie authority with CSRF; the singular route revokes the
current session and the plural route revokes all sessions.

ID: REQ-dashboard-owner-auth-004
Source: heart-and-soul/security.md Credential Management and Deployment Security; RFC 0008; design.md D4
Scope: v1-mandatory

#### Scenario: Restart preserves only valid bounded state

- **WHEN** the dashboard restarts with readable consistent auth storage
- **THEN** an unexpired and unrevoked session SHALL remain valid only until its original absolute expiry
- **AND** consumed enrollment authority SHALL remain consumed

#### Scenario: Corrupt or unavailable state is not an empty default

- **WHEN** auth state is unknown, duplicated, internally inconsistent, unreadable, or its storage is unavailable
- **THEN** protected requests SHALL return a fixed unavailable response and public status SHALL report only `unavailable`
- **AND** no path SHALL treat the condition as keyless-unenrolled, authenticated, or pass-through

#### Scenario: Key removal cannot revive prior keyless authority

- **WHEN** a configured key is removed and the process restarts
- **THEN** every configured-key session SHALL be revoked and the instance SHALL enter `keyless_unenrolled`
- **AND** no historical keyless session, proof, challenge, or owner state SHALL reactivate

#### Scenario: Key addition or rotation fences prior sessions

- **WHEN** a key is added or its value changes and the process restarts
- **THEN** the authentication epoch SHALL advance and every session from the prior mode or key generation SHALL be rejected
- **AND** the matching new `X-API-Key` SHALL retain its configured-key contract

#### Scenario: Revocation is immediate and recoverable

- **WHEN** an authenticated owner or trusted host operator revokes the current or all sessions
- **THEN** affected session digests SHALL be rejected immediately without waiting for cookie expiry
- **AND** recovery without another valid session SHALL require fresh host authority rather than an unauthenticated browser reset

#### Scenario: Page reload obtains bounded replacement CSRF authority

- **WHEN** an authenticated browser reloads and requests `GET /api/auth/owner/csrf` through the bounded Fetch-Metadata and exact-HTTPS-authority exception
- **THEN** the server SHALL return one no-store CSRF token and expiry without returning session or owner identity
- **AND** it SHALL retain no more than four active CSRF digests for that session, each bounded by 30 minutes and the session expiry

### Requirement: Every owner-gated browser route uses the central boundary

In keyless mode, the dashboard SHALL admit no API request except public health,
a content-blind owner-auth status read, and the exact minimal enrollment surface
selected in a later adopted amendment until a valid owner session exists. Static
frontend assets MAY load but SHALL confer no data or action authority.

Every route tagged or specified as owner-only SHALL pass the centralized owner
authentication boundary before body buffering, database-pool acquisition,
protected-state observation, or a domain owner/contact assertion. The inventory
in `design.md` SHALL be covered at implementation time, including Models and
Spend attention, model Test/Verify, Home presence settings, prompt overlay and
mode, conversation ingress recovery, terminal-action inspection/resolution,
memory dead-letter requeue, dashboard briefing, System egress, relationship
entity PII/mutations, and owner-operated credential mutations. In particular,
the mounted `POST /api/secrets/cli/{credential_id:path}/rotate` SHALL pass the
central boundary before it reads a body, credential row, or generation state;
its separately sanctioned one-time response remains governed by
REQ-dashboard-owner-auth-006. A domain assertion that an owner entity exists
SHALL remain additive and SHALL not authenticate the HTTP caller.

Mounted route metadata and a route-introspection contract test SHALL make future
owner-only routes fail when they omit the centralized boundary. Public health,
connector-scoped callbacks, OAuth state, and private service-control credentials
SHALL remain narrowly scoped and SHALL not create an owner session.

ID: REQ-dashboard-owner-auth-005
Source: RFC 0007; dashboard-model-settings REQ-dashboard-model-settings-001/002; runtime-attention-outbox REQ-runtime-attention-outbox-003; dashboard-relationship Clause 12; system-overview-page System Page Privacy Contract; active owner-control capability changes inventoried in design.md
Scope: v1-mandatory

#### Scenario: Every inventoried owner route authenticates before access

- **WHEN** any implemented or already-specified owner-gated browser route receives a request
- **THEN** the centralized boundary SHALL authenticate the configured-key header or server session before body buffering, pool acquisition, protected reads, or domain owner assertions
- **AND** a missing, expired, revoked, or unavailable authority SHALL expose no protected data and perform no action

#### Scenario: Domain owner existence is not caller identity

- **WHEN** a route can prove that the database contains an owner-role entity or owner contact
- **THEN** that fact SHALL NOT satisfy or bypass the central HTTP owner-authentication boundary
- **AND** the route's domain assertion SHALL run only after transport authentication succeeds

#### Scenario: Keyless unenrolled dashboard exposes no API data

- **WHEN** the instance is `keyless_unenrolled`
- **THEN** only health/readiness, content-blind auth status, and the later-selected minimal enrollment surface SHALL be reachable without a session
- **AND** loading static assets or being the first visitor SHALL not expose dashboard API data

#### Scenario: Future owner route cannot omit the boundary

- **WHEN** a new mounted route is tagged or specified as owner-only without the centralized dependency
- **THEN** the route-introspection contract gate SHALL fail before merge

### Requirement: Owner-auth issuance and absence evidence use exact allowlists

Successful `POST /api/auth/owner/session` and the later-selected keyless
completion SHALL emit owner-auth material only as: (1) the opaque session token
in one `Set-Cookie` header carrying every attribute in
REQ-dashboard-owner-auth-001, and (2) response data containing exactly
`csrf_token`, `csrf_expires_at`, and `session_expires_at`. Successful
`GET /api/auth/owner/csrf` SHALL return response data containing exactly
`csrf_token` and `csrf_expires_at`. All three responses SHALL set
`Cache-Control: no-store`. Status, denial, conflict, expiry, replay, logout, and
revocation responses SHALL contain none of those materials.

Privacy verification SHALL seed distinct non-secret fixture sentinels for the
submitted dashboard key, host proof or challenge authority, issued session,
issued CSRF token, stored digests, and owner identity. It SHALL positively prove
that the issued session and CSRF sentinels appear in only the exact allowlisted
locations above, then prove every other sentinel absent from every other
response, audit event, log, metric, trace, prompt, MCP surface, connector event,
notification, built frontend asset, source map, and service-worker cache. The
test SHALL first prove each sink and positive issuance path was exercised, so an
empty capture cannot satisfy the absence assertion.

The canonical one-time credential result from
`POST /api/secrets/cli/{credential_id:path}/rotate` SHALL retain its separate
successful response allowlist of exactly the already-specified `value` and
display `fingerprint`. That allowlist SHALL contain no dashboard key, host
authority, owner session, CSRF token, auth digest, or owner identity. The
rotated credential SHALL remain absent from every other response and evidence
sink. No other credential endpoint or status code inherits this exception.

ID: REQ-dashboard-owner-auth-006
Source: heart-and-soul/security.md credential non-disclosure; dashboard-api Secrets Mutation Endpoints; generation-fenced-codex-auth-rotation-provenance Dashboard Codex Mutations; design.md D7
Scope: v1-mandatory

#### Scenario: Session issuance emits only the cookie and CSRF tuple

- **WHEN** configured-key or adopted keyless session establishment succeeds
- **THEN** the session token SHALL appear only in the exact `Set-Cookie` header and response data SHALL contain exactly `csrf_token`, `csrf_expires_at`, and `session_expires_at`
- **AND** the response SHALL be no-store and SHALL contain no submitted key, host authority, digest, owner identity, or additional auth field

#### Scenario: CSRF rehydration emits only its bounded tuple

- **WHEN** the bounded CSRF rehydration GET succeeds
- **THEN** response data SHALL contain exactly `csrf_token` and `csrf_expires_at`, with no session token or owner identity
- **AND** the response SHALL be no-store

#### Scenario: Absence assertions cannot pass vacuously

- **WHEN** privacy verification exercises issuance and every named evidence sink with distinct fixture sentinels
- **THEN** it SHALL first assert the two allowed token outputs positively
- **AND** it SHALL assert every key, proof/challenge, digest, and identity sentinel absent everywhere, plus session and CSRF sentinels absent outside their exact allowlists

#### Scenario: CLI rotate keeps only its existing one-time exception

- **WHEN** `POST /api/secrets/cli/{credential_id:path}/rotate` succeeds under its canonical contract
- **THEN** only that response may contain its newly issued credential `value` and display `fingerprint`
- **AND** it SHALL contain no owner-auth material, and the rotated credential SHALL be absent from every other response and evidence sink

### Requirement: Implementation, adoption, and real-world effects remain separately gated

This specification SHALL NOT select a keyless proof transport or HTTPS entry
point. The default SHALL remain no keyless enrollment/cutover until the owner
selects both choices in `design.md`, an amendment specifies their exact surfaces
and threat model, independent semantic/security review passes on that exact
head, and the owner separately adopts it.

Implementation SHALL then require mounted full-app API tests, real-PostgreSQL
replay/concurrency/restart tests, exact HTTPS Compose browser tests, CSRF tests,
route-introspection coverage, rollback rehearsal, and positive-allowlist plus
absence-sentinel privacy evidence. Mock-only, dependency-override-only,
source-text-only, or UI-only evidence SHALL not satisfy the contract.

Review, adoption, implementation, migration, proof/key generation, credential
access, provisioning, browser enrollment, merge, queue entry, deployment,
restart, rollback, runtime verification, archive, and release SHALL remain
distinct authorized acts.

ID: REQ-dashboard-owner-auth-007
Source: craft-and-care/review-and-documentation.md; craft-and-care/testing-and-verification.md; craft-and-care/security-and-secrets.md; design.md Future verification seams and Adoption boundary
Scope: v1-mandatory

#### Scenario: No mechanism is selected by this draft

- **WHEN** this exact draft is reviewed but the owner has not selected and adopted E1 and E2
- **THEN** no keyless enrollment surface, proof generation, HTTPS topology change, or cutover SHALL be implemented or activated

#### Scenario: Future verification exercises real boundaries

- **WHEN** an adopted implementation claims completion
- **THEN** evidence SHALL exercise the mounted API/middleware stack, real database concurrency and restart, selected HTTPS Compose browser path, CSRF attacks, every owner route, and content-blind outputs
- **AND** mock-only, source-scan-only, or UI-only checks SHALL not satisfy those claims

#### Scenario: Technical success grants no operational authority

- **WHEN** a future implementation passes review and terminal hosted CI
- **THEN** that result SHALL NOT authorize credential access, proof generation, provisioning, enrollment, deployment, restart, rollback, runtime exercise, archive, or release

## Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): one user, one instance, full sovereignty.
- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): authentication and lifecycle decisions remain deterministic infrastructure.
- `about/heart-and-soul/security.md`: trusted host, untrusted external callers, credential non-disclosure, and localhost/Tailscale deployment boundary.
- RFC 0007 (`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`): mounted API, error envelope, browser shell, and owner-only dashboard surfaces.
- RFC 0008 (`about/legends-and-lore/rfcs/0008-deployment-network-security.md`): loopback binding and Tailscale HTTPS boundary.
- `about/craft-and-care/security-and-secrets.md`: explicit privilege boundaries and no secret leakage.
- `openspec/specs/dashboard-design-language/spec.md`: honest states, accessible controls, restrained copy, and repeat-safe interaction.
