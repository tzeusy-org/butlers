## ADDED Requirements

### Requirement: Tool Exposure Metadata

Module tool metadata SHALL describe canonical identity, owning module/group,
logical namespace, LLM-presentable classification, and `eager` or `deferred`
load posture without registering another handler or changing call-time authority.
Existing argument-sensitivity and approval metadata SHALL remain authoritative.

ID: REQ-core-modules-001
Source: RFC 0027 §Tool Metadata Contract; RFC 0002 §Tool Sensitivity Metadata
Scope: v1-mandatory

#### Scenario: Complete metadata identifies the existing tool

- **WHEN** a module supplies presentation metadata
- **THEN** its canonical name and module owner SHALL match the registered tool
- **AND** a partial declaration, invalid posture, or owner mismatch SHALL be rejected by catalog validation

#### Scenario: Missing metadata retains compatibility classification

- **WHEN** a registered module tool has no presentation declaration
- **THEN** its catalog descriptor SHALL be presentable and eager with incomplete classification
- **AND** catalog construction SHALL leave registration and handler approval behavior unchanged

#### Scenario: Authorization declarations remain descriptive

- **WHEN** metadata carries owner domain, effects, output provenance, resource selectors, selection bounds, safety arguments, approval, idempotency, owner authority, or recipient declarations
- **THEN** the catalog SHALL preserve those declarations without executing them as an invocation policy
- **AND** completeness or grantability metadata SHALL NOT authorize a call or resolve an unknown recipient to the owner

## Source References

- Non-Negotiable Rule 3 (MCP inter-butler communication)
- Non-Negotiable Rule 5 (DB-persisted operational tuning)
- RFC 0027 (catalog and exposure contract; unlanded runtime work succeeds to RFC 0038)
