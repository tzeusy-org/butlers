## ADDED Requirements

### Requirement: Opaque System-Global Codex Authority Generation

The system SHALL bind the existing system-global `cli-auth/codex` credential
row to exactly one opaque, database-generated authority generation whenever
that credential is valid and current.  The binding SHALL consist of a singleton
current-generation state, the existing credential row's matching opaque
generation reference, and an append-only opaque generation record.  The raw
credential SHALL remain only in the existing Tier 1 credential row; the
generation/state/provenance records SHALL contain no raw credential,
credential-derived fingerprint or digest, credential-shaped metadata, secret
error, PID, local-file identity, timestamp heuristic, or capability.

An absent or revoked authority SHALL have no current generation.  A missing,
malformed, duplicated, stale, or inconsistent state/generation/credential
binding is unprovable and SHALL be unavailable for new Codex launch,
rotation, health attachment, or automatic repair.  The system SHALL not infer
authority from a local runtime file, process cache, process identifier,
timestamp, session identifier, or a matching-looking document.

#### Scenario: Valid shared authority receives an opaque generation
- **WHEN** the selected system-global pool has one valid `cli-auth/codex`
  credential and no current Codex generation has ever been initialized
- **THEN** the guarded authority path creates one random opaque
  `legacy_adoption` generation and atomically binds it to the existing row and
  singleton current pointer
- **AND** no new durable record contains the credential or a
  credential-derived identifier

#### Scenario: Inconsistent authority evidence fails closed
- **WHEN** the selected system-global pool has a missing state row, a missing
  generation row, a mismatched/null row binding, duplicate singleton evidence,
  a malformed credential, or a current generation without its matching
  credential row
- **THEN** the authority path returns a safe unavailable/unprovable result
  before a Codex child can launch
- **AND** it does not repair, promote, hash, or copy a local runtime file

#### Scenario: Existing initialized lineage cannot silently adopt a legacy write
- **WHEN** a Codex authority has previously had a generation and a later
  generic/legacy write leaves the reserved credential binding absent or
  inconsistent
- **THEN** the authority is unprovable rather than eligible for another
  automatic adoption
- **AND** only an explicit owner replacement or explicit device-auth bootstrap
  can establish a new current generation

### Requirement: Generation-Fenced Codex Operation Completion

The credential store SHALL prepare every Codex runtime invocation, prewarm,
device-auth flow, and health probe as a durable internal operation bound to the
exact current opaque generation.  An operation record SHALL be a server-side
row identity, not a bearer capability, and SHALL not be exposed through an API,
MCP surface, browser payload, command line, log, or telemetry attribute.

The only exception to a current-generation binding is an explicitly requested
first device-auth bootstrap when the guarded singleton is absent, has never
initialized, has no generation record, and has no eligible credential row. The
operation SHALL record that exact never-initialized absence as an internal,
non-capability bootstrap state; it SHALL not use a null generation, dashboard
session, device code, local file, or time observation as an authority proof.
Once any generation has existed, including after revoke, an absent authority
is not bootstrap-eligible.

An operation may launch only after a conditional recheck proves that it is
prepared, unexpired, bound to the expected generation, and that generation is
still current.  Its completion may attach a health outcome or create a new
successor only when the same conditions still hold and the operation is in its
single launched state.  The successor write SHALL atomically replace the
existing raw credential row, bind a new opaque generation, update the current
pointer, retire the prior generation, reset inherited health, and terminalize
the operation.  A duplicate, stale, expired, malformed, wrong-kind, or
otherwise unprovable operation SHALL not write a successor or health result.

#### Scenario: Runtime rotation is conditional on its exact launch generation
- **WHEN** a runtime operation launched on current generation `G` returns a
  strictly validated staged successor while `G` remains current and the
  operation remains launched and unexpired
- **THEN** one transaction creates a fresh opaque generation `G2`, writes the
  successor only to the existing Tier 1 row, binds `G2` as current, and marks
  the operation completed with `G2`
- **AND** the old credential health state is cleared in that same transaction

#### Scenario: Stale operation cannot overwrite an owner replacement
- **WHEN** an owner replacement or revoke changes the current generation after
  an operation on `G` launched but before it completes
- **THEN** the operation is terminalized as superseded without writing a
  successor or health result
- **AND** the owner mutation remains the current authority

