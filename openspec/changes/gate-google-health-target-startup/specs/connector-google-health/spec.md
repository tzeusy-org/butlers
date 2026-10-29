## ADDED Requirements

### Requirement: Helm startup waits for Health route acceptance

The Helm-managed Google Health connector SHALL wait for Health's route acceptance before starting its polling process, after the existing OAuth and Switchboard startup gates. This deployment gate SHALL leave runtime account discovery and polling profiles unchanged and SHALL NOT add target gates to other connectors.

ID: REQ-connector-google-health-016
Source: vision.md Non-Negotiable Rules 4 and 7; RFC 0008 Internal Observer and Deployment Readiness Boundary; daemon _identity_facts; bu-1jkc89
Scope: v1-mandatory

#### Scenario: Expected Health identity releases startup

- **WHEN** the existing Google Health initialization gates have completed
- **THEN** a further gate SHALL poll `http://butlers-up:41103/internal/control-plane/identity` in both split and unsplit control-plane deployments
- **AND** it SHALL release startup only for HTTP 200 containing a JSON object with `schema_version=butler.control.v1`, `butler_name=health`, an exact integer positive `boot_epoch`, and literal boolean `accepting_routes=true`
- **AND** a transient refusal followed by qualifying readiness SHALL allow startup within the remaining deadline

#### Scenario: Missing or invalid readiness keeps polling stopped

- **WHEN** Health reports HTTP success without the required identity and route acceptance, including false, absent or string readiness, a wrong name or schema, nonpositive or noninteger epoch, malformed JSON, oversized response or unavailable endpoint
- **THEN** the init container SHALL NOT release the connector polling process
- **AND** it SHALL retry within its finite startup deadline without logging response bodies or exception content

#### Scenario: Deadline failure remains bounded

- **WHEN** Health never supplies qualifying readiness before the startup deadline
- **THEN** the gate SHALL exit nonzero, permitting Kubernetes to retry the init container
- **AND** the default overall deadline SHALL be 300 seconds, polling interval five seconds, each entire HTTP attempt at most five seconds and the remaining deadline, and each response at most 2 KiB
- **AND** redirects and environment proxy routing SHALL NOT bypass the configured endpoint

#### Scenario: Startup observation has no recovery authority

- **WHEN** the startup gate succeeds or fails
- **THEN** it SHALL NOT change inboxes, cursors, delivery records, replay state or connector runtime retry behavior
- **AND** its success SHALL NOT claim recovery of already accepted ingestion or guarantee availability after startup

## Source References

- Non-Negotiable Rules 4 (deterministic infrastructure) and 7 (connector transport ownership).
- RFC 0008 Internal Observer and Deployment Readiness Boundary; existing daemon identity facts.
