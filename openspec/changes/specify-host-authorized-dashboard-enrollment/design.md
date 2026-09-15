# Owner passkeys and server-managed sessions

Status: implementing the exact adopted successor; see [adoption.md](adoption.md)
for original artifact identity and bounded implementation clarifications.
Baseline: `5221178fbcfe60edeec0b7af31b71c7d55f0639e`.
Reuses PR4164 `285687c987b7c340b58b2fb6adafdaabbeb9c984`, bu-azqfpk
(author), bu-eeqmwt (review), and bu-7y7z2 (vertical delivery).
The September 15 owner records approve passkeys stored in Bitwarden, host-local
first enrollment/recovery, and canonical Tailscale Serve HTTPS. Those direction
records alone did not adopt this successor; the subsequent exact adoption is
recorded in adoption.md. Old E1/E2 questions are resolved direction. The host-to-ceremony protocol below is an engineering decision.

## D1. Motif, doctrine and placement

One owner needs low-friction daily access without putting a reusable dashboard
key in JavaScript persistence. Host control anchors initial trust; later
WebAuthn assertions authenticate the same server-controlled `owner` principal.
The server never treats a Bitwarden account, submitted name/email, owner entity,
Tailnet membership, or a first HTTP arrival as identity proof. WebAuthn is
verified deterministically; no model participates.

[Observed] At the baseline, ApiKeyMiddleware is header-only and passes through
when unset; owner_control independently rejects absent keys; the shell has no
login gate. The successor replaces this split enforcement with one central
ASGI boundary before protected body reads, domain pool acquisition, owner
lookups, caches and handlers. Its dedicated authentication-store access is
necessarily before domain access and is not a domain-pool exemption.
`authenticated_principal()` consumes verified request authority for HTTP audit
attribution, while preserving existing explicit internal caller contracts.

[Inferred] Placement: `src/butlers/api/` owns verification, auth repository,
HTTP boundary and auth router; the existing CLI command group owns trusted
host operations; additive core migrations own an isolated `dashboard_auth`
schema and role grants. `frontend/src/components/layout/Shell.tsx` gates
protected queries; `frontend/src/api/client.ts` handles session/CSRF transport.
Tailscale Serve terminates HTTPS and forwards the dashboard shell and API on
one origin. No module, connector, LLM tool or Bitwarden vault integration is
added. See route-reconciliation.md for the current integration inventory.

## D2. Origin and mode identity

`DASHBOARD_AUTH_ORIGIN` is exactly one canonical `https://<dns-host>` origin
(port 443, no path, userinfo, query, fragment, wildcard or trailing dot).
`DASHBOARD_AUTH_RP_ID` equals that canonical DNS host, never a parent domain.
The existing Compose alternate HTTPS-port override is outside this supported
browser contract: a non-443 configured origin is rejected, not normalized away.
Configuration is normalized/validated once; comparison thereafter is exact.
The same origin serves frontend and API, including deployments using a path
prefix. Deployments sharing that origin are mutually trusted browser surfaces;
separate cookie names/state prevent accidental collision, not same-origin script
attacks. Independent trust requires distinct canonical hostnames. Paths do not enter the RP ID. No alias-origin list or localhost HTTP
exception is supported. Origin/RP are public deployment configuration, not
secrets. Neither request Host nor Forwarded headers can expand them.

At the trusted loopback proxy boundary, the effective HTTPS authority must
match this configuration. The deployed proxy must strip inbound forwarded
headers and set its own; the API trusts metadata only from explicitly configured
proxy peers. Direct HTTP or unknown proxy paths cannot issue/use browser
cookies, even with a spoofed HTTPS header. Tests exercise the actual proxy
chain. Tailnet access remains necessary network reachability, not owner auth.

Persist instance UUID, canonical origin/RP, state and separate credential and
session epochs in one singleton row. States are `keyless_unenrolled`,
`keyless_enrolled`, `configured_key`, `recovery_pending`; `unavailable` is the
public effective state when configuration/storage is inconsistent. Migration
creates the singleton once with no authority. A missing row after initialization,
duplicate row, missing previously installed credential, bad enum/epoch, or
unreadable store is unavailable, never fresh enrollment. API startup cannot
recreate missing rows. An absent/malformed origin disables browser ceremonies
and sessions; configured matching X-API-Key automation remains available if
authoritative auth storage is healthy. Unavailable storage denies protected
requests even with a key. Health remains available.

