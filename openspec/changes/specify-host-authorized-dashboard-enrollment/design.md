## Context

This design is grounded at source commit
`d8b1924635a04f3159dffa5e3e47e83633e79693`.

### Observed authentication seams

- `ApiKeyMiddleware` reads the effective `DASHBOARD_API_KEY` once during app
  construction. With a configured key it accepts only a matching `X-API-Key`
  for `/api/*` except health; with no key it is a complete pass-through.
- `require_dashboard_owner_control` independently reads the key for each call,
  returns `503` when absent and `401` when missing or mismatched, and accepts
  only `X-API-Key`.
- The browser API client sends neither `X-API-Key` nor an owner-session
  credential. The default Compose value is empty in both dashboard service
  shapes.
- RFC 0008 binds published ports to loopback and identifies Tailscale Serve as
  the external HTTPS boundary. The development frontend is ordinarily reached
  over plain HTTP, which cannot be treated as satisfying the approved `Secure`
  cookie contract without real browser evidence.
- Several domain gates assert that an owner record exists. That is a secondary
  authorization/data-integrity check, not proof that the HTTP caller controls
  the owner. The central session boundary must run first.

### Owner-gated browser route inventory

The global rule is broader than this list: after keyless cutover every
dashboard `/api/*` route requires a session except the three narrow exception
classes in D5. The inventory below is the complete additional owner-only set
named by current source, canonical specs, and unarchived deltas at the source
baseline. A future route tagged or specified as owner-only joins the set
automatically and must not depend on this hand-maintained list for enforcement.

| Contract/source | Method and route | Additional check |
| --- | --- | --- |
| Implemented `require_dashboard_owner_control` | `GET /api/settings/models/attention` | runtime-attention state |
| Implemented `require_dashboard_owner_control` | `POST /api/settings/models/attention/{episode_id}/reissue` | uncertain-state/idempotency |
| Implemented `require_dashboard_owner_control` | `GET /api/spend/runtime-attention` | fleet-halt state |
| `harden-runtime-auth-and-breaker-attention` | `POST /api/settings/models/{entry_id}/test` | catalog/probe control |
| `harden-runtime-auth-and-breaker-attention` | `POST /api/settings/models/verify-all` | rate/concurrency/probe control |
| `specify-home-presence-owner-entity-configuration` | `GET`, `PUT /api/home/settings/presence/owner-entities` | Home validation/CAS |
| `specify-roster-identity-owner-operations-overlay` | `GET`, `PUT /api/butlers/{name}/prompt` | roster/pool/overlay CAS |
| `specify-roster-identity-owner-operations-overlay` | `GET /api/butlers/{name}/prompt/history` | roster/pool/history |
| `specify-roster-identity-owner-operations-overlay` | `PUT /api/butlers/{name}/prompt/mode` | rollback-window CAS |
| `durable-dashboard-terminal-action-recovery` | `POST /api/butlers/{name}/conversation-turns/{message_id}/retry-ingress` | durable ingress fence |
| `durable-dashboard-terminal-action-recovery` | `GET /api/dashboard/terminal-actions/{id}` | action ownership/read model |
| `durable-dashboard-terminal-action-recovery` | `POST /api/dashboard/terminal-actions/{id}/resolve` | immutable resolution |
| `memory-honesty-last-mile` | `POST /api/memory/episodes/{episode_id}/requeue` | recovery eligibility/idempotency |
| canonical `dashboard-briefing` | `GET /api/dashboard/briefing` | owner-contact assertion/cache |
| canonical `system-overview-page` | `GET /api/system/egress` | owner-contact assertion |

Canonical `dashboard-relationship` Clause 12 adds this exact set. The central
session boundary runs before its owner-role assertion:

