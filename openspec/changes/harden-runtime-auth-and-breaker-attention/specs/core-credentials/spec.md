## MODIFIED Requirements

### Requirement: Live Codex Device-Auth Reconciliation

The runtime SHALL treat an explicitly supplied system-global Tier 1
`cli-auth/codex` authority as the authoritative Codex device-auth state in
every topology. In flat topology the authority pool MAY be the same object as
the local pool, but callers SHALL still select it explicitly rather than
deriving authority from fallback order. Before a new Codex subprocess is
launched, the runtime SHALL reconcile that DB-backed authority to the
canonical local `~/.codex/auth.json` path when the contents differ. The
reconciliation SHALL never log credential content, SHALL write a replacement
atomically with mode `0600`, and SHALL refresh the local rotation baseline
after a DB-originated write.

All `cli-auth/codex` restore, live Codex reconciliation, Codex
runtime-originated rotation persistence, Codex dashboard device authentication,
Codex runtime probes, and Codex-dependent connector startup paths SHALL use the
explicit authority channel. They SHALL not read a schema-local
`cli-auth/codex` row as a fallback or bootstrap source. Ordinary domain
credentials and existing other-provider CLI-auth authority behavior retain
their existing resolution behavior.

ID: REQ-core-credentials-001
Source: heart-and-soul/security.md; craft-and-care/security-and-secrets.md; RFC 0006; core-credentials Live Codex Device-Auth Reconciliation; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Dashboard refresh takes effect on the next invocation

- **WHEN** the dashboard stores a newer authoritative `cli-auth/codex` value
  while a daemon remains running with a different local `auth.json`
- **THEN** the next Codex invocation SHALL use the authoritative value without
  requiring a daemon restart
- **AND** no completed or already-running session SHALL be changed or replayed

#### Scenario: A schema-local row cannot shadow authority

- **WHEN** a schema-isolated daemon has an older local `cli-auth/codex` row
  and the explicit authority contains a newer dashboard credential
- **THEN** restoration, reconciliation, and runtime-originated persistence use
  the authority value
- **AND** the local row neither reaches a shared runtime file nor prevents the
  authoritative credential from reaching the next invocation

#### Scenario: Multiple shared-volume writers converge on authority

- **WHEN** multiple in-process daemons sharing a Codex runtime filesystem
  restore CLI auth during startup
- **THEN** each writes the same authoritative document or reports authority
  unavailability
- **AND** startup order cannot make a schema-local credential the final shared
  file contents

#### Scenario: Matching local token is left untouched

- **WHEN** the authoritative `cli-auth/codex` value exactly matches the
  canonical local `auth.json`
- **THEN** reconciliation SHALL not replace the file
- **AND** it SHALL record the existing file as the rotation baseline

#### Scenario: Unavailable authority fails closed for new auth-dependent work

- **WHEN** the authority is absent, unavailable, exceeds the bounded
  synchronization wait, or is malformed
- **THEN** reconciliation SHALL log only safe context and SHALL not expose a
  raw credential value
- **AND** it SHALL not launch a new Codex subprocess by falling back to a
  schema-local credential or an unverified local auth file
- **AND** it SHALL not change or replay an already-running session

#### Scenario: Concurrent reconciliation cannot expose a partial file

- **WHEN** multiple local runtime invocations reconcile the same Codex
  auth-file path concurrently
- **THEN** every visible file state SHALL be a complete credential document
- **AND** the final file mode SHALL remain `0600`

#### Scenario: A stale runtime rotation cannot overwrite a dashboard refresh

- **WHEN** a Codex subprocess was launched with an older authority snapshot
  and the dashboard writes a newer `cli-auth/codex` value before that process
  finishes
- **THEN** post-invocation rotation persistence SHALL perform a conditional
  update using the launch snapshot
- **AND** its update SHALL be skipped when the authority value has changed

#### Scenario: A stale runtime health result cannot affect a dashboard replacement