For compatibility, configured-key mode retains the original exclusive mode
transition contract: the host-only `butlers auth reconcile-mode --confirm-revoke` operation,
run before restart after adding/changing/removing DASHBOARD_API_KEY, advances both epochs, revokes sessions and ceremonies, and retires historical
passkey authority. Removing a key enters keyless_unenrolled, requiring fresh
host enrollment; adding one enters configured_key. Passkey ceremonies in
configured_key mode return fixed unavailable results. The operator sees this
tradeoff before enabling the key; this is not a silent alternate login method.
The host operation reads the intended deployment configuration without printing
the key, locks the singleton and atomically records the target mode/key-generation
digest with epoch revocation. Repeating it with unchanged configuration is a
no-op. API startup compares its effective configuration with that committed
generation; mismatch is unavailable and cannot mutate or repair authority.
Every header-authenticated request compares the process key generation with
the authoritative current generation before accepting the key. Configured-key
session issuance repeats that comparison under its final transaction lock. A
still-running stale worker returns unavailable immediately after reconciliation,
until restarted with matching configuration; no old-key grace window exists.
The host operation requires initialized consistent state and cannot repair a
missing singleton. A key generation digest is internal restricted state, never
an output.

Ordinary restarts with unchanged configuration preserve unexpired sessions,
credentials, receipts and original deadlines. A changed origin/RP makes browser
auth unavailable until explicit host `auth rebind-origin --confirm-revoke`
validates the configured identity, advances both epochs, retires credentials,
revokes sessions/ceremonies and enters recovery_pending in keyless mode.
The specific exclusive configured-key contract continues to govern when a key
is configured: that mode remains configured_key, old browser authority is
revoked, and a fresh key-to-session login uses the new canonical origin.
Rebind never accepts both origins or migrates a credential to a different RP. Restore from older database
state is an operational recovery event requiring host epoch invalidation before
exposure; a database rollback cannot be detected from that database alone.

## D3. Host authorization bound to the browser

Chosen mechanism: browser-bound inert request, approved by a host-local CLI
using existing trusted infrastructure DB access. This avoids a transferable
bootstrap secret and the extra deployment signing-key exception. A bearer code
would add clipboard/typing secret exposure without improving the accepted user
outcome. Approval never means 'allow the next visitor'.

A preauth context has a 256-bit random opaque cookie stored only as a digest,
a separate 256-bit CSRF token stored only as a digest, and a five-minute absolute
expiry. A server-configured stable deployment suffix (validated lowercase ASCII, unique
per deployment on an origin) scopes both cookie names; it is public config, not
authentication. Cookie `__Host-butlers-<deployment>-preauth` is HttpOnly, Secure, SameSite=Strict,
Path=/, no Domain; it never authenticates protected data. A browser creates one
intent bound to that context, current instance, origin/RP, operation and epoch.
Its random 256-bit `request_id` is a locator, not authority. UI shows the full
ID and canonical origin for exact comparison; no ID appears in a URL or retained
telemetry. Reload abandons the context and starts a fresh one; no browser storage
rehydrates secret material. Context refresh cannot extend an authorized intent.

`butlers auth authorize-registration --request <id>` validates an unexpired
keyless_unenrolled intent and atomically marks only that intent authorized.
`butlers auth authorize-recovery --request <id> --confirm-revoke` validates a
recovery intent, immediately advances credential/session epochs, retires the
old credential, revokes all sessions and other ceremonies, enters
recovery_pending and authorizes that exact intent at the new epoch. The bound
context is retained only for that intent. Authorization expires at the earlier
of intent expiry and five minutes after the host action. Host operations use
the owner-controlled migration/administrative DB connection; the API role
cannot authorize intents, recover, initialize missing state or rebind origins.
A restricted host-owned SQL function/role boundary grants the API only finish,
login/session and unapproved-intent operations, not direct authority-column
writes. The exact grants and negative role tests are delivery requirements.
No host control HTTP endpoint, MCP tool, signing secret, vault API or environment
secret fallback is introduced.