- `POST /api/relationship/entities`
- `POST /api/relationship/entities/{id}/merge`
- `POST /api/relationship/entities/{id}/archive`
- `POST /api/relationship/entities/{id}/promote-tier`
- `DELETE /api/relationship/entities/{id}`
- `POST /api/relationship/entities/queue/dismiss`
- `POST /api/relationship/entities/{id}/contacts`
- `DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}`
- `POST /api/relationship/entities/{id}/notes`
- `POST /api/relationship/entities/{id}/interactions`
- `POST /api/relationship/entities/{id}/gifts`
- `POST /api/relationship/entities/{id}/reach-out-drafts`
- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`
- `GET /api/relationship/plex/halo`

The canonical Secrets surface is owner-operated in v1 and the active
`generation-fenced-codex-auth-rotation-provenance` delta explicitly treats
Codex save/rotate, reauthorization/device-auth, probe, and revoke as owner
operations. Therefore the additional inventory also includes:

- `PUT`, `DELETE /api/butlers/{name}/secrets/{key}`
- `PUT`, `DELETE /api/oauth/google/credentials`
- `POST /api/secrets/user/{provider}/reauthorize`
- `POST /api/secrets/user/{provider}/rotate`
- `POST /api/secrets/user/{provider}/disconnect`
- `POST /api/secrets/user/{provider}/probe`
- `POST /api/secrets/system/{key}`
- `POST /api/secrets/system/{key}/probe`
- `DELETE /api/secrets/system/{key}`
- `POST /api/secrets/cli/{credential_id:path}/rotate`
- `POST /api/secrets/cli/{credential_id:path}/revoke`
- `POST /api/secrets/cli/{credential_id:path}/reauthorize`
- `POST /api/secrets/probe-all`

The CLI rotate path is spelled with `{credential_id:path}` as mounted, not the
canonical spec's shorthand `{id}`, because credential identifiers contain a
slash. It retains its separately sanctioned one-time credential response; D7
distinguishes that response from owner-auth material.

The active deltas listed above, plus
`generation-fenced-codex-auth-rotation-provenance` and
`memory-honesty-last-mile`, must be rebuilt after E1/E2 adoption so their
header-only, unconfigured-503, or implicit-owner language points to the final
central contract. No such delta may archive afterward from its stale ancestor.
The domain assertions continue to run after authentication; they cannot create
a session or convert the presence of an owner row into caller identity.

The rebuild plan is exact and ordered:

| Active change | Requirements/clauses to rebuild against the adopted auth contract |
| --- | --- |
| `harden-runtime-auth-and-breaker-attention` | `dashboard-model-settings` REQ-001/002, `dashboard-spend-dashboard` fleet-halt owner-control scenario, and `runtime-attention-outbox` REQ-003 |
| `specify-home-presence-owner-entity-configuration` | `home-presence-configuration` owner-authenticated surface and its unconfigured/wrong-credential scenarios |
| `specify-roster-identity-owner-operations-overlay` | `dashboard-butler-management` REQ-001 owner-control prose/scenarios |
| `durable-dashboard-terminal-action-recovery` | `dashboard-conversations` REQ-006 and `dashboard-terminal-action-recovery` REQ-005 owner-only operations |
| `generation-fenced-codex-auth-rotation-provenance` | `dashboard-api` Codex save/rotate, reauthorize/device-auth, probe, and revoke owner boundary |
| `memory-honesty-last-mile` | `dashboard-api` Owner-Scoped Dead-Letter Episode Requeue API |

After the selected E1/E2 amendment is independently reviewed and adopted,
each owner copies the then-current whole requirement, changes only its
transport-auth clauses to the central header-or-session contract, preserves all
domain checks and scenario names, and reruns same-name plus body-overwrite
checks. No related change may archive while it still carries the stale clauses;
archive order is coordinated only after every rebuild validates against the
then-current baseline.

## Goals and non-goals

Goals:

- Preserve one-user/one-instance sovereignty while making browser ownership a
  real server-enforced fact.
- Keep configured-key automation and the already-approved configured-key
  browser session compatible.
- Make keyless first-owner enrollment possible only after a fresh explicit
  action by an operator who already controls the trusted host.
- Define failure, restart, recovery, revocation, and rollback before any
  credential or enrollment surface exists.
- Give the eventual browser flow honest, accessible, non-duplicating states.

Non-goals:

- No selection or recommendation of a keyless enrollment transport.
- No implementation, endpoint registration, schema, migration, key/proof
  generation, credential read, host command, Compose change, browser flow,
  enrollment, provisioning, deployment, restart, or runtime action.
- No multi-user accounts, invitations, roles, password recovery, email/OAuth
  identity provider, or claim that Tailscale/loopback/same-origin is owner
  authentication.
- No relaxation of route-specific domain authorization, approval, idempotency,
  or privacy rules.

## Decisions fixed by the governing owner choice

### D1: Configured-key browser sessions are opaque and server-managed

The configured-key session establishment surface accepts the raw
`DASHBOARD_API_KEY` once over an approved HTTPS origin, compares it in constant
time, and returns no copy, fingerprint, prefix, or derived identifier. The
request body is structurally exempt from generic audit/body buffering before it
is read. The frontend holds the submitted value only in the live input control
for that request and clears it on settlement; it never uses localStorage,
sessionStorage, IndexedDB, a URL, application cache, query cache, service worker,
log, telemetry, error detail, or build-time variable.

The configured-key surface is `POST /api/auth/owner/session` with an exact
`{api_key: string}` body (`extra="forbid"`, bounded before decode). On success
the server creates a cryptographically random opaque session token
with at least 256 bits of entropy, stores only its digest plus bounded metadata,
and sets it in a cookie named with the `__Host-` prefix. The cookie is
`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, has no `Domain`, and has an
absolute `Max-Age` no greater than 12 hours. Expiry is not sliding. Session
issuance rotates any browser-supplied session identifier, so a caller cannot
fix the issued identity.