- **WHEN** a Codex subprocess launched on an older authority reports an auth
  failure after the dashboard has stored a replacement credential
- **THEN** its credential health update SHALL be conditional on the exact
  credential bytes used by that subprocess
- **AND** it SHALL not mark the replacement credential failing

#### Scenario: Value replacement atomically clears prior health state

- **WHEN** a runtime health update for credential A obtains the row lock before
  a dashboard refresh or a winning runtime rotation replaces A with B
- **THEN** the value-changing write SHALL clear the prior test status, code,
  message, and verification timestamp in the same database statement
- **AND** B SHALL not inherit A's healthy or failing state

#### Scenario: Dashboard runtime probe binds to the canonical authority it tests

- **WHEN** a dashboard-requested runtime probe begins with authoritative
  credential B while the canonical local auth file still contains A
- **THEN** the runtime-probe coordinator SHALL reconcile the canonical file to
  B before running the provider command
- **AND** it SHALL persist health, probe history, and audit evidence only when
  that file still matches B and the authority value remains B
- **AND** a concurrent replacement or local-file change SHALL leave the
  operator response intact while withholding its durable health result

#### Scenario: Absent authority is never implicitly bootstrapped

- **WHEN** the shared Codex credential is absent, revoked, unavailable, or
  malformed while a canonical local auth file exists
- **THEN** a runtime preflight or post-operation finalizer SHALL NOT create or
  recreate the authority from that local file
- **AND** explicit dashboard device authentication remains the supported
  bootstrap path for a new authority value

#### Scenario: Direct dispatcher authority is explicit

- **WHEN** a direct `DiscretionDispatcher` has only a schema-local model pool
- **THEN** it SHALL not construct a Codex credential authority from that pool
- **AND** callers with a known system-global credential authority SHALL pass it
  explicitly to the runtime adapter

#### Scenario: Ignored local CLI-auth evidence is safe and diagnostic only

- **WHEN** a local CLI-auth row differs from the explicit authority
- **THEN** the runtime MAY record safe, value-free diagnostic metadata that the
  local scope was ignored
- **AND** it SHALL not reveal either credential, token fingerprint, or raw
  serialized auth document

## ADDED Requirements

### Requirement: Asymmetric Runtime-Probe Control Capability