The CLI displays operation and canonical origin before its result, never lists
other pending visitors, and requires the exact request ID copied from the
owner's own browser. A supplied ID alone cannot complete registration: finish
also needs the bound HttpOnly cookie, independent CSRF and verified WebAuthn
response. An attacker persuading the host operator to approve an attacker-owned
request is outside cryptographic proof; the UI/runbook explicitly says to use
only the request shown in the browser being enrolled. Repeated approval cannot
extend deadlines or reopen consumed authority. At most one authorized intent
exists per instance; explicit host replacement invalidates the former one.

## D4. Closed HTTP ceremony contract

All paths below are under `/api/auth/owner`. JSON success uses the ordinary
`{data: ...}` envelope; each data object has exactly the listed fields. No extra
input fields. All responses, including errors, use no-store, no permissive
CORS, and Referrer-Policy no-referrer. Exact configured non-null HTTPS Origin
is required on every ceremony POST. JSON Content-Type only, no compression,
streaming body limit before decode, 5-second body deadline. Cross-origin requests
are rejected before auth-state lookup/body parsing. Cookie-bound POSTs require
`X-CSRF-Token`; all request/response bodies and credential headers bypass generic
body logging, exception reflection and telemetry structurally.

| Method/path | Exact input | Success data / cookie | Raw body limit |
|---|---|---|---:|
| GET /status | no body | `{state, authenticated, session_expires_at}`; expiry null unless this browser has a valid session | 0 |
| POST /context | `{}` | `{csrf_token, csrf_expires_at}` plus preauth cookie | 1 KiB |
| POST /registration/intent | `{operation: "enroll" or "recover"}` + preauth/CSRF | `{request_id, expires_at, operation, canonical_origin}` | 1 KiB |
| POST /registration/options | `{request_id}` + bound preauth/CSRF | 202 `{state:"pending", expires_at}` or 200 `{ceremony_id, publicKey}` after host approval | 1 KiB |
| POST /registration/finish | `{ceremony_id, credential}` + bound preauth/CSRF | session tuple below plus owner cookie, clears preauth cookie | 64 KiB |
| POST /login/options | `{}` + preauth/CSRF | `{ceremony_id, publicKey}` | 1 KiB |
| POST /login/finish | `{ceremony_id, credential}` + bound preauth/CSRF | session tuple plus owner cookie, clears preauth cookie | 16 KiB |
| POST /ceremony/cancel | `{request_id}` or `{ceremony_id}` (exactly one) + preauth/CSRF | `{}`; invalidates caller-bound pending state only | 1 KiB |
| POST /session | `{api_key: string}` + exact Origin | session tuple plus owner cookie | 4 KiB |
| GET /csrf | valid owner cookie and bounded metadata checks in D7 | `{csrf_token, csrf_expires_at}` | 0 |
| DELETE /session | cookie+CSRF or X-API-Key; no body | `{}` and cookie expiry | 0 |
| DELETE /sessions | cookie+CSRF or X-API-Key; no body | `{}` and cookie expiry | 0 |

Session tuple is exactly `{csrf_token, csrf_expires_at, session_expires_at}`.
Deleting with header authority: singular expires/revokes the presented session
if any (otherwise succeeds with no session effect); plural advances the session
epoch and revokes all browser sessions. A malformed supplied X-API-Key fails
401 even when a cookie is present; a valid header is the chosen auth method and
is not ambient cookie authority. It never bypasses a route's domain checks.

Registration publicKey is exactly `challenge`, `rp:{id,name:"Butlers"}`,
`user:{id,name:"owner",displayName:"Butlers owner"}`, `pubKeyCredParams`
(ES256 -7 and RS256 -257), `timeout` (remaining milliseconds, at most 300000),
`authenticatorSelection:{residentKey:"required",requireResidentKey:true,
userVerification:"required"}`, `attestation:"none"`. No extensions or
excludeCredentials list. The user ID is a server-generated 32-byte opaque
handle, distinct from every domain entity identifier, bound to this credential
epoch; it is visible only to the authorized ceremony and returned assertion.
Authentication publicKey is exactly `challenge`, `rpId`, `timeout`,
`userVerification:"required"`; omit allowCredentials for discoverable passkeys.
No credential inventory or account names are returned. Challenge and ceremony ID
are fresh 32-byte base64url values; challenge is public ceremony input, not
host authorization. Options retries return the same unexpired ceremony until
cancellation; they never refresh deadlines. Login starts one ceremony per
context. Registration finish `credential` accepts only id/rawId/type,
response(clientDataJSON, attestationObject, optional transports from a closed
WebAuthn transport enum), clientExtensionResults (empty object), optional
standard authenticatorAttachment; login response substitutes authenticatorData,
signature,userHandle. Binary strings must be canonical base64url; id must equal
rawId; type is public-key. Field caps are checked before decoding (credential ID
1023 bytes decoded; userHandle exactly 32; clientDataJSON 4 KiB; all remaining
fields within the total body cap). Unknown extensions/fields fail closed.