`X-API-Key` remains a first-class non-browser credential. It is checked exactly
as today and does not require a CSRF token because it is not ambient browser
authority. A valid owner session and a valid header both resolve to the same
single owner principal, but neither can mint, reveal, rotate, or retrieve the
configured key.

### D2: Cookie authority always carries an independent CSRF boundary

The session response also creates a distinct random synchronizer CSRF token.
The server stores only its digest. The browser may retain the CSRF token in
memory for the lifetime of the page and sends it in `X-CSRF-Token`; it is not an
authentication credential and never enters persistent browser storage. After a
page reload, authenticated `GET /api/auth/owner/csrf` may issue a replacement
token with `Cache-Control: no-store`. This is the sole bounded exception to the
exact-Origin rule because browsers do not reliably send `Origin` on a
same-origin GET. It requires the valid Strict session cookie, HTTPS request
authority exactly matching one configured origin, `Sec-Fetch-Site: same-origin`,
`Sec-Fetch-Mode: cors`, and `Sec-Fetch-Dest: empty`; it rejects redirects and
sends no permissive CORS header. Trusted-proxy configuration, not arbitrary
`Forwarded` or `X-Forwarded-*`, determines the effective HTTPS authority. Its
only state effect is inserting or replacing a bounded CSRF digest; it reads no
domain data and performs no owner action. The server may retain at most four
active token digests per session so separate tabs do not invalidate one
another; each expires within 30 minutes and never outlives the session. The
response contains only the new token and its expiry.

Every unsafe request (`POST`, `PUT`, `PATCH`, `DELETE`) authenticated by the
cookie must pass all of:

1. exact `Origin` match against the configured finite HTTPS origin allowlist;
2. a non-empty `X-CSRF-Token` whose digest matches the active session in
   constant time; and
3. the normal owner session, route authorization, validation, idempotency, and
   audit checks.

A missing, malformed, `null`, wildcard, HTTP, or mismatched Origin fails closed
on every unsafe request. `SameSite=Strict` and CORS are defense in depth, not
substitutes for the synchronizer token. Safe cookie-backed reads require the
valid session but do not require an `Origin` or CSRF token because they perform
no mutation. The CSRF rehydration GET has the additional bounded Fetch-Metadata
and effective-HTTPS checks above because it issues a mutation capability and
updates its digest set. Session establishment has no prior
cookie authority, so it uses the submitted configured key or the eventually
adopted host proof plus exact HTTPS Origin; it is never exempt from origin
validation. Logout and session-revocation mutations require CSRF when invoked
through a cookie session.