The system SHALL authenticate the private Dashboard/Scheduler to Switchboard
runtime-probe control plane with a short-lived asymmetric signed capability,
not a shared bearer token. The dedicated client SHALL call
`POST /_control/runtime-probe/v1` and SHALL place one compact JWS only in
`Authorization: Bearer <compact-jws>`; the endpoint SHALL reject capability
copies in cookies, query parameters, or the request body. The protected header
SHALL contain exactly string `alg: "EdDSA"` and string `kid`. The payload SHALL
contain exactly string `aud: "switchboard.runtime_probe_control.v1"`, enum
string `caller: "dashboard" | "scheduler"`, canonical lowercase UUID string
`catalog_entry_id`, integer NumericDate-second `iat` and `exp`, and `nonce` as
unpadded base64url encoding of 32 cryptographically random bytes. Extra header
or payload fields SHALL be rejected. The verifier SHALL resolve `kid` only from
its deployment keyring, SHALL NOT perform remote/dynamic lookup, SHALL reject
`jku`/`x5u`, and SHALL reject `none`, symmetric, and other asymmetric
algorithms. It SHALL allow at most five seconds of clock skew and accept only
`iat <= now + 5s`, `exp >= now - 5s`, and `0 < exp - iat <= 60s`.
The endpoint SHALL return safe typed JSON with HTTP `200`/status `completed`,
`401`/status `unauthorized` for missing/invalid/expired capability,
`409`/status `replay`, `429`/status `busy` for an occupied per-entry or global
bound, `503`/status `unavailable` for coordinator/catalog/authority/
configuration/runtime setup, and `504`/status `timeout` for the runtime
deadline. The deadline SHALL be 30 seconds, global concurrency SHALL
be eight, and per-catalog-entry concurrency SHALL be one. Error bodies SHALL
contain no raw provider error, credential, key material, capability, signature,
or nonce. A private `RUNTIME_PROBE_CONTROL_SIGNING_KEY` SHALL
be delivered through a dedicated deployment-secret mount only to the Dashboard
API process, including its registered verification scheduler. The all-butlers
daemon, Switchboard, unrelated butlers, and model-runtime child processes SHALL
receive only the corresponding non-secret verification key. The private key
SHALL NOT be stored in a schema-local or `public.butler_secrets` credential row,
resolved by `CredentialStore`, or supplied through an environment-value
fallback. It SHALL not be exposed to a browser, generic MCP client, model
session, normal MCP client manager, telemetry, audit note, log, or generic
Secrets API inventory, detail, mutation, or fingerprint response. The generic
Secrets API SHALL reject the reserved private-key name rather than creating a
shadow DB credential. Missing, malformed, unavailable, or mismatched signing /
verification key state SHALL make the control plane unavailable; it SHALL not
fall back to an unauthenticated command.
The verifier SHALL accept only configured current and retiring verification
keys identified by protected key ID. Rotation SHALL install the new verifier
before the Dashboard signer uses its key ID. The retiring signer SHALL stop at
its configured `sign_until`, and the verifier SHALL retain that key through an
`accept_until` at least 70 seconds later (60-second maximum capability lifetime
plus two five-second skew allowances), but no more than five minutes later. An
unknown key ID SHALL fail closed.
The deployment SHALL use two strict UTF-8 JSON documents with unknown or
duplicate fields rejected. Key IDs SHALL match `[A-Za-z0-9._-]{1,64}`;
Ed25519 material SHALL be unpadded base64url-encoded raw 32-byte seed/public
values; timestamps SHALL be UTC RFC 3339 seconds. The Dashboard-only signer at
`/run/secrets/runtime_probe_control_signing_key` SHALL contain exactly
`version: 1`, `alg: "EdDSA"`, `kid`, `private_key_b64u`, `sign_from`, and
nullable `sign_until`. It SHALL be a regular non-symlink file owned by the
Dashboard process identity, mode `0400`, on a read-only mount. The shared
non-secret keyring at
`/run/secrets/runtime_probe_control_verifiers` SHALL contain exactly
`version: 1`, one current object with `alg: "EdDSA"`, `kid`,
`public_key_b64u`, and `sign_from`, and zero or one retiring object with
`alg: "EdDSA"`, `kid`, `public_key_b64u`, `sign_from`, `sign_until`, and
`accept_until`. Current/retiring IDs and public keys SHALL be distinct. When a
retiring key exists, `current.sign_from == retiring.sign_until == T`,
`retiring.sign_from < retiring.sign_until`, and
`retiring.accept_until - retiring.sign_until` SHALL be in
`[70 seconds, 5 minutes]`.
Dashboard and all-butlers SHALL mount the same keyring source read-only;
all-butlers SHALL receive no private signer material.
Parser, receipt, endpoint, and client code MAY land inert before deployment
activation, but the production private signer mount SHALL NOT be present until
every Dashboard runtime-CLI subprocess path is either removed or uses one
mandatory OS launcher. That launcher SHALL allocate an exclusive unprivileged
UID/GID to each live invocation, clear supplementary groups, set
`no_new_privs`, use a child-owned per-invocation HOME and configuration
directory, and pass an allowlisted environment with no database credential,
dashboard owner-control key, OAuth secret, or deployment secret. A
Bubblewrap nested-user/mount/PID-namespace domain SHALL deny every peer staging
tree, peer `/proc` state, canonical credential root, and signer. The outer
identity SHALL come from reserved UID/GID range `61000..61999`. The sandbox
SHALL provide a fresh procfs, private tmpfs, minimal device view, one writable
staged HOME, and only the read-only provider executable/runtime-library, CA,
and resolver inputs required for networked auth/health; root, run, app,
canonical homes, peer stages, and host procfs SHALL be absent. The
parent SHALL spawn Bubblewrap with `close_fds=True` and an exact `pass_fds`
allowlist containing only stdio and typed launcher-created Bubblewrap setup
pipes or seccomp descriptors. Those referents SHALL NOT be the signer,
canonical authority, a staging-root descriptor, a peer stage, parent procfs, or
other credential-bearing material. A repository-owned
`runtime-cli-sandbox-init` SHALL become namespace PID 1 and, after the trusted
handshake releases it, SHALL close every descriptor above stderr with
`close_range(3, UINT_MAX, 0)`, verify that no unexpected descriptor remains,
and `execve` the provider CLI. The runtime payload SHALL therefore inherit only
the approved stdio endpoints and no Bubblewrap setup descriptor. Missing
`close_range`, a missing or mismatched shim, an unsafe descriptor referent, or
close/verification failure SHALL disable CLI-auth launch and signer activation
without fallback. The
identity SHALL NOT be reused until the domain has no live descendant. A fixed
shared child UID, directory permissions, global serialization, or
process-group/`setsid` handling alone SHALL NOT satisfy the boundary. Default
and hotreload Dashboard services SHALL use the same repository-owned
namespace-capable seccomp profile plus
`apparmor:unconfined` and `systempaths:unconfined` in default and hotreload
Dashboard, with no `privileged`, `cap_add`, host PID, or Docker socket. Exact-
image namespace/pidfd preflight failure SHALL disable CLI-auth launch and
signer activation without a direct-subprocess fallback. The signer
SHALL be owned by the Dashboard process identity with mode `0400`; tests SHALL
prove the exact signer path is absent from the child mount view and opening it
returns `ENOENT` without reading bytes or disclosing a mounted file.
Dashboard-local model-verification adapter paths SHALL be removed rather than
sandboxed. Provider health, device-auth, API-key-test, Settings Console, and
Secrets aliases SHALL use the sandboxed launcher, and Dashboard Codex prewarm
or any other adapter invocation SHALL be removed or executed outside Dashboard.
The trusted parent SHALL resolve canonical CLI-auth authority before spawn and
stage only the operation-required material into a newly created regular file
under the child-owned per-invocation HOME. Health and API-key-test commands MAY
read that staged copy, but any child modification is discarded and SHALL NOT
write back to canonical authority. Commands used for those read-only checks
SHALL NOT perform credential rotation. A device-auth child SHALL write exactly
one provider credential artifact at its expected relative path. Provider-
specific disposable scratch roots MAY contain the exact database, WAL, log,
configuration, or package files required by that CLI; alternate credential
artifacts and writes outside those roots SHALL fail closed, and all scratch
content SHALL be discarded. Direct-child or outer-Bubblewrap exit SHALL NOT
authorize validation. The launcher SHALL use
`--as-pid-1 --die-with-parent` plus a trusted `--info-fd`/`--block-fd`
handshake, open a pidfd for the reported namespace PID 1 before releasing the
payload, and on every outcome signal and boundedly escalate that pidfd until
death before separately waiting for the direct Bubblewrap child. It SHALL NOT
call `waitid` on the non-child namespace PID 1. The parent SHALL then open the expected relative file
from a trusted staging-root descriptor with
`openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` or behaviorally equivalent
no-follow/beneath constraints, verify the
single-link regular file's owner, mode, size bound, and provider schema, and
consume the bounded bytes through that same open descriptor without a path
reopen. It SHALL then atomically persist through explicit provider authority
with compare-and-set fencing. Symlinks, hardlinks, path escapes, unexpected
files, writes outside the provider scratch allowlist, live or daemonized
 descendants, authority-version races, cancellation, timeouts, inherited-FD
 closure failures, containment failures, or staged-output descriptor failures
 SHALL discard the staging tree without persistence.
