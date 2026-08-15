## MODIFIED Requirements

### Requirement: Pre-Launch and Prewarm Codex Auth Synchronization

The Codex adapter SHALL use an explicitly selected system-global credential
authority for live reconciliation before evaluating token freshness, creating
an isolated HOME, launching any subprocess attempt, or running speculative or
on-path prewarm. For each child it SHALL prepare a durable exact-generation
operation, write the transient `repr`-safe authority document into a distinct
private stage, conditionally mark that operation launched immediately before
process creation, and refuse the child when that recheck fails. Every child
SHALL also execute inside kernel-enforced per-invocation isolation with a unique
leased outer UID/GID and distinct user, mount, PID, IPC, and UTS namespaces;
only its own stage is mounted as HOME. Distinct paths, mode `0600`,
`no_new_privs`, or a process group alone SHALL NOT satisfy peer isolation. The
Codex CLI's `--dangerously-bypass-approvals-and-sandbox` flag SHALL remain
inside this outer boundary. The child SHALL not use a mutable shared canonical
`auth.json` as its authority or result source.

The adapter SHALL preserve the existing provider execution timeout and use the
existing bounded setup/finalizer allowance without allowing retries or child
chunks to extend the operation's one absolute authority deadline.  A missing,
unprovable, expired, or unavailable authority is a fail-closed launch result,
not permission to use a schema-local credential or local file. It SHALL retain
the existing environment-isolation, session-recording, and same-tier failover
safety, including the prohibition on replaying an attempt that may have caused
side effects.

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
- **WHEN** two daemons launch Codex operations on the same current generation
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

## ADDED Requirements

### Requirement: Codex Adapter Finalization Uses the Launch Operation

The adapter SHALL, after a Codex child has been completely terminated and its
own private stage has been strictly validated, finalize only through the
operation that launched that child.  A valid changed stage is a candidate
successor, not authority by itself.  The adapter SHALL pass only a safe
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

The system SHALL require any compatibility component needing a canonical local
Codex projection to write only a database-originated complete current authority
under the cross-process projection lock and atomic `0600` replacement.  Failure to obtain
or safely use that lock SHALL fail the associated operation before child launch;
the component SHALL not proceed unlocked.  Runtime children and their
finalization SHALL not depend on that projection as a mutable authority source.

#### Scenario: Projection contention does not permit unlocked launch
- **WHEN** a component that still requires the canonical projection cannot
  acquire its cross-process lock within the operation's setup allowance
- **THEN** it does not launch a Codex child against an unlocked projection
- **AND** it returns a safe unavailable outcome without changing authority