### D3: Keyless enrollment authority originates outside the HTTP visitor

When `DASHBOARD_API_KEY` is absent, the instance begins `keyless_unenrolled`.
No request order, source address, Host header, forwarded header, same-origin
relationship, possession of a public status/enrollment-request identifier, or
successful loading of frontend assets can transition it to enrolled or issue a
session. An explicit host-local operator action must first create or approve
one enrollment authority through the mechanism the owner later adopts.

Every conforming mechanism must satisfy the same invariant:

- authority is created or approved only by a process action available to an
  operator who controls the trusted host, never by the public HTTP request;
- it is scoped to one instance, one current enrollment epoch, one first-owner
  completion, and an expiry no more than 10 minutes after host authorization;
- bearer proof, when a mechanism has one, contains at least 256 bits of
  randomness and is stored server-side only as a digest;
- consumption and session creation commit atomically, with one durable
  consumed receipt retained beyond proof expiry;
- two concurrent attempts can produce at most one enrolled transition and one
  session; a retry or replay after the committed winner receives a fixed
  content-blind already-consumed/unauthorized result and no session;
- malformed, expired, wrong-instance, wrong-epoch, unapproved, unknown, or
  already-consumed authority produces no owner, session, disclosure, or partial
  state;
- the proof, challenge secret, cookie, CSRF token, configured key, and their
  digests never enter URLs, logs, audit notes, metrics, traces, prompts, model
  sessions, MCP tools/resources, connectors, notifications, or frontend assets.

The browser may be allowed to create a bounded pending reference under a future
challenge mechanism, but that reference is never authority. The host action
must authorize the exact current reference, and abandoned requests expire
without changing enrollment state.

### D4: Enrollment, session, restart, revocation, and recovery form one state machine

The server persists a closed enum and monotonically increasing enrollment epoch.
The only accepted states are `configured_key`, `keyless_unenrolled`,
`keyless_enrolled`, and `unavailable`. Unknown enum values, duplicate active
owner records, inconsistent epochs, unreadable state, or unavailable session
storage map to `unavailable`; they never map to a permissive default.

Session rows are durable so an ordinary process restart preserves an unexpired,
unrevoked session and its original absolute expiry. Restart never extends a
session or reopens an enrollment authority. If durable auth state cannot be
read, protected requests return a fixed `503` and the public status reports only
`unavailable`; health remains reachable for repair.

Configured-key rotation increments the authentication epoch and revokes every
session established from the prior key generation. Removing a configured key
does not reactivate any historical keyless enrollment: the instance enters
`keyless_unenrolled` until a new host-authorized completion occurs. Adding a key
revokes keyless sessions and enters `configured_key` after restart. A malformed
or internally contradictory configuration enters `unavailable` instead of
falling back to either mode.

`DELETE /api/auth/owner/session` revokes the current session and
`DELETE /api/auth/owner/sessions` revokes all sessions. An authenticated owner
can invoke either route with cookie plus CSRF or with `X-API-Key`. A host
operator can perform emergency revocation without a browser session through the
same selected host-authority boundary. Recovery after all sessions are lost is
not an unauthenticated browser reset: a fresh host authorization increments the
epoch, revokes every prior session/proof, and issues at most one new session
under D3. No historical proof or session becomes valid again.

### D5: Keyless mode protects the dashboard before it enrolls the owner

After implementation cutover, keyless mode admits no dashboard API request
except health/readiness, a content-blind owner-auth status read, and the exact
minimal enrollment surface chosen later. Frontend static assets may load, but
data and controls remain behind the owner session. In configured-key mode, the
matching header or approved session satisfies the global dashboard boundary.

