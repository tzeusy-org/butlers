## MODIFIED Requirements

### Requirement: Pre-Launch and Prewarm Codex Auth Synchronization

The control-plane goal runner SHALL select the system-global credential
authority and dispatch Codex execution to the session host over its
authenticated control channel. The session host SHALL have no database or
credential-store access, domain/connector credentials, or inbound dashboard
port. Domain daemons SHALL not receive goal provider credentials or runtime
OAuth volumes. The session-host Codex adapter SHALL use control-plane fenced
reconciliation before evaluating token freshness, creating
an isolated HOME, launching any subprocess attempt, or running speculative or
on-path prewarm. For each child the trusted control-plane caller SHALL prepare
a durable exact-generation operation, stage its transient `repr`-safe authority
document in a distinct private session-host stage over the authenticated channel,
and conditionally mark that operation launched immediately before process
creation. The prepared host launcher SHALL refuse the child if that mark fails. Every child
SHALL also execute inside kernel-enforced per-invocation isolation with a unique
leased outer UID/GID and distinct user, mount, PID, IPC, and UTS namespaces;
only its own stage is mounted as HOME. Distinct paths, mode `0600`,
`no_new_privs`, or a process group alone SHALL NOT satisfy peer isolation.
Goal adapters SHALL drop the Codex
`--dangerously-bypass-approvals-and-sandbox` flag, require deny-all built-in
and provider-hosted tools, and use strict broker-only MCP. The separate
engineering executor's permission policy SHALL NOT authorize goal invocations.
An adapter unable to prove these restrictions for its runtime/model tuple
SHALL be ineligible. The child SHALL not link to or use a mutable shared
canonical `auth.json` as its authority or result source.

The adapter SHALL preserve the existing provider execution timeout and use the
existing bounded setup/finalizer allowance without allowing retries or child
chunks to extend the operation's one absolute authority deadline.  A missing,
unprovable, expired, or unavailable authority is a fail-closed launch result,
not permission to use a schema-local credential or local file. It SHALL retain
the existing environment-isolation, session-recording, and same-tier failover
safety, including the prohibition on replaying an attempt that may have caused
side effects.

ID: REQ-core-spawner-011
Source: RFC 0038 D3; approved opaque-generation Option-A authority and generation-fenced-codex-auth-rotation-provenance design D0-D10
Scope: v1-mandatory

#### Scenario: Exact-generation reconciliation precedes a Codex subprocess
- **WHEN** a Codex adapter with an explicit system-global authority invokes a
  new runtime session and is about to create a subprocess
- **THEN** it prepares and stages the current exact generation before the child
  can start
- **AND** it conditionally marks that operation launched immediately before
  process creation
- **AND** replacement, revoke, expiry, or unavailable authority causes a safe
  no-launch result rather than a schema-local or local-file fallback

#### Scenario: Private stages prevent cross-daemon result attribution
- **WHEN** two control-plane launches dispatch concurrent session-host Codex
  operations on the same current generation
- **THEN** each child receives a distinct private auth stage seeded from that
  generation
- **AND** behavior-executing peer read and peer write attempts against the
  other's host stage fail under the kernel boundary
- **AND** neither child can make the other's local/staged file appear as its
  own successor result

#### Scenario: Unavailable authority no longer permits local launch fallback
- **WHEN** a selected system-global authority cannot prepare a complete current
  generation binding before a Codex subprocess or prewarm
- **THEN** the adapter creates no Codex child for that operation
- **AND** it does not use an existing canonical local auth file as fallback

#### Scenario: Bounded auth setup remains outside provider execution time
- **WHEN** a credential-wired runtime declares a finite setup/finalizer
  allowance for an invocation
- **THEN** the Spawner and direct dispatcher guard add that allowance outside
  the catalog-resolved provider execution timeout
- **AND** the unmodified catalog timeout still bounds the provider subprocess
- **AND** prepare, private-stage creation, projection lock acquisition,
  immediate pre-spawn marking, and finalization share one absolute authority
  deadline that retries or child chunks cannot extend

#### Scenario: Internal retries receive separate fenced attempts
- **WHEN** a Codex invocation performs an internal retry before any prohibited
  side effect
