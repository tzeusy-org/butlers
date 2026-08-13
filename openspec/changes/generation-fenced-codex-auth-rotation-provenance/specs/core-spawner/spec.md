## ADDED Requirements

### Requirement: Codex Subprocesses Use Exact-Generation Private Stages

The adapter SHALL prepare every runtime subprocess attempt and
speculative/on-path prewarm with a selected system-global authority as a durable
exact-generation operation before it constructs a child.  The operation's raw
authority document is transient and `repr`-safe only; the adapter SHALL write
it into a private operation stage and SHALL not link the child to a mutable
shared canonical `auth.json` as its result source.  It SHALL conditionally mark
the operation launched immediately before creating the child, and SHALL refuse
the child when that recheck fails.

The adapter SHALL preserve the existing provider execution timeout and use the
existing bounded setup/finalizer allowance without allowing retries or child
chunks to extend the operation's one absolute authority deadline.  A missing,
unprovable, expired, or unavailable authority is a fail-closed launch result,
not permission to use a local file.

#### Scenario: Runtime child is fenced immediately before creation
- **WHEN** a Codex invocation has completed its setup work and is about to
  create a subprocess
- **THEN** it conditionally marks the prepared operation launched against the
  exact current generation before process creation
- **AND** it refuses the child if replacement, revoke, expiry, or unavailable
  authority has made that generation non-current

#### Scenario: Private stages prevent cross-daemon result attribution
- **WHEN** two daemons launch Codex operations on the same current generation
- **THEN** each child receives a distinct private auth stage seeded from that
  generation
- **AND** neither child can make the other's local/staged file appear as its
  own successor result

#### Scenario: Unavailable authority no longer permits local launch fallback
- **WHEN** a selected system-global authority cannot prepare a complete current
  generation binding before a Codex subprocess or prewarm
- **THEN** the adapter creates no Codex child for that operation
- **AND** it does not use an existing canonical local auth file as fallback

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