Every route specifically tagged or specified as owner-only additionally passes
`require_dashboard_owner_control` (or its adopted centralized successor) before
body buffering, database access, protected-state observation, or domain owner
assertion. Route-specific checks still run after authentication. Public health,
connector-scoped callbacks, private service control credentials, and OAuth
state do not confer owner-session authority.

A route-introspection contract test must enumerate the mounted application and
compare every owner-only route metadata entry with the centralized dependency.
The explicit inventory above is review evidence, not the runtime enforcement
mechanism.

### D6: The browser flow reports state without leaking authority

A content-blind `GET /api/auth/owner/status` response may report only the closed mode/state,
whether this browser is authenticated, and its own session expiry. It never
reports a key, proof, challenge secret, token/digest, session count, prior
visitor, owner/contact identity, configuration path, or failure tail.

The shell renders one calm state at a time: configured-key entry,
host-authorization required, waiting for the selected host action,
authenticated, session expired/revoked, or auth unavailable. A pending action
acknowledges within 100 ms, disables duplicate submission, remains keyboard
operable with visible focus, and exposes a bounded retry only when retry is
safe. Replays and concurrent losses never show success. The page uses one
commit action, terse owner-direct copy, canonical status indicators, no
celebration, and no claim that loading the page enrolled the visitor.

### D7: Issuance output is a closed positive allowlist

Successful configured-key session establishment and the later-selected keyless
completion may emit owner-auth material in exactly two places: the opaque
session token in one `Set-Cookie` header and response data containing exactly
`csrf_token`, `csrf_expires_at`, and `session_expires_at`. Successful
`GET /api/auth/owner/csrf` response data contains exactly `csrf_token` and
`csrf_expires_at`. These responses set `Cache-Control: no-store`; no other
header or field carries owner-auth material. Status and revocation responses
carry none.

The security test uses distinct, non-secret fixture sentinels for the submitted
key, host proof/challenge authority, issued session, issued CSRF token, stored
digests, and owner identity. It positively asserts the session and CSRF token
only in the exact allowlisted locations above, then proves every other sentinel
absent from every other response and evidence sink. An empty response or empty
log capture cannot pass because the test first proves that each sink and both
positive issuance locations were exercised.

The existing `POST /api/secrets/cli/{credential_id:path}/rotate` contract is a
separate positive allowlist: only its successful one-time response may contain
its newly issued credential `value` and display `fingerprint`. That exception
does not permit a dashboard key, host authority, session, CSRF token, digest, or
owner identity to appear there, and it does not permit the rotated credential
to appear in any other response or evidence sink.

## Remaining owner adoption choices

No option below is selected or recommended by this draft. If unanswered, the
default is no keyless enrollment surface and no cutover from the current
deployment behavior.

### Choice E1: Keyless host-authority transport

**A. Transferable one-time host code.** A host-local command asks the dashboard
service to mint one short-lived bearer code and prints it once to the operator,
who enters it in the browser. This is simple and works without a long-lived
host IPC channel, but the code crosses the clipboard/typing boundary and must be
protected as authentication material until consumed.

**B. Host approval of a browser challenge.** The browser creates an inert,
bounded challenge reference; a host-local command displays and approves that
exact reference, after which the browser can complete once. No bearer authority
is copied into browser input, but the protocol, polling, expiry, request-flood
limits, and exact binding are more complex.

Both choices must satisfy D3 and D4. A file-mounted bootstrap secret is not an
implicit third option: the doctrine permits only one named process-bound
deployment-control key today, so adding another file-backed authentication
secret would first require an explicit doctrine amendment.

### Choice E2: HTTPS entry point for default Compose

**A. Existing Tailscale Serve HTTPS is canonical.** Default browser enrollment
and session use is documented and tested through the existing tailnet HTTPS
boundary. This avoids a local CA/certificate lifecycle, but a host without that
boundary has no supported browser session path.

**B. Add a loopback HTTPS terminator.** Compose gains a separately specified
local TLS endpoint and certificate trust workflow while retaining Tailscale
Serve for remote access. This preserves offline-local use but adds certificate,
port, proxy, and recovery surface that must be specified and verified.