Pre-session exemptions are EXACT method/path pairs above except GET /csrf and
the DELETEs. Only GET /health and GET /api/health are public probes, never a
prefix exemption for /api/health/briefing. Existing connector callback/private
control/OAuth routes keep only their separately verified scoped authorities;
no such credential grants dashboard owner access. No wildcard auth path,
OPTIONS data response, redirect, download or WebSocket escapes the boundary.
Public status has exactly D2's closed state, the requesting browser's boolean
and its expiry; it never reports intent, credential count, owner or error tail.

Limits are transactional across workers: one live intent and one live ceremony
per context, 64 live contexts globally, 16 live registration intents globally,
one authorized registration globally; expired rows do not occupy capacity.
Creation is capped at 30 contexts/minute and 60 options/minute per instance;
finish at 120/minute, plus 10/minute per context. Fixed 429 + Retry-After 60
when capped; bounded buckets, never attacker-controlled labels/IP maps.
Pending options polling is at most once per two seconds, stops at expiry, and
pauses when hidden. Host operations remain available during public saturation
and can invalidate pending contexts without revealing them through
`butlers auth clear-pending --confirm-revoke`; this consumes pending intents
and ceremonies while preserving the active credential and established sessions. No guarantee of
availability against an authenticated-network denial-of-service attacker.

Errors use fixed `{error:{code,message,butler:null}}`: malformed/oversize 400/413,
missing/mismatched proof 401 UNAUTHORIZED, Origin/CSRF 403 FORBIDDEN,
consumed/expired ceremony 409 AUTH_RESTART_REQUIRED, capacity 429 RATE_LIMITED,
unavailable 503 AUTH_UNAVAILABLE. No reflected body, verifier exception, ID,
credential existence, signature/counter detail or database error. Successful
issuance requires durable commit; errors never set an owner session cookie.

## D5. Verified credential and transaction state

Use maintained Yubico `fido2==2.2.1` (pin and lock at implementation), not custom
cryptography. Pass explicit exact-origin verifier, RP ID, required UV and stored
challenge/state. Registration validates client/authenticator data, type, challenge, origin,
RP hash, UP/UV, credential key and algorithm using the conforming none-attestation
policy; none attestation has no attestation signature and makes no manufacturer
trust claim. Authentication additionally verifies the assertion signature with
the known active credential key and exact server-held owner handle. Reject
crossOrigin true, unexpected topOrigin, unknown credential, nonmatching userHandle,
malformed attestation/assertion and invalid BE/BS flags. Attestation preference
none carries no device/vendor trust claim and never proves Bitwarden storage.
Library debug logs may contain credential IDs: suppress those loggers and never
retain exception arguments. Pinning alone is not a security proof; synthetic
signed vectors exercise the real library and adapter together.

Credential BE is fixed at registration; BS may change and cannot be true with
BE false. For BE true, zero or nonincreasing counters do not independently deny
an otherwise valid assertion; store maximum observed counter and sanitized
counter-anomaly outcome. For BE false, reject a nonincrease when either old or
new counter is positive. Challenge single-use, not a counter, prevents replay.
Reject BE change. Concurrent verification rechecks policy under the final lock.

The host-authorized first registration finish locks singleton then context,
intent and ceremony in that order. It rechecks operation, instance, origin/RP,
epoch, deadline, approval, cookie/CSRF, unconsumed challenge and verified result;
atomically installs one credential, marks intent/challenge consumed, appends a
content-blind completion event, transitions to keyless_enrolled and creates one
session and CSRF digest. Any database/audit/session failure rolls back all of it;
no partial credential or cookie success. Unique constraints enforce singleton,
credential ID uniqueness and one active credential. Verify cryptography outside
the transaction, then recheck every authoritative predicate under locks.

