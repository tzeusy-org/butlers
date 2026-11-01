## MODIFIED Requirements

### Requirement: Relationship Butler Commitment Extraction

A goal admitted from an owner conversation SHALL detect explicit first-person
commitment patterns (e.g., "I'll send", "I promised", "I need to follow up")
using Relationship guidance and SHALL create commitment-class owner conditions
through Relationship capabilities with the appropriate counterparty entity,
direction, and confidence. Only explicit statements with confidence >= 0.8
SHALL be auto-created. An exposed invocation SHALL NOT create a commitment with
owner authority.

ID: REQ-commitment-lifecycle-007
Source: heart-and-soul/vision.md (mental labor absorption); RFC 0038 D2, D6 and D7
Scope: v1-mandatory

#### Scenario: Explicit promise detected and committed

- **WHEN** the owner says "I'll send Sam that book tomorrow" in a conversation
  admitted as an owner goal with a resolved entity for "Sam"
- **THEN** the goal creates, through Relationship capabilities, a commitment
  with `kind: "promise"`, `direction: "owner_to_other"`,
  `counterparty_entity_id: <sam-uuid>`, `confidence: 0.9`, and
  `deadline: <tomorrow>`

#### Scenario: Ambiguous statement does not create a commitment

- **WHEN** the owner says "I should probably get around to calling Sam" in a
  conversation admitted as an owner goal
- **THEN** no commitment is created (confidence below 0.8 for a conditional
  hedging statement)

#### Scenario: Owner resolves a commitment via conversation

- **WHEN** the owner says "I sent Sam the book" and an active commitment
  matching that action and counterparty exists
- **THEN** the goal calls the `resolve_owner_condition` capability with
  `resolution_reason: "satisfied"`

#### Scenario: Quoted third-party text does not create an owner commitment

- **WHEN** a commitment-like statement reaches a goal only through forwarded or
  quoted third-party content, so the invocation is exposed
- **THEN** the Relationship capability refuses an owner-authority commitment
  write from that invocation

### Requirement: Resolution Provenance

Every commitment resolution SHALL record `evidence_closed` in the row's
metadata, containing the resolution source (owner_confirmed, evidence_observed,
expired, cancelled, superseded), the goal ID and invocation ID from the
verified assertion lineage if resolved from a goal invocation, and a detail
string. No silent resolution — a commitment SHALL NOT transition to `resolved`
without `evidence_closed` being populated. Evidence recorded before cutover
SHALL keep its session ID unchanged.

ID: REQ-commitment-lifecycle-008
Source: RFC 0026 §3, JARVIS Run 08 "Accepted is not completed"; RFC 0038 D7 and D17
Scope: v1-mandatory

#### Scenario: Resolution without evidence is rejected

- **WHEN** `resolve_commitment()` is called without `evidence_closed`
  containing at least a `source` field
- **THEN** it raises a validation error without touching the database

#### Scenario: Resolved commitment retains opening and closing evidence

- **WHEN** a resolved commitment is queried
- **THEN** the row's metadata contains both `evidence_opened` (creation
  provenance) and `evidence_closed` (resolution provenance) with goal and
  invocation IDs and timestamps