Cleanup SHALL run before the exclusive identity is
released after every terminal outcome. The child SHALL never receive or open
the canonical root credential path.
The canonical full-stack launcher MAY start Dashboard before all-butlers, but
the signed client SHALL remain unavailable and SHALL sign nothing until
`GET /_control/runtime-probe/v1/readiness?kid=<kid>` on the private Switchboard
ASGI surface returns HTTP `200` with exactly `{"status":"ready"}`. It SHALL
return HTTP `503` with exactly `{"status":"unavailable"}` unless the requested
key ID matches a loaded verifier that is valid for issuance at the current
integer second: the current entry is eligible only at or after `sign_from`,
while the retiring entry is eligible only at or before `sign_until` and not
after `accept_until`. It SHALL expose no configured key ID or key material,
accept no capability, perform no catalog lookup or runtime launch, and remain
absent from generic MCP discovery. Dashboard's ordinary health SHALL remain
available so `oauth-gate` can start all-butlers without a dependency cycle.
While the signer mount is active, rollback SHALL retain the child sandbox and
disable Test, verify-all, and scheduled verification, or remove the mount before
old code starts. It SHALL NOT start an image lacking the sandbox or restore a
local adapter probe beside the mount.
Dashboard SHALL derive the public key from the private seed. A current signer
SHALL match the current algorithm, ID, derived public key, and `sign_from`,
SHALL have `sign_until=null`, and SHALL sign only at/after `sign_from`. A
retiring signer SHALL match every retiring field through `sign_until`, SHALL
have local `sign_from < sign_until`, and SHALL sign at or before that integer
second. `iat == sign_until` is permitted without any cutover-skew extension.
Switchboard SHALL reject current-key capabilities issued before
`sign_from`, retiring-key capabilities whose integer `iat` is greater than
`sign_until` with no cutover-skew exception, and a retiring key after
`accept_until`. The ordinary request `iat`/`exp` skew checks remain separate. Each process
SHALL validate and snapshot its files at startup and SHALL reload them only on
process restart. A missing, unreadable, malformed, duplicate-key-ID,
algorithm-mismatched, permission-unsafe, or signer/keyring-mismatched snapshot
SHALL disable probe control without taking unrelated Dashboard or daemon
functions down. The application SHALL NOT generate, reconstruct, or persist
deployment key material.

