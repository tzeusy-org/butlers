## ADDED Requirements

### Requirement: Reviewed entity merges consume the original parked call

Owner-authenticated dashboard approval SHALL support a dedicated safe review of Relationship-owned `memory_entity_merge` and legacy `entity_merge` requests, bound to the original stored tool identity and complete argument digest. Both dashboard approval routes SHALL check that binding before a decision or dispatch and refuse merge edits that would change the reviewed operation. A successful decision SHALL execute through the original approval lifecycle and single merge authority, retaining its approval events, merge-review history and durable fleet-rebind receipts. Missing, malformed or unshowable calls SHALL remain ineligible.

ID: REQ-module-approvals-008
Source: review-parked-entity-merge-approvals/design.md D1-D3 and D5; heart-and-soul/security.md Approval Gates
Scope: v1-mandatory

#### Scenario: Confirmation resolves the original pending merge

- **WHEN** the authenticated owner confirms a valid reviewed pending merge through either dashboard approval route
- **THEN** the original stored source-to-target operation SHALL execute through its owning approval handler
- **AND** success SHALL persist execution outcome on the original approval and retain its decision and execution events
- **AND** the merge SHALL retain its merge-review record and durable fleet-rebind receipts
- **AND** it SHALL NOT execute a separate standalone merge followed by best-effort approval bookkeeping

#### Scenario: Changed or edited calls are refused before decision

- **WHEN** an approval submission has a missing or mismatched displayed tool/digest, or attempts to edit the reviewed merge command
- **THEN** the dashboard approval route SHALL reject the submission before approval, decision audit or dispatch
- **AND** the stored call SHALL remain unchanged and nothing SHALL merge

#### Scenario: Unsupported merge arguments remain unapprovable

- **WHEN** a pending merge has invalid or identical entity IDs or contains an executable argument the review cannot show completely
- **THEN** its dashboard detail SHALL provide no eligible merge review
- **AND** both dashboard approval routes SHALL refuse the operation without decision or merge effects

#### Scenario: Legacy tool names keep their original binding

- **WHEN** a retained Relationship request stores `entity_merge` with valid source and target arguments
- **THEN** its dashboard review SHALL support the same operation as `memory_entity_merge` while retaining the stored name in its displayed-call binding
- **AND** successful dispatch SHALL resolve the existing registered merge handler without rewriting the stored row

#### Scenario: Merge execution failure remains an explicit recovery state

- **WHEN** a reviewed approval is accepted but merge execution fails, including when an entity became unavailable after comparison
- **THEN** the approval SHALL retain the existing approved-with-null-execution-result recovery state and an execution-failed event
- **AND** the merge SHALL NOT substitute another entity, report success or automatically replay the action

### Requirement: Entity merge notification cards require dashboard review

Entity-merge notifications SHALL offer a dashboard review link rather than direct merge approval, independently of dashboard preview support. A notification callback SHALL NOT approve or execute either supported merge tool. Dashboard detail SHALL expose dedicated merge-review context without supplying a generic directly approvable preview to clients that do not support structural merge review. Existing owner authentication and unrelated tool preview eligibility SHALL remain unchanged.

ID: REQ-module-approvals-009
Source: review-parked-entity-merge-approvals/design.md D1 and D4; heart-and-soul/security.md Approval Gates
Scope: v1-mandatory

#### Scenario: Dashboard support does not unlock notification decisions

- **WHEN** a notification is rendered for either merge tool after dashboard merge review is supported
- **THEN** it SHALL offer a dashboard link labeled Review merge and no inline approval decision
- **AND** a direct callback approval attempt SHALL execute nothing and direct the owner to dashboard review

#### Scenario: Older clients remain safely unable to approve merges

- **WHEN** a dashboard client understands generic call previews but not dedicated merge review
- **THEN** the merge detail SHALL leave its generic call preview unavailable
- **AND** that client SHALL remain unable to approve the merge through its generic preview controls
- **AND** supported messaging tools SHALL retain their existing safe preview and decision behavior

#### Scenario: Unauthenticated review cannot grant merge authority

- **WHEN** a caller without the existing owner authorization attempts to read merge comparison or submit its approval
- **THEN** the request SHALL be refused under the existing owner-authentication policy without comparison disclosure or decision effects
- **AND** client-supplied review fields SHALL NOT assert actor identity or grant approval authority

## Source References

- Non-Negotiable Rule 3 (domain capability boundaries), Rule 4 (deterministic infrastructure) and Rule 7 (connector-owned transport), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, Approval Gates and owner decision authority.
- RFC 0021 (decision loop), existing shared approval execution and audit.
- RFC 0023 (notification delivery), existing presentation and callback bindings remain unchanged.
- `review-parked-entity-merge-approvals/design.md`, D1-D5.
