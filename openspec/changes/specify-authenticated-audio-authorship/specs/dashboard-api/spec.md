## ADDED Requirements

### Requirement: Dashboard recording authorship requires server-held owner authentication
The dashboard ingress SHALL authorize a recording as owner audio only from its existing
server-held owner authentication and request protections, bound to the actual recording bytes
submitted by that authenticated owner and a qualified successful ASR transform. A browser's
`owner`, `origin`, source-kind, backend or success fields SHALL NOT confer authority. A recording
SHALL follow `REQ-audio-authorship-001` through `REQ-audio-authorship-003`; dashboard-channel
discretion bypass SHALL NOT substitute for authentication or erase untrusted content markers.
This contract SHALL NOT authorize arbitrary audio uploads or room recordings as the owner's own
speech merely because they arrive in an authenticated request.

ID: REQ-dashboard-api-072
Source: RFC 0038 D5/D18 proposed audio amendment; owner decision bu-2utmy5; dashboard owner authentication
Scope: v1-mandatory

#### Scenario: Authenticated owner recording succeeds
- **WHEN** the owner submits their dashboard recording through authenticated protected ingress and its bound qualified ASR transform succeeds
- **THEN** the exact transcript is eligible for owner-input admission under the ordinary owner ceiling
- **AND** the original submission identity prevents duplicate admission on request retry

#### Scenario: Browser asserts audio authority without authentication
- **WHEN** an unauthenticated request or a request with invalid protections claims owner audio or a trusted ASR backend
- **THEN** it receives no owner-audio authorization and no owner goal is admitted from those claims

#### Scenario: A room or forwarded clip is submitted through the dashboard
- **WHEN** the owner submits a clip identified as room, forwarded or quoted material rather than their own recording
- **THEN** authentication does not remove its third-party provenance or exposure
- **AND** a missing source binding does not turn an arbitrary upload into an authenticated owner recording
