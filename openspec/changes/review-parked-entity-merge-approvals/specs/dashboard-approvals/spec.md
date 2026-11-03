## ADDED Requirements

### Requirement: Parked entity merges show structural review before approval

The dashboard SHALL provide a structural review for a pending entity-merge approval before enabling its final merge confirmation. It SHALL show the stored source and target IDs, their current names and types, shared identity evidence, divergences and the existing per-entity comparison context. It SHALL identify the absorbed source and surviving target, explain source tombstoning and target-type preservation, and keep that direction fixed. Different entity types SHALL NOT by themselves block review or approval.

ID: REQ-dashboard-approvals-902
Source: review-parked-entity-merge-approvals/design.md D1-D3; heart-and-soul/security.md Approval Gates
Scope: v1-mandatory

#### Scenario: Identical names remain distinguishable in review

- **WHEN** the owner opens a pending merge between a person and an organization with the same canonical name
- **THEN** the review SHALL distinguish both records by ID and type and identify which is absorbed and which survives
- **AND** it SHALL show shared evidence and divergences, including explicit empty states when either set is empty
- **AND** it SHALL state that the surviving entity keeps the target's type
- **AND** it SHALL offer no control that silently reverses this parked operation

#### Scenario: Every dashboard entry uses the reviewed operation

- **WHEN** the owner initiates approval from the dossier, a summary surface or a keyboard shortcut
- **THEN** that flow SHALL display the comparison for the original stored operation before final confirmation becomes available
- **AND** rail selection, a raw-argument preview or a tool-name summary alone SHALL NOT execute a merge

#### Scenario: Comparison failure offers recovery without approval

- **WHEN** the required comparison is loading or fails because a source is unavailable, an entity is missing or the pair cannot be compared
- **THEN** the dashboard SHALL keep merge confirmation unavailable and show a specific loading or error state
- **AND** a failed read SHALL offer retry without approving or merging anything
- **AND** it SHALL NOT substitute another entity or present a partial read as a complete review

#### Scenario: Selection and stored-call changes invalidate the rendered review

- **WHEN** the selected action or its stored source, target or argument digest changes while a comparison or delayed approval is outstanding
- **THEN** the dashboard SHALL discard that review's eligibility for the newly selected operation
- **AND** a late response for the previous operation SHALL NOT enable its successor's approval
- **AND** delayed confirmation SHALL retain the exact reviewed call rather than replace it with a refreshed call

#### Scenario: Final merge confirmation is accessible and cancelable

- **WHEN** the comparison is displayed and the owner invokes merge confirmation
- **THEN** the dashboard SHALL show a keyboard and screen-reader accessible confirmation identifying both entities by name, type and ID and naming the survivor
- **AND** Cancel, Escape or closing the confirmation SHALL issue no approval or merge request
- **AND** confirming SHALL submit the reviewed operation once while repeated activation is disabled during submission

### Requirement: Parked merge review preserves the pending decision lifecycle

The dashboard SHALL preserve denial, deferral and truthful execution recovery for parked merges. Valid retained pending requests SHALL become reviewable without recreation or stored-command rewrites. Ineligible or historical states SHALL NOT be presented as newly pending approvals, and approval outcome and fleet-rebind completion SHALL remain distinguishable.

ID: REQ-dashboard-approvals-903
Source: review-parked-entity-merge-approvals/design.md D5 and Migration Plan; heart-and-soul/vision.md What Success Looks Like
Scope: v1-mandatory

#### Scenario: Deny and Defer work while merge comparison is unavailable

- **WHEN** a pending merge cannot currently load its comparison and the owner chooses Deny or Defer
- **THEN** the existing approval denial or deferral SHALL resolve that command without executing a merge
- **AND** a denial SHALL retain the optional reason on the original approval
- **AND** deferral SHALL retain the original call and existing notification re-presentation semantics

#### Scenario: Existing pending rows need no resubmission

- **WHEN** the owner opens a retained valid pending merge request created before this feature
- **THEN** the dashboard SHALL offer the new review on that original request
- **AND** it SHALL NOT require a new approval ID, originating session or rewritten stored tool name

#### Scenario: Decision state changes remove pending approval controls

- **WHEN** the reviewed request becomes rejected, expired, abandoned, approved or executed before confirmation is accepted
- **THEN** the dashboard SHALL show its current decision or execution state instead of offering it as pending
- **AND** an approved action with no execution result SHALL retain only the existing explicit recovery workflow
- **AND** refreshing, deployment or opening review SHALL NOT automatically replay it

#### Scenario: Execution and fleet rebinding have separate outcomes

- **WHEN** the merge executes successfully but some fleet-rebind receipts remain pending or failed
- **THEN** the dashboard SHALL resolve the original approval and refresh the affected approval and entity views
- **AND** it SHALL distinguish completed merge execution from outstanding reference rebinding rather than claim full fleet completion

## Source References

- Non-Negotiable Rule 1 (owner sovereignty) and Rule 4 (deterministic infrastructure), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, Approval Gates: execute the exact stored call the owner saw.
- RFC 0021 (decision loop), existing approval decisions and execution lifecycle.
- `openspec/specs/dashboard-relationship/spec.md`, Merge review requires final accessible confirmation.
- `review-parked-entity-merge-approvals/design.md`, D1-D5.
