## MODIFIED Requirements

### Requirement: Catalog Verify-All API

The dashboard SHALL expose `POST /api/settings/models/verify-all` to
re-verify enabled catalog models in bounded parallelism. The verification core
(`butlers.api.routers.model_settings.run_verify_all_models`) is shared by this
manual endpoint and the hourly automated sweep so both use the same explicit
Codex authority and persistence semantics. The manual endpoint SHALL select
its shared credential pool before constructing `CredentialStore(pool,
system_global_pool=pool)` and pass that authority only to Codex adapter
construction; non-Codex adapter construction retains its existing provider
configuration behavior.

#### Scenario: Verify-all persists completed model probes

- **WHEN** `POST /api/settings/models/verify-all` is called for an enabled
  model whose runtime can be constructed with its required authority
- **THEN** the system issues a 1-token completion with bounded concurrency of
  eight and persists `last_verified_at`, `last_verified_latency_ms`,
  `last_verified_ok`, and `last_verified_error` for that model
- **AND** `last_verified_error` is cleared on success and retains the existing
  safe failure classification on a failed probe
- **AND** the call remains rate-limited to once per minute system-wide and
  appends one `models.verify_all` audit record for the accepted run

#### Scenario: Verify-all parallel execution

- **WHEN** `POST /api/settings/models/verify-all` is accepted
- **THEN** enabled, authority-ready models are probed concurrently with bounded
  concurrency of eight
- **AND** each completed probe persists the canonical verification fields
- **AND** the accepted manual run remains rate-limited and audited once

#### Scenario: Unavailable Codex authority is skipped without poisoning verification evidence

- **WHEN** the shared verification core encounters an enabled Codex entry
  without an explicitly selected system-global Codex `CredentialStore`
  authority
- **THEN** it SHALL neither construct a Codex adapter nor invoke a Codex
  subprocess for that entry
- **AND** it SHALL not write `last_verified_at`, `last_verified_latency_ms`,
  `last_verified_ok`, or `last_verified_error`, preserving the prior catalog
  evidence and routing eligibility
- **AND** the accepted result increments `skipped`, does not increment
  `failed`, and records only a categorical authority-unavailable audit note
  without provider diagnostic text
- **AND** remaining eligible non-Codex and authorized Codex entries retain
  their normal construction, invocation, and persistence behavior

### Requirement: Hourly Automated Verification Sweep

The dashboard-api process SHALL run an hourly background sweep
(`butlers.jobs.model_verify.run_model_verify_loop`, started from the FastAPI
lifespan) that calls the same verification core as the manual endpoint. Each
sweep SHALL construct and pass `CredentialStore(pool, system_global_pool=pool)`
from `DatabaseManager.credential_shared_pool()`; it SHALL not infer Codex
authority from a schema-local or fallback pool.

#### Scenario: Hourly sweep preserves authority-unavailable catalog entries

- **WHEN** the shared credential pool is unavailable at a sweep tick
- **THEN** the sweep is a logged no-op and does not call the verification core
- **AND** it does not write failed verification evidence for any model
- **WHEN** the core reports an authority-unavailable Codex skip
- **THEN** the sweep returns that safe `skipped` count rather than recording a
  false model failure or excluding the entry from routing

#### Scenario: Hourly sweep runs independently of the manual rate limit

- **WHEN** the configured verification interval elapses
- **THEN** the sweep calls the shared verification core directly as the
  registered scheduler caller, outside the manual HTTP rate limit
- **AND** its audit actor distinguishes it from an owner-initiated run

#### Scenario: Sweep sleeps first and tolerates a bad tick

- **WHEN** dashboard-api starts
- **THEN** the sweep sleeps for one interval before its first run
- **AND** one failed or authority-unavailable tick is logged safely and does
  not terminate later intervals

## ADDED Requirements

### Requirement: Catalog Test Uses a Runtime Probe, Not a Dashboard-Local Adapter

