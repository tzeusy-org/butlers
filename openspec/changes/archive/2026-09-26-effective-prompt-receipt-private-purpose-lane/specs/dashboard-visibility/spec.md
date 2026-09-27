## ADDED Requirements

### Requirement: Protected effective-prompt session door

The dashboard SHALL expose `GET /api/sessions/{id}/prompt` through the existing dashboard
authentication boundary. It SHALL return the exact stored effective system prompt, verified digest,
total UTF-8 bytes, and ordered content-free provenance only on demand. Session list, aggregate, and
ordinary detail responses SHALL NOT include effective prompt content.

#### Scenario: Prompt receipt is retrieved across butlers
- **WHEN** an authenticated dashboard caller requests a known session prompt receipt
- **THEN** the API returns the stored effective prompt and N provenance entries
- **AND** total bytes equals the UTF-8 byte length of the returned prompt and its SHA-256 equals the returned digest

#### Scenario: Prompt receipt source is degraded
- **WHEN** the session is absent from reachable schemas and at least one schema is unreachable
- **THEN** the API returns an unavailable response rather than a definitive not-found result

#### Scenario: Legacy or corrupt receipt is not fabricated
- **WHEN** a legacy session has no receipt or stored receipt verification fails
- **THEN** the API returns an explicit unavailable or corrupt state without synthesizing prompt bytes or provenance

### Requirement: Purpose lane visibility

Session and spend projections that identify an individual dispatch SHALL render its closed purpose
lane as an accessible badge without rendering private source identities or message content.

#### Scenario: Private-content dispatch is visible
- **WHEN** a session or spend row carries `purpose_lane=private_content`
- **THEN** the row renders a `Private content` badge with a non-color-only accessible label
