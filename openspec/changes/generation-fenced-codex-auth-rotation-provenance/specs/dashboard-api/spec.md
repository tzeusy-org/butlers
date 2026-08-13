## ADDED Requirements

### Requirement: Dashboard Codex Mutations Use Shared Generation Precedence

The dashboard SHALL route its Codex CLI-auth save/rotate,
reauthorization/device-auth, probe, and revoke paths through the selected
system-global generation-fenced authority boundary used by runtime code.  Direct owner save/rotate
and revoke are serialized owner mutations; device-auth and probe completion are
conditional exact-operation outcomes.  A route SHALL fail closed before child
launch or mutation when the selected authority is unavailable or unprovable.

The existing API shapes remain value-focused only where already documented.  No
route shall add a generation ID, operation ID, operation state, lineage,
credential-derived fingerprint/digest, provider stderr, or secret-bearing error
to an HTTP response, audit note, browser payload, or log.

#### Scenario: Dashboard save supersedes an in-flight runtime operation
- **WHEN** the owner saves a valid Codex replacement while a runtime operation
  is launched on the prior generation
- **THEN** the save atomically creates the new shared generation and
  supersedes the in-flight operation
- **AND** the API response exposes no internal generation or operation detail

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
prepare a durable device-auth operation against the exact current generation,
or against an explicitly absent uninitialized authority only for the documented
owner bootstrap path.  After containment and strict staged-output validation,
the callback SHALL conditionally complete that same operation.  It SHALL not
use the dashboard session ID, device code, child PID, local file timestamp, or
staged output identity as authorization.

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