ID: REQ-core-credentials-002
Source: heart-and-soul/security.md; craft-and-care/security-and-secrets.md; RFC 0003; dashboard-model-settings REQ-dashboard-model-settings-001; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Missing signing or verification material fails closed before runtime work

- **WHEN** the Dashboard API cannot read `RUNTIME_PROBE_CONTROL_SIGNING_KEY`
  from its dedicated deployment-secret mount, or Switchboard cannot load the
  corresponding verification key
- **THEN** the requested runtime probe reports the control plane unavailable
  without catalog lookup, runtime launch, or verification persistence
- **AND** it does not use a credential-store value, local value, environment
  fallback, generic MCP route, or unsigned request as a substitute

#### Scenario: Private signing material remains outside runtime, browser, MCP, and generic Secrets surfaces

- **WHEN** the dashboard requests a runtime probe or the scheduler runs one
- **THEN** the server-side dedicated control client signs its short-lived
  capability without sending the private key
- **AND** the all-butlers container, non-Switchboard daemon code, and runtime
  child process cannot read the signing-key mount
- **AND** the browser payload, generic MCP tool list/call, runtime prompt,
  telemetry, logs, and generic Secrets API inventory/detail/mutation/audit
  responses contain no private key or private-key-derived fingerprint

#### Scenario: Every Dashboard runtime CLI child is isolated from the signer

- **WHEN** a production Dashboard deployment mounts the private signer
- **THEN** Test, verify-all, and scheduled verification contain no
  dashboard-local adapter path, and every remaining provider-health,
  device-auth, API-key-test, Settings, or Secrets runtime-CLI child uses the
  exclusive-invocation-identity, cleared-group, `no_new_privs`, child-HOME,
  allowlisted-environment, kernel-containment launcher
