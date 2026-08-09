## ADDED Requirements

### Requirement: Pre-Launch and Prewarm Codex Auth Synchronization
The Codex adapter SHALL perform live Codex device-auth reconciliation before
evaluating token freshness, creating its isolated HOME directory, launching
the Codex subprocess, or running speculative prewarm when it is supplied a
credential authority. It SHALL retain the existing environment-isolation,
session recording, and same-tier failover behavior. It SHALL revalidate the
authority immediately before each subprocess attempt, and post-operation local
rotation persistence SHALL be fenced by that attempt's captured snapshot.

#### Scenario: Reconciliation precedes a Codex subprocess
- **WHEN** a Codex adapter with a credential store invokes a new runtime
  session and a canonical home directory is available
- **THEN** it SHALL await live device-auth reconciliation before starting the
  Codex CLI subprocess
- **AND** the isolated invocation HOME SHALL link to the reconciled canonical
  auth file

#### Scenario: Unavailable auth synchronization does not consume session time
- **WHEN** credential-authority synchronization is blocked on a pool checkout,
  row lock, or another local reconciliation task
- **THEN** the adapter SHALL bound that best-effort work and treat expiry as an
  unavailable authority result
- **AND** it SHALL continue the existing local-auth subprocess path without
  consuming the session runtime timeout for synchronization

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
- **WHEN** a Codex subprocess changes its canonical `auth.json` during an
  invocation
- **THEN** the adapter SHALL retain its existing post-invocation rotation
  detection and persistence behavior
- **AND** a database-originated reconciliation baseline SHALL not be treated as
  a new CLI-originated rotation

#### Scenario: Internal retries persist the final local rotation
- **WHEN** a Codex invocation retries internally and successive attempts rotate
  the canonical auth file
- **THEN** each later attempt SHALL revalidate the authority before it starts
- **AND** the invocation finalizer SHALL conditionally persist the final file
  state against the last attempt's captured authority snapshot

#### Scenario: Speculative prewarm receives reconciled auth
- **WHEN** the spawner invokes `CodexAdapter.speculative_prewarm()` before the
  normal runtime invocation
- **THEN** the adapter SHALL reconcile canonical auth before `codex login
  status` can run
- **AND** it SHALL finalize afterward using the prewarm's captured authority
  snapshot so a prewarm-caused rotation is durable before the next spawn

#### Scenario: Dashboard login captures its prewarm rotation
- **WHEN** dashboard device authentication succeeds for Codex and its
  post-login prewarm modifies `auth.json`
- **THEN** the dashboard callback SHALL finalize the final file through the
  same authority-safe path using the snapshot captured before that prewarm
- **AND** a concurrent newer dashboard refresh SHALL remain authoritative

#### Scenario: Auth reconciliation does not alter failover safety
- **WHEN** a Codex invocation still fails before side effects after
  reconciliation
- **THEN** the spawner SHALL classify and handle that failure using the
  unchanged model-failover contract