- **THEN** each subprocess attempt prepares and launches its own exact-
  generation operation and private stage
- **AND** each attempt finalizes only its own stage without treating another
  attempt's result as a successor

#### Scenario: Speculative prewarm is independently fenced
- **WHEN** the spawner invokes speculative or on-path Codex prewarm
- **THEN** the prewarm prepares and marks its own exact-generation operation
  before its status child starts
- **AND** any prewarm rotation is conditionally finalized through that
  operation before a later spawn can consume a successor

#### Scenario: Dashboard login prewarm retains the device-auth fence
- **WHEN** successful dashboard Codex device authentication triggers a
  post-login prewarm
- **THEN** the prewarm uses a new current-generation operation rather than the
  completed device-auth operation or a shared local-file baseline
- **AND** a concurrent owner replacement remains authoritative

#### Scenario: Auth fencing does not alter failover safety
- **WHEN** a Codex attempt fails before side effects after exact-generation
  reconciliation
- **THEN** the spawner classifies and handles it using the existing model-
  failover contract
- **AND** authority finalization failure never authorizes replay of a child
  that may have caused side effects

#### Scenario: Reconciliation precedes a Codex subprocess
- **WHEN** the control-plane runner dispatches a Codex goal invocation with a
  selected system-global credential authority to the session host
- **THEN** it SHALL await exact-generation reconciliation and successful launch
  marking before starting the Codex CLI subprocess
- **AND** the isolated invocation HOME SHALL contain only its operation-private
  staged credential, never a link to the shared canonical auth file

#### Scenario: Unavailable auth synchronization does not consume session time
- **WHEN** credential-authority synchronization is blocked on a pool checkout,
  row lock, or another local reconciliation task
- **THEN** the control-plane caller SHALL bound that work and treat expiry as
  an unavailable authority result without consuming provider execution time
- **AND** it SHALL create no Codex child or local-auth fallback and preserve
  the existing bounded setup/finalizer allowance and failover safety

#### Scenario: Bounded auth setup is outside provider execution time
- **WHEN** a credential-wired runtime declares a finite setup/finalizer
  allowance for an invocation
- **THEN** the Spawner and direct `DiscretionDispatcher` guard SHALL add that
  allowance outside the catalog-resolved provider execution timeout
- **AND** the unmodified catalog timeout SHALL still be passed to the runtime
  adapter and bound its provider subprocess
- **AND** all preflight, on-path prewarm, refresh-lock acquisition, immediate
  pre-spawn, and post-operation authority synchronization for one Codex
  invocation SHALL share that single allowance

#### Scenario: Existing local CLI rotation persistence remains intact
- **WHEN** a Codex subprocess changes its operation-private staged `auth.json`
  during a session-host invocation
- **THEN** after complete descendant termination the control-plane caller SHALL
  conditionally persist the strictly validated rotation through that exact
  launched operation while its generation remains current and unexpired
- **AND** a database-originated reconciliation baseline SHALL not be treated as
  a new CLI-originated rotation

#### Scenario: Internal retries persist the final local rotation
- **WHEN** a Codex invocation retries internally before prohibited side effects
  and successive attempts rotate their operation-private auth stages
- **THEN** each later attempt SHALL revalidate the current authority and prepare
  and mark its own operation before it starts, within the original deadline
- **AND** each finalizer SHALL conditionally persist only its own contained,
  validated result against that operation's captured generation; the final
  attempt cannot attribute an earlier or shared file to itself

#### Scenario: Speculative prewarm receives reconciled auth
- **WHEN** the spawner invokes `CodexAdapter.speculative_prewarm()` before the
  normal runtime invocation
- **THEN** the control-plane caller SHALL reconcile the current generation,
  stage it on the session host, and mark that prewarm's own operation before
  `codex login status` can run
- **AND** it SHALL finalize afterward through that exact operation so an
  eligible prewarm-caused rotation is durable before the next spawn

#### Scenario: Dashboard login captures its prewarm rotation
- **WHEN** dashboard device authentication through the control-plane service
  listener succeeds on the session host and its post-login prewarm modifies
  that prewarm's private `auth.json`