Plain HTTP and reliance on a browser's localhost secure-context exception do
not satisfy either choice. The selected entry point must prove that the exact
`Secure` cookie is accepted and sent by supported browsers.

## Compatibility and rollback

- Existing callers with a configured key continue to send the same
  `X-API-Key` and receive the same constant-time authentication decision.
- Configured-key browser sessions and keyless sessions share the same cookie,
  CSRF, expiry, revocation, and route contract; their issuance authority differs.
- No configured key is exposed to make legacy browser code work. No query
  parameter, frontend environment variable, service worker, or JS-readable
  storage compatibility shim is permitted.
- Existing no-key unauthenticated API clients are not silently grandfathered
  into owner authority. Before cutover they must configure `DASHBOARD_API_KEY`
  or receive a separately adopted non-browser credential contract.
- Session/enrollment storage is additive. Rollback to an image that does not
  understand it must first revoke sessions and proofs, then either configure a
  non-empty dashboard key or remove every browser/network exposure. Starting a
  legacy fail-open image with an unset key while the dashboard remains reachable
  is a forbidden rollback.
- Rollback never deletes enrollment/audit history as a side effect and never
  translates a session token into an API key. A later forward cutover increments
  the epoch before admitting new sessions.

## Future verification seams

Implementation is not complete until one exact head provides all of this
evidence, with one gate species per invariant:

1. Mounted full-app API tests for configured-key session establishment,
   `X-API-Key` compatibility, cookie attributes, session fixation resistance,
   all owner-gated routes, and pre-body/pre-pool denial.
2. Real-PostgreSQL tests that race equal enrollment authority, prove one
   atomic winner/one consumed receipt, reject replay before and after process
   restart, preserve expiry, and fence recovery by epoch.
3. Browser tests over the selected real HTTPS Compose entry point that prove a
   `Secure`/`HttpOnly`/`SameSite=Strict` cookie works, is absent from JS APIs,
   survives an ordinary restart only until its original expiry, and disappears
   on logout/revocation.
4. CSRF tests from the mounted middleware stack for missing/wrong token,
   missing/`null`/wrong/HTTP Origin, cross-origin form and credentialed fetch,
   allowed-origin success, header-auth exemption, and denial before mutation.
   The selected real HTTPS browser lane must reload, demonstrate its same-origin
   CSRF fetch omits `Origin` while sending the exact required Fetch Metadata,
   obtain a usable token, and reject cross-site navigation/fetch plus untrusted
   forwarded-authority variants.
5. Configuration/recovery tests for absent key, key addition/removal/rotation,
   malformed and unknown state, unavailable storage, lost-session recovery,
   emergency host revocation, and guarded rollback to the prior image.
6. D7 positive-issuance plus absence-sentinel security tests. Positively prove
   the exact session-cookie and CSRF response locations, then prove distinct
   key, proof/challenge, session, CSRF, digest, body, and owner-identity
   sentinels absent everywhere else: responses, audit, logs, metrics, traces,
   prompts, MCP, connectors, notifications, built frontend, source maps, and
   service-worker caches. Separately prove the existing CLI rotate one-time
   `value`/`fingerprint` response and absence everywhere else.
7. Route-introspection and frontend contract tests proving every inventoried or
   newly tagged owner-only browser route uses the centralized boundary and that
   the shell exposes honest, accessible, repeat-safe states.

Mock-only, dependency-override-only, source-grep-only, or a polished browser
page without the mounted API, real database race, and exact HTTPS Compose path
does not satisfy this evidence.

## Adoption and authority boundary

This is a contract draft only. Independent semantic/security review must pass
on an exact commit. The owner must then select E1 and E2 and separately adopt
the exact reviewed artifact. Any semantic edit after review or adoption
requires fresh exact-head review and renewed adoption.

Adoption does not authorize implementation, migrations, proof/key generation,
credential access, provisioning, browser enrollment, configuration changes,
deployment, restart, rollback, runtime verification, merge, queue entry,
archive, or release. Those remain separately allocated and authorized acts.
