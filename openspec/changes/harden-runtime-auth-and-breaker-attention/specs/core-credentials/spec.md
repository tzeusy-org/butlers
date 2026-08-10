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
Source: heart-and-soul/security-and-secrets.md; RFC 0006; core-credentials Live Codex Device-Auth Reconciliation; design.md Decision 1
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
not a shared bearer token. The only accepted capability format SHALL be an
Ed25519 JWS with protected `alg=EdDSA` and protected key ID; the verifier SHALL
resolve that key ID only from its deployment-configured keyring, SHALL NOT
perform remote/dynamic key lookup, and SHALL reject `jku`/`x5u` headers. It
SHALL NOT select an algorithm from an untrusted capability and SHALL reject
`none`, symmetric, and other asymmetric algorithms. The signed capability
SHALL bind a fixed `switchboard.runtime_probe_control.v1` audience, catalog entry ID,
registered caller class, issue/expiry times, 256-bit cryptographically random
single-use nonce, and key ID. The verifier SHALL allow at most five seconds of
clock skew and accept only `iat <= now + 5s`, `exp >= now - 5s`, and
`0 < exp - iat <= 60s`. A private `RUNTIME_PROBE_CONTROL_SIGNING_KEY` SHALL
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
before the Dashboard signer uses its key ID and retain the retiring verifier
for at least 70 seconds after its signer stops (60-second maximum capability
lifetime plus two five-second skew allowances), but no longer than five
minutes. An unknown key ID SHALL fail closed.

ID: REQ-core-credentials-002
Source: heart-and-soul/security-and-secrets.md; RFC 0003; dashboard-model-settings REQ-dashboard-model-settings-001; design.md Decision 2
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
- **AND** Switchboard accepts the retiring verification key for at least 70
  seconds after its signer stops and no longer than five minutes, so a valid
  pre-cutover capability remains valid for its normal accepted lifetime
- **AND** it rejects unknown key IDs
- **AND** removal of the retiring key does not fall back to a bearer token,
  credential-store value, or unsigned request
