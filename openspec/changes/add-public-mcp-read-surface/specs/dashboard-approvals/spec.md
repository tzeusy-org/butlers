## ADDED Requirements

### Requirement: External fact proposals show a typed review
The approvals dossier SHALL render a pending `relationship_assert_fact` action whose deduplication key starts with `external_mcp:` as a typed review rather than a raw tool-call preview. The review SHALL show the subject and object entity names, types and IDs, the predicate, a label stating that an external AI client proposed it and the client label, and the note and evidence URL, read from `relationship.external_fact_proposals`, as inert, non-clickable text. It SHALL state that an approved fact keeps third-party authority. Approve SHALL submit the exact displayed call through the existing approval route and SHALL offer no "create rule" option. Reject, Defer and owner edit SHALL keep their existing semantics. When an entity can no longer be resolved, approval SHALL be unavailable while Reject stays available.

ID: REQ-dashboard-approvals-904
Source: RFC 0039 D13 step 4; review-parked-entity-merge-approvals typed-review precedent (REQ-dashboard-approvals-902); heart-and-soul/security.md Approval Gates
Scope: v1-mandatory

#### Scenario: The owner sees who and what before approving
- **WHEN** the owner opens a pending external proposal
- **THEN** the dossier SHALL show both entities by name, type and ID, the predicate, and an "External AI client" label with the client label
- **AND** it SHALL show no raw `tool_args` dump

#### Scenario: Evidence links are inert
- **WHEN** the proposal carries an evidence URL and a note
- **THEN** both SHALL render as plain text that does not navigate, fetch or preview the URL

#### Scenario: Approval submits the displayed call
- **WHEN** the owner approves from the typed review
- **THEN** the request SHALL carry the exact displayed call, and a call changed since display SHALL be refused without execution

#### Scenario: No rule can be created from the review
- **WHEN** the owner opens the approve control of a pending external proposal
- **THEN** no "create a standing rule" option SHALL be offered

#### Scenario: Unresolvable entity blocks approval but not rejection
- **WHEN** the subject entity has been merged or deleted since parking
- **THEN** Approve SHALL be unavailable with a stated reason, and Reject SHALL still resolve the action

#### Scenario: Review is keyboard and screen-reader accessible
- **WHEN** the owner reaches the review by keyboard
- **THEN** every field and decision control SHALL be reachable by keyboard and SHALL be named for assistive technology

## Source References

- Non-Negotiable Rule 1 (owner sovereignty), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, Approval Gates: execute the exact stored call the owner saw.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D13.
- RFC 0021 (decision loop), owner edit and decision lifecycle.
- `openspec/changes/review-parked-entity-merge-approvals`, typed review in place of a generic call preview.