Login finish follows the same lock order and rechecks active credential/epoch
before atomically consuming the challenge, updating verified metadata and
creating one session. Competing finishes yield at most one issuance. Host
revocation/recovery races either follow a committed login and revoke it or win
first and deny its commit. Session authorization reads authoritative epochs on
every protected request, never an unbounded process cache. Session revocation
linearizes at authorization; requests already admitted may finish their normal
transaction. No promise of undoing previously admitted domain mutations.

Lost response after commit: no replay reissues the session. Registration UI
says completion could not be confirmed and offers fresh passkey login; login
UI offers fresh login. Server-verified status may establish success only when
the new session cookie was actually received and remains valid. Cancellation
invalidates only the bound pending ceremony and cannot undo committed authority.
After host recovery has revoked the old credential, cancellation/expiry leaves
recovery_pending; another explicit host recovery can authorize replacement.
Ordinary session expiry/logout/all-session revocation leaves the credential
active, and Sign in with passkey needs no host action.

## D6. Persistence, privilege and retention

`dashboard_auth` is dedicated authentication state, not the shared public schema.
Public credential keys, opaque handles and challenges are verification records;
cookie/CSRF values are ephemeral browser secrets held only as digests server-side.
These are neither external provider Tier 1 credentials nor owner-account Tier 2
vault secrets; no provider secret is added. Existing host/database connection
credentials stay Tier 0. This classification introduces no file-secret exception.
No generic Secrets, owner entity/contact API, runtime child, MCP or connector may
read/write these records. Revoke PUBLIC access and default privileges; explicitly
grant the API role only the operations described in D3 and D5. No butler role,
including shared-public readers/writers, has schema usage or function execute.
Migration tests prove grants using actual SET ROLE and forbidden SQL calls.

Tables conceptually hold singleton identity/epochs/mode; one active credential
(public key, opaque handle, BE/BS/counter, active epoch); contexts/CSRF digests;
intents/host authorization; ceremonies/challenge/consumed state; sessions/digests;
and append-only sanitized audit outcomes. No raw key, session or preauth token
is stored. Use DB clock deadlines. Retain consumed/revoked intent and ceremony
receipts for 24 hours, then delete their verification payload; unknown IDs
always fail, so cleanup cannot permit replay. Expired sessions/CSRF/context
rows are pruned within 24 hours. Retired credential key/ID/handle are removed
within 24 hours, keeping only epoch and nonidentifying outcome. Sanitized auth
audit retains timestamp, action, outcome, verified actor category (owner,
host_operator or unauthenticated), no IDs/handles/material, for 30 days. Cleanup
is bounded and independent of request success. Singleton epochs never expire.

## D7. Configured sessions and CSRF

Keep POST /session's exact configured-key body, constant-time comparison and
structural secret-body exclusion. Browser clears input on settlement, uses no
URL/storage/query cache/service worker/telemetry for keys. Session cookie
`__Host-butlers-<deployment>-owner` contains at least 256 random bits, digest-only server
storage, HttpOnly Secure SameSite=Strict Path=/ no Domain, absolute non-sliding
12-hour maximum lifetime. Rotate caller-supplied identity; never derive a key
from a session. Ordinary restart cannot extend expiry.

Independent CSRF is random, digest-only and memory-only in the browser. Every
unsafe owner-cookie request requires exact Origin and X-CSRF-Token before domain
access/body buffering. SameSite/CORS cannot replace it. After reload GET /csrf
requires valid owner cookie, exact trusted HTTPS authority, Sec-Fetch-Site
same-origin, Sec-Fetch-Mode cors, Sec-Fetch-Dest empty, no redirects/CORS; it
needs no Origin. Keep at most four digests per session, each at most 30 minutes
and never beyond session expiry. Preauth contexts use their own CSRF token and
cannot call this endpoint. Authentication never bypasses independent approval,
owner-entity integrity, privacy, CAS, idempotency or the HA mapping 32 KiB
post-authentication body limit. Fixed actor attribution follows verified auth.

## D8. Browser walkthrough and privacy

Use Dispatch shell/tokens, one commit button, visible focus and keyboard paths,
AA contrast, status text with aria-live and no decorative motion/celebration.
Acknowledge work within 100 ms; do not start WebAuthn without a user gesture.
No protected query mounts until authenticated. On 401 purge protected query
cache and unmount private data; preserve only already-approved safe draft
contracts, never auth material. Do not replay failed mutations automatically.
Return after login only to a validated same-origin relative route, never a
caller-supplied external URL.