- **THEN** the control-plane callback SHALL finalize that validated stage through
  the prewarm's own exact launched operation and captured generation
- **AND** a concurrent newer dashboard refresh SHALL remain authoritative

#### Scenario: Auth reconciliation does not alter failover safety
- **WHEN** a Codex invocation still fails before side effects after
  reconciliation
- **THEN** the spawner SHALL classify and handle that failure using the
  unchanged model-failover contract
## ADDED Requirements

### Requirement: Codex Adapter Finalization Uses the Launch Operation

The control-plane caller SHALL, after a session-host Codex child's complete
descendant domain has been terminated and its own private stage has been
strictly validated, finalize only through the operation that launched that
child. The authenticated channel SHALL carry that result to the trusted caller;
the session host SHALL neither access the database nor authorize completion
from a local file or operation identifier alone. A valid changed stage is a
candidate successor, not authority by itself.  The adapter SHALL pass only a safe
classified health outcome; it SHALL not persist provider stderr, raw exception
text, a token-derived digest, file identity, or process identity as health or
provenance.

The adapter SHALL discard the private stage and terminalize safely when the
child is cancelled, times out, produces malformed output, cannot prove stage
containment, loses its generation, or cannot persist finalization.  It SHALL
retain existing same-tier failover safety: auth fencing neither fabricates a
successful runtime result nor causes an operation that might have side effects
to be replayed.

Before launch, stage preparation, cancellation, marking, or process-creation
failure SHALL invoke the guarded abandonment operation with a closed reason.
After launch, cancellation, containment, parsing, or persistence failure SHALL
invoke that same abandonment operation only after the complete child namespace
is proven dead. Duplicate abandonment SHALL be non-committing.

ID: REQ-core-spawner-012
Source: RFC 0038 D3; approved opaque-generation Option-A authority and generation-fenced-codex-auth-rotation-provenance design D0-D10
Scope: v1-mandatory

#### Scenario: Winning stage creates one conditional successor
- **WHEN** a completed runtime child has a valid operation-private staged
  result and its launch generation is still current
- **THEN** the adapter submits that result once to the guarded operation
  completion path
- **AND** only the completion transaction may create the successor generation

#### Scenario: Stale completed child does not update the replacement
- **WHEN** a child completes after a dashboard replacement has superseded its
  launch generation
- **THEN** the adapter discards its staged result and withholds its health
  outcome from the replacement
- **AND** the next invocation prepares a fresh operation from the replacement

#### Scenario: Containment or parser failure never promotes a local file
- **WHEN** a child stage is malformed, ambiguous, outside the approved stage,
  or cannot be read after complete child termination
- **THEN** the adapter records only a safe terminal outcome and discards the
  stage
- **AND** it does not promote the result, inspect a shared local file, or retry
  the operation as a successor

#### Scenario: Launch failure abandons without an unauthorized child
- **WHEN** stage preparation, launch marking, or process creation fails before
  a Codex child is successfully created
- **THEN** the exact prepared operation is abandoned through the guarded closed-
  reason path
- **AND** it writes no successor or health and exposes no peer stage

### Requirement: Codex Projection Lock Is Not a Fallback Authorization

The system SHALL keep any compatibility canonical Codex projection only on
the session-host credential volume, transported through the control plane's
service listener and authenticated control channel. It SHALL write only a
database-originated complete current authority under the cross-process
projection lock and atomic `0600` replacement. No domain daemon SHALL receive
the provider credential, and no goal HOME SHALL link to the shared projection.
Failure to obtain or safely use that lock SHALL fail the associated operation before child launch;
the component SHALL not proceed unlocked.  Runtime children and their
finalization SHALL not depend on that projection as a mutable authority source.

ID: REQ-core-spawner-013
Source: RFC 0038 D3; approved opaque-generation Option-A authority and generation-fenced-codex-auth-rotation-provenance design D0-D10
Scope: v1-mandatory

#### Scenario: Projection contention does not permit unlocked launch
- **WHEN** a component that still requires the canonical projection cannot
  acquire its cross-process lock within the operation's setup allowance
- **THEN** it does not launch a Codex child against an unlocked projection
- **AND** it returns a safe unavailable outcome without changing authority