The Models API's per-entry test and scheduled verification SHALL invoke a
deterministic Switchboard-owned runtime-probe coordinator. The coordinator
SHALL use the same runtime home, authoritative CLI-auth source, adapter
construction, canonical model identifier, runtime-specific execution mapping,
generated runtime configuration, and runtime arguments as a new daemon
invocation, but SHALL expose no domain MCP tools and SHALL not create routed
dispatch provenance. It SHALL be reached only through an authenticated
dashboard-to-Switchboard control-plane command that is not registered in the
generic MCP/LLM tool surface. Dashboard-triggered Test and Verify requests
SHALL first pass the fail-closed `require_dashboard_owner_control` dependency:
a configured non-empty `DASHBOARD_API_KEY` and constant-time matching
`X-API-Key` header are required, with absent configuration reported as safe
unavailability. The dashboard server SHALL then call Switchboard through a
dedicated `runtime_probe_control` client using a separately scoped system
signed capability produced by `RUNTIME_PROBE_CONTROL_SIGNING_KEY` from a
dedicated Dashboard-only deployment-secret mount, never from `CredentialStore`
or the generic Secrets API. Switchboard SHALL accept only Ed25519 JWS
capabilities with protected `alg=EdDSA` and protected configured key ID, using
its non-secret verification key; it SHALL reject token-selected `none`,
symmetric, and other algorithms. It SHALL require the fixed
`switchboard.runtime_probe_control.v1` audience, matching catalog entry ID,
registered caller class, five-second-skew-bounded `iat`/`exp` interval of at
most one minute, and unused 256-bit nonce on its dedicated internal-control
endpoint. It SHALL atomically commit a SHA-256 nonce-digest receipt before
catalog resolution, runtime launch, or verification persistence, retain it
through at least `exp + 5s`, and never retain a raw nonce or signature. The
private signing key SHALL not be available to the
all-butlers daemon, model sessions, generic MCP clients, the normal MCP client
manager, logs, or any generic Secrets API response. The command accepts only a
catalog entry ID, enforces bounded timeout, per-entry de-duplication, and a
bounded global concurrency cap, and accepts no credential material, prompt,
model override, or runtime arguments from the dashboard. A successful probe
updates verification evidence only; it does not close an open breaker.
The production signing-key mount SHALL be activated only after every Dashboard
runtime-CLI child path is removed or forced through the exclusive
per-invocation identity and kernel-containment launcher required by
core-credentials REQ-core-credentials-002. The cutover of
Test, verify-all, and scheduled verification SHALL remove every
dashboard-local runtime-adapter probe path. The signed client SHALL remain
unavailable and sign nothing during canonical full-stack startup until
Switchboard's private `GET /_control/runtime-probe/v1/readiness?kid=<kid>`
returns the exact `200/ready` response for the matching verifier key ID; its
exact unavailable behavior and no-action/no-disclosure limits are those in
core-credentials REQ-core-credentials-002. Rollback while the mount is active
SHALL retain the child sandbox and make model-verification callers unavailable
rather than restoring a local adapter probe.
The dedicated client SHALL use `POST /_control/runtime-probe/v1` with the
compact capability only in `Authorization: Bearer`. Its exact protected header,
claim names/types, nonce encoding, time validation, and key-selection rules are
those in core-credentials REQ-core-credentials-002. The control response SHALL
preserve safe typed HTTP/status pairs `200/completed`, `401/unauthorized`,
`409/replay`, `429/busy`, `503/unavailable`, and `504/timeout`. Runtime execution SHALL
have a 30-second deadline, global concurrency eight, and per-catalog-entry
concurrency one. Dashboard API mapping SHALL preserve these distinctions rather
than reporting a provider failure or successful test.

ID: REQ-dashboard-model-settings-001
Source: heart-and-soul/security.md; craft-and-care/security-and-secrets.md; core-credentials REQ-core-credentials-002; dashboard-model-settings Catalog Verify-All API and Hourly Automated Verification Sweep; model-catalog REQ-model-catalog-001; design.md Decisions 2 and 6
Scope: v1-mandatory

#### Scenario: Test checks the routed runtime environment

- **WHEN** an operator selects `Test` for a catalog entry or the scheduled
  verification sweep runs
- **THEN** the request is executed by the runtime-probe coordinator using the
  same shared runtime environment, canonical-to-execution mapping, generated
  runtime configuration, and catalog arguments as new daemon work
- **AND** the returned evidence is labelled as a runtime probe rather than a
  routed session result

#### Scenario: Probe control command is not a generic routable tool

- **WHEN** a model session, ordinary MCP client, or unauthenticated caller
  enumerates or invokes Switchboard tools
- **THEN** it cannot discover or invoke the runtime-probe control command
- **AND** only the Dashboard/Scheduler control client with a valid scoped,
  unexpired signed capability can request a bounded probe by catalog entry ID

#### Scenario: Dashboard-triggered probe requires owner control before Switchboard work

- **WHEN** the dashboard owner-control key is absent from configuration, or a
  dashboard caller omits or supplies a wrong `X-API-Key` for Test or Verify
- **THEN** the API returns its safe unavailable or `401` response before it
  contacts Switchboard
- **AND** it launches no runtime and writes no verification evidence

#### Scenario: Direct control-plane callers require the scoped signed capability

- **WHEN** a caller reaches the private runtime-probe command without the
  valid fixed-algorithm signature, fixed audience, matching caller
  class/catalog ID, accepted bounded time claims, unused nonce, and configured
  verification key
- **THEN** Switchboard rejects it before catalog lookup, runtime launch, or
  verification persistence
- **AND** a generic MCP client cannot substitute its normal connection for
  that capability or replay a previously accepted request