- **AND** a behavior-executing container test proves such a child receives
  `ENOENT` when opening the absent signer path and cannot read protected parent
  environment values
- **AND** concurrent adversarial children cannot read or modify a peer staging
  tree or inspect peer process state, and no identity is reused while a process
  remains in its domain
- **AND** an intentionally inheritable descriptor for signer-equivalent secret
  material, canonical authority, or a peer stage is closed before provider code
  executes, while the runtime payload has no descriptor above stderr
- **AND** a child that forks, double-forks, or calls `setsid` cannot survive a
  terminal outcome, mutate staged output after direct-child exit, or cause
  persistence
- **AND** a completeness scan covers direct and aliased Dashboard subprocess
  callsites so no runtime CLI bypasses the launcher
- **AND** rollback retains those protections or removes the mount before an
  older image starts

#### Scenario: Canonical full-stack startup gates signing on verifier readiness

- **WHEN** the canonical default or hotreload launcher restarts the whole stack
  with signer and verifier files provisioned
- **THEN** Dashboard health permits `oauth-gate` and all-butlers startup, but
  the runtime-probe client reports unavailable and signs nothing
- **AND** signing becomes available only after Switchboard's private non-secret
  readiness response confirms the configured signer key ID matches either the
  current verifier at or after `sign_from` or the retiring verifier at or before
  `sign_until` and not after `accept_until`
- **AND** that response uses only the exact `200/ready` or `503/unavailable`
  shape, reveals no configured key ID/material, and performs no receipt, lookup,
  launch, or persistence
- **AND** a missing or mismatched readiness response preserves prior
  verification history and cannot create a runtime child or signed request

#### Scenario: Sandboxed CLI auth health uses a disposable staged copy

- **WHEN** Dashboard runs provider health or API-key testing
- **THEN** the trusted parent stages a validated authority copy in the child
  HOME, the sandboxed child cannot open the canonical path, and all staged
  modifications are discarded without authority writeback

#### Scenario: Sandboxed device auth persists only validated staged output

- **WHEN** a device-auth child succeeds
- **THEN** the trusted parent first proves the complete descendant domain is
  terminated and fenced, then opens and validates the expected no-follow,
  single-link, in-root, owner/mode/size/schema-valid output and consumes it
  through that same descriptor before explicit compare-and-set persistence
- **AND** any path escape, link, unexpected file, authority race, cancellation,
  timeout, surviving descendant, containment, or validation failure persists
  nothing and cleans the staging tree before releasing the invocation identity

#### Scenario: Generic Secrets API cannot create a shadow signing key

- **WHEN** a browser-facing generic Secrets API caller inventories, fetches,
  mutates, or attempts to create `RUNTIME_PROBE_CONTROL_SIGNING_KEY`
- **THEN** the key is absent from inventory/detail responses and mutation is
  rejected as reserved for deployment-secret control use
- **AND** no private-key value or fingerprint is added to an API or audit response

#### Scenario: Signed capability is scoped, short-lived, and single use

- **WHEN** the dashboard control client or registered scheduler sends a probe
  request to Switchboard
- **THEN** its Ed25519/`EdDSA` signed capability binds the fixed control-plane
  audience, catalog entry ID, caller class, issue/expiry time of at most one
  minute, 256-bit nonce, and protected key ID
- **AND** Switchboard accepts only the configured current or retiring key for
  that key ID, verifies the fixed algorithm, signature, audience, caller class,
  and bounded time claims, then atomically inserts a SHA-256 nonce digest into
  a durable unique receipt and commits it before catalog lookup, runtime launch,
  or verification persistence
- **AND** that receipt retains the digest through at least `exp + 5s`, stores
  neither the raw nonce nor signature, and cleanup cannot remove it before the
  capability's accepted lifetime has ended
- **AND** an invalid, expired, audience-mismatched, or replayed capability is
  rejected without a probe