| Entry/condition | Flow and honest result |
|---|---|
| Fresh instance | Register passkey creates inert intent; displays canonical origin and copyable host command/request; Check authorization retries bounded options; then Register passkey invokes browser chooser. |
| Bitwarden registration | Explain 'Choose Bitwarden to save your passkey'; browser controls provider selection; show registered only after durable server response/session verification. Never claim vault persistence was inspected. |
| Returning or new synced browser | Sign in with passkey starts discoverable assertion and returns to protected shell after commit. No host command, username or vault password. |
| Expired session/logout | Clear private query cache; 'Sign in again' starts a fresh ordinary assertion. |
| Cancelled or timed-out chooser | 'Sign-in cancelled' or 'Registration cancelled', focus returns to action, cancel pending ceremony best-effort; retry creates fresh context/ceremony and invalidates predecessor. No success toast. |
| Lost credential/vault | 'Recover access' creates recovery intent; explains immediate revocation on host approval; explicit recovery host command then new registration. |
| Recovery interrupted | State remains locked for passkey login; show host recovery retry instructions, not 'try your old passkey'. |
| Response lost | State is uncertain; check own session status, otherwise offer fresh passkey login; never repeat finish to infer success. |
| HTTPS unsupported/missing | Explain canonical HTTPS is required; no HTTP downgrade or browser prompt; configured-key automation retains independent contract. |
| Configured-key mode | Key input and Sign in; clear input on settlement; automation continues X-API-Key; explain that passkey mode requires deliberate host configuration transition. |

The complete D4 output table is a positive allowlist. Public challenge/options,
request/ceremony locators and the authorized opaque handle are ceremony-visible
only there; they are not blanket exceptions for status/DTOs/logging. Auth tokens
appear only in designated cookies and CSRF tuples. Distinct synthetic sentinels
exercise each positive location and each actual evidence sink before absence
assertions; empty captures cannot pass. Preserve CLI credential rotation's
separate existing one-time value/fingerprint output; it gains no auth-material
exception. No raw auth HTTP bodies, credential IDs, handles, digests, command
arguments, URLs or library errors enter retained logs/traces/metrics/audits,
notifications, prompts, MCP, source maps or frontend caches. Metrics use only
closed action/outcome labels. Proxy/access logs use fixed route templates for
these endpoints and no query/header/body capture.

## D9. Delivery and operational boundary

See tasks.md for cohesive vertical implementation and verification, and
runbook.md for prepared operations. Additive storage may ship dark, but no
owner enforcement cutover lands alone without functional browser access.
Preserve bu-7y7z2 backend enforcement commit
`5e31158065b099e92aae887cb919523d8cb6b79b`; reconcile it into the same vertical
outcome. bu-a0aj3k stays held. HA mapping adoption at PR4050 is already applied;
this feature supplies auth, never private mapping values or HA runtime action.

Safe isolated tests precede any separately authorized canonical HTTPS/live
Bitwarden run. Actual owner vault, migration, proxy/certificate, credential,
restart, deployment and rollback actions remain separately bounded. Adoption
of exact spec bytes is required before implementation. User's full lifecycle
request authorizes ordinary development after that gate; it does not authorize
live or external communications. This preparation does not merge/archive the
feature or claim working authentication. Rollback requires revocation plus a
configured key or removal of exposure before starting any legacy fail-open
image; preserve epoch history and invalidate authority before re-exposure.

## Primary evidence refreshed 2026-09-15

- W3C WebAuthn Level 3 registration/authentication and backup semantics:
  https://www.w3.org/TR/2026/REC-webauthn-3-20260825/
- Yubico maintained verifier release and server API (explicit origin callback):
  https://github.com/Yubico/python-fido2/releases/tag/2.2.1
  https://developers.yubico.com/python-fido2/API_Documentation/autoapi/fido2/server/index.html
- Bitwarden website passkey storage/use (not a vault API or storage attestation):
  https://bitwarden.com/help/storing-passkeys/

[Unknown] Real canonical-host browser and owner-vault interoperability have not
been exercised; the delivery gate requires them under separate live authority.