#### Scenario: Scheduled verification is a registered trusted control caller

- **WHEN** the scheduled verification sweep requests a runtime probe
- **THEN** it uses the dedicated control client and scoped capability as an
  explicitly registered scheduler caller
- **AND** it does not bypass the command through generic MCP-tool access

#### Scenario: Production signer mount cannot reach an unsandboxed runtime child

- **WHEN** the Dashboard deployment activates its private signing-key mount
- **THEN** Test, verify-all, and scheduled verification use only the dedicated
  signed client and no dashboard-local model-verification adapter can spawn
- **AND** every other Dashboard runtime-CLI child is forced through the
  exclusive per-invocation identity and kernel-containment sandbox, is
  behaviorally denied access to the signer and peer invocations, and has no
  surviving descendant before staged output can be consumed
- **AND** the client signs nothing until Switchboard verifier readiness matches
  its configured signer key ID to a currently issuable current or retiring
  verifier entry
- **AND** a rollback retains the sandbox and disables those callers, or removes
  the mount before legacy code can start

#### Scenario: Probe success does not close a breaker

- **WHEN** a breaker-open entry's runtime probe succeeds
- **THEN** the API persists its verification result without inserting
  `model_dispatch_attempts.success`
- **AND** the list response and Models page continue to show the entry as
  breaker-open until a later routed success is recorded

#### Scenario: Probe coordinator unavailability is honest

- **WHEN** the runtime-probe coordinator is unavailable or cannot establish
  the authoritative runtime environment, exceeds its rate/concurrency limit,
  or reaches its bounded timeout
- **THEN** the test response reports the coordinator as unavailable or degraded
  without overwriting the last successful verification evidence
- **AND** the UI does not show a successful model test or a generic provider
  failure for that condition

### Requirement: Model Breaker Attention Episode Visibility and Reissue

The Models list and per-entry detail API SHALL expose the latest relevant
model-breaker attention episode's sanitized lifecycle state, timestamps, and
safe reason independently from verification and breaker facts. The Models page
SHALL make an `uncertain` episode's one permitted manual reissue deliberate:
it presents a confirmation-gated `Send a new alert` control, disables it while
the request is pending or a successor exists, and immediately reports the new
episode result. Every Models endpoint that exposes attention episode data or
permits reissue SHALL use a fail-closed `require_dashboard_owner_control`
server dependency. That dependency SHALL require a configured non-empty
`DASHBOARD_API_KEY` and a constant-time matching `X-API-Key` header, treating a
valid key as the single dashboard-owner principal; it SHALL not inherit the
dashboard's optional general API-auth behavior. No UI visibility rule is an
authorization substitute. No other attention state offers an automatic resend
control.

ID: REQ-dashboard-model-settings-002
Source: heart-and-soul/vision.md Rule 1; RFC 0005; runtime-attention-outbox REQ-runtime-attention-outbox-003; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Independent operational facts are visible

- **WHEN** the Models page renders a catalog entry with verification evidence,
  an open breaker, and an attention episode
- **THEN** it renders those as distinct labelled facts with canonical status
  indicators and safe timestamps/reasons
- **AND** a runtime-probe success explicitly states that it does not clear the
  breaker

#### Scenario: Manual reissue is server-enforced and idempotent

- **WHEN** an operator confirms `Send a new alert` for an `uncertain` episode
- **THEN** the API creates or returns exactly one successor episode for that
  original episode and returns both safe episode identities and states
- **AND** concurrent or retried submissions cannot create additional
  successors for the same original episode

#### Scenario: Owner-control configuration and credentials are required before observation or delivery

- **WHEN** the owner-control key is absent from server configuration and a
  caller requests attention detail or `Send a new alert`
- **THEN** the API returns a safe `503` owner-control-unavailable response
  without exposing the episode, creating a successor, or invoking delivery
- **WHEN** the configured key is present but a caller omits or supplies a wrong
  `X-API-Key`
- **THEN** the API returns `401` without exposing the episode, creating a
  successor, or invoking delivery
- **WHEN** a caller supplies the matching configured `X-API-Key`
- **THEN** it is the authenticated dashboard-owner principal eligible for the
  existing observation/reissue state checks

#### Scenario: Non-uncertain episode cannot be resent from the Models page

- **WHEN** a caller requests a manual reissue for a pending, sending, sent,
  failed, or already-reissued episode
- **THEN** the API rejects it without creating an episode or external delivery
- **AND** the UI keeps the control absent or disabled with an accessible reason

#### Scenario: Attention observation degradation stays truthful

- **WHEN** the API cannot read the model's attention-episode source
- **THEN** the response marks that observation unavailable rather than
  returning no episode as a proven no-alert state
- **AND** existing verification and breaker fields retain their independently
  available values