#### Scenario: Concurrent use of one capability yields one probe

- **WHEN** two Switchboard control requests concurrently present the same
  otherwise valid signed capability
- **THEN** the durable unique nonce-digest insert permits exactly one request
  to proceed beyond receipt creation
- **AND** the losing request is rejected as a replay without catalog lookup,
  runtime launch, or verification persistence

#### Scenario: Algorithm and clock validation fail before receipt creation

- **WHEN** a capability has `alg=none`, a symmetric or unsupported asymmetric
  algorithm, dynamic key-discovery header, invalid Ed25519 signature, an
  unknown protected key ID, a future `iat` beyond five seconds, expired `exp`
  beyond five seconds, or an issue/expiry interval outside `(0, 60]` seconds
- **THEN** Switchboard rejects it before creating a receipt, catalog lookup,
  runtime launch, or verification persistence

#### Scenario: Key rotation preserves the fail-closed boundary

- **WHEN** the deployment rotates the runtime-probe signing key
- **THEN** it installs the new public verification key before the Dashboard
  starts signing with its new key ID
- **AND** Switchboard enforces the retiring key's `sign_until` and accepts that
  key only through an `accept_until` 70 seconds to five minutes later, so every
  valid capability issued before cutover can finish its at-most-60-second
  lifetime plus allowed clock skew
- **AND** it rejects unknown key IDs
- **AND** removal of the retiring key does not fall back to a bearer token,
  credential-store value, or unsigned request

#### Scenario: Startup snapshots deployment keys and isolates failure

- **WHEN** Dashboard or all-butlers starts or restarts with its configured
  runtime-probe key file missing, unreadable, malformed, duplicated, using the
  wrong algorithm, or inconsistent with the peer's configured key ID
- **THEN** the affected signed client or coordinator remains unavailable and
  Test/Verify returns typed unavailability while scheduled verification emits
  safe operational telemetry for a degraded skip
- **AND** model verification history remains unchanged
- **AND** unrelated Dashboard and daemon behavior remains available
- **AND** no environment-value, credential-store, database, generic-Secrets,
  generated-key, or unsigned fallback is attempted

#### Scenario: Rotation gates signer use on verifier readiness

- **WHEN** the operator rotates the process-bound deployment key
- **THEN** it chooses cutover `T`, installs a shared keyring whose old key has
  `sign_until=T`, whose new key has `sign_from=T`, and whose old-key
  `accept_until` is within `[T+70s, T+5m]`, and installs a matching old signer
  bounded by `sign_until=T` before cutover
- **AND** the canonical full-stack restart leaves Dashboard signing disabled
  until restarted Switchboard confirms the old configured signer matches the
  still-issuable retiring verifier entry
- **AND** at or after `T`, Dashboard loads the matching new signer through a
  second canonical full-stack restart and again waits until Switchboard
  confirms the new configured signer matches the now-issuable current verifier
- **AND** the old signer cannot issue after `T`, the new signer cannot issue
  before `T`, and a late Dashboard restart creates safe probe unavailability
- **AND** Switchboard rejects old-key capabilities issued after `T` and rejects
  the old key completely after `accept_until`, even if its immutable keyring
  file has not yet been removed
- **AND** a later all-butlers restart removes the expired old entry
- **AND** durable receipts continue to reject a capability consumed before any
  of those restarts

#### Scenario: Pre-cutover old signer becomes ready through the retiring verifier

- **WHEN** the shared rotation keyring is loaded before cutover `T` with the new
  key as current and the old key as retiring, and Dashboard loads the matching
  old signer bounded by `sign_until=T`
- **THEN** the readiness endpoint returns `200/ready` for the old signer's key ID
  while the current integer second is at or before `T` and not after
  `accept_until`
- **AND** it returns `503/unavailable` for that key ID after `T`, without
  disclosing which verifier entry matched or performing receipt, lookup, launch,
  or persistence work