#### Scenario: A duplicate completion does not replay a successor
- **WHEN** a terminal Codex operation is submitted for completion again with
  the same or different staged result
- **THEN** the operation returns a safe non-commit result
- **AND** it does not create another generation, modify the current credential,
  or attach another health outcome

#### Scenario: Health is bound but does not create authority
- **WHEN** a launched operation reports a safe health outcome without a valid
  successor while its exact generation is still current
- **THEN** the outcome is recorded only against that current credential
- **AND** no generation pointer, generation row, or credential value changes

#### Scenario: Bootstrap cannot reauthorize a previously initialized absence
- **WHEN** the current Codex authority is absent after a prior generation or
  revoke
- **THEN** a device-auth prepare request returns a safe unavailable result
- **AND** it does not create an operation that can recreate authority

#### Scenario: Bootstrap has no unchanged health completion
- **WHEN** an explicitly absent never-initialized device-auth bootstrap is
  launched but returns no strictly validated successor
- **THEN** the operation terminalizes safely without writing a credential or
  attaching health to an absent authority
- **AND** the state remains eligible only according to the guarded bootstrap
  rule, not a local stage or device session observation

### Requirement: Codex Owner Replacement and Revocation Precedence

The system SHALL serialize dashboard owner replacement, explicit device-auth
bootstrap, runtime rotation, prewarm rotation, and health through the same
system-global authority state.  A direct owner replacement SHALL create a fresh opaque
generation and supersede every nonterminal operation bound to the replaced
generation.  A revoke SHALL atomically make the authority absent and supersede
all nonterminal operations.  Runtime/device successors are conditional only;
the first valid conditional successor that holds the current pointer may win,
while a later direct owner replacement remains intentional current authority.

#### Scenario: Direct owner replacement wins over device auth
- **WHEN** a device-auth operation is prepared on generation `G` and the owner
  saves a replacement before the device-auth result completes
- **THEN** the owner save creates a fresh current generation and supersedes the
  device-auth operation
- **AND** the device-auth result cannot recreate or overwrite the replacement

#### Scenario: Revoke prevents local resurrection
- **WHEN** the owner revokes current generation `G` while a runtime operation
  or local auth file still exists
- **THEN** the current authority becomes absent and operations on `G` are
  superseded
- **AND** no runtime finalizer or later startup may recreate the credential
  from a local file

#### Scenario: Concurrent conditional successors serialize without a clock tie-break
- **WHEN** two launched operations on the same current generation both produce
  valid successors concurrently
- **THEN** exactly one transaction creates the next current generation
- **AND** the other operation is superseded without using child exit time, PID,
  or file metadata to choose a winner

### Requirement: Expiry, Recovery, and Value-Free Codex Provenance

Every Codex operation SHALL receive one absolute deadline from its established
runtime, prewarm/health, or device-auth boundary.  PostgreSQL time may mark an
operation expired, but timestamps SHALL not prove authority or successor
ordering.  A deterministic cleanup path SHALL mark expired nonterminal
operations terminal and delete only terminal operation metadata at least 90
days after terminalization.  Current generations, referenced generations, and
the Tier 1 credential row SHALL not be garbage-collected by that path.

After restart, the system SHALL reconstruct eligibility only from a complete
current shared binding.  It SHALL not complete an operation from an orphaned
stage/local file or infer a successor from a process/cache/file observation.
Generation records remain append-only and value-free in this version; a future
generation-compaction policy requires a separate contract.

#### Scenario: Crash leaves an orphaned local result non-authoritative
- **WHEN** the parent of a launched operation crashes before completion and a
  later process finds a changed local/staged auth file
- **THEN** the later process does not persist or attach that result
- **AND** it restores only a complete current shared authority or fails closed
  until explicit owner action

#### Scenario: Expired operation cannot complete late
- **WHEN** PostgreSQL time has reached an operation's stored absolute deadline
- **THEN** completion is rejected/terminalized as expired before any successor
  or health write
- **AND** the deadline does not determine which generation is authoritative

#### Scenario: Provenance cleanup retains no secret evidence
- **WHEN** the cleanup path removes terminal operation metadata older than the
  retention threshold
- **THEN** it removes only value-free terminal operation rows that are no
  longer required by retained lineage
- **AND** it never logs, returns, or derives a credential identity while doing
  so
