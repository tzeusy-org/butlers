## ADDED Requirements

### Requirement: Dashboard Codex Mutations Use Shared Generation Precedence

The dashboard SHALL route its Codex CLI-auth save/rotate,
reauthorization/device-auth, probe, and revoke paths through the selected
system-global generation-fenced authority boundary used by runtime code.  Direct owner save/rotate
and revoke are serialized owner mutations; device-auth and probe completion are
conditional exact-operation outcomes.  A route SHALL fail closed before child
launch or mutation when the selected authority is unavailable or unprovable.

The existing owner-only Codex rotate response SHALL remain the documented
one-time `{fingerprint, value}` shape, and Codex inventory/detail responses
SHALL retain their on-read display `fingerprint` without a raw value. The raw
rotate value SHALL be returned only in that existing one-time response. The
display fingerprint SHALL remain derived on read and unpersisted; neither field
is operation provenance or authority. No route SHALL add a generation ID,
operation ID, operation state, lineage, provider stderr, secret-bearing error,
or any new credential-derived identifier to an HTTP response, audit note,
browser payload, or log.

#### Scenario: Dashboard save supersedes an in-flight runtime operation
- **WHEN** the owner saves a valid Codex replacement while a runtime operation
  is launched on the prior generation
- **THEN** the save atomically creates the new shared generation and
  supersedes the in-flight operation
- **AND** the API response exposes no internal generation or operation detail

#### Scenario: Codex rotate preserves the existing owner response contract
- **WHEN** the owner rotates or pastes a valid Codex credential through the
  existing CLI rotate endpoint
- **THEN** the response contains exactly the existing one-time raw value and
  its display-only fingerprint fields
- **AND** the response contains no generation, operation, lineage, or other
  provenance field

#### Scenario: Codex inventory and detail remain display-only
- **WHEN** the owner reads Codex credential inventory or detail after a
  generation-fenced replacement
- **THEN** the response may contain the existing on-read display fingerprint
  and SHALL NOT contain the raw credential value
- **AND** it contains no generation, operation, lineage, or reusable
  credential capability

#### Scenario: Dashboard revoke blocks device-auth resurrection
- **WHEN** the owner revokes Codex auth while a dashboard device-auth session
  is awaiting or finalizing a result
- **THEN** revoke makes the current authority absent and the device-auth
  completion fails safely
- **AND** the device-auth response does not reveal the stale operation or
  staged credential

#### Scenario: Probe outcome is withheld after a replacement
- **WHEN** a dashboard Codex health probe launched on generation `G` completes
  after a replacement has made another generation current
- **THEN** the HTTP probe may report its own safe execution outcome but it
  attaches no health/history/audit state to the replacement
- **AND** it exposes no raw provider error or generation provenance

### Requirement: Dashboard Device Authentication Has a Durable Prelaunch Fence

Before the dashboard's Codex device-auth sandbox starts a child, it SHALL
prepare a durable device-auth operation against the exact current generation
through the normal operation entry point, or call the distinct
owner-authorized bootstrap entry point for the documented never-initialized
owner bootstrap path. The session SHALL carry that preparation into a
two-phase sandbox launch: the platform launcher first creates and seeds only
that operation's private stage, the operation is conditionally marked launched,
and only then may the same prepared sandbox create its child. After complete
containment and strict staged-output validation, the callback SHALL
conditionally complete that same operation and remove its stage. It SHALL not
use the dashboard session ID, device code, child PID, local file timestamp, or
staged output identity as authorization.

The prepared sandbox SHALL use the same kernel-enforced per-invocation boundary
as runtime children: a unique leased outer UID/GID and distinct user, mount,
PID, IPC, and UTS namespaces with only the owning stage mounted. Stage
preparation, prelaunch cancellation, failed marking, process-launch failure,
and contained post-launch failures SHALL call the explicit guarded abandonment
operation with a closed reason; duplicate abandonment SHALL be non-committing.

#### Scenario: First owner device auth bootstraps one current generation
- **WHEN** the selected shared Codex authority is explicitly absent with no
  prior generation and the owner completes a valid contained device-auth flow
- **THEN** the guarded bootstrap writes the existing shared credential row and
  creates one fresh current opaque generation atomically
- **AND** no local file or dashboard session value becomes persistent authority

#### Scenario: Malformed device-auth output fails without a secret error
- **WHEN** a Codex device-auth child output is malformed, ambiguous, or cannot
  be proven contained after child termination
- **THEN** the dashboard marks the session failed with a safe generic result
- **AND** it persists no credential, derived fingerprint/digest, raw parser
  error, or operation detail

#### Scenario: A failed prelaunch mark starts no sandbox child
- **WHEN** replacement, revoke, expiry, or unavailable authority invalidates a
  prepared Codex device-auth operation after its private stage is created but
  before launch marking succeeds
- **THEN** the platform launcher creates no device-auth child
- **AND** the prepared stage and operation are terminalized without exposing
  their contents or identifiers

#### Scenario: Device-auth launch failure uses guarded abandonment
- **WHEN** a prepared device-auth stage cannot be created, is cancelled before
  launch, fails conditional marking, or cannot create its sandbox child
- **THEN** no unauthorized child starts and the exact operation is abandoned
  with the matching closed reason
- **AND** no successor, health outcome, peer-stage access, or internal ID is exposed
