## ADDED Requirements

### Requirement: Entity merges preserve the selected target type

An authorized entity merge SHALL retain the surviving target's entity type even when the source has a different type. A source/target type difference SHALL NOT by itself reject the merge. The source SHALL be tombstoned into that target under the existing role, fact-conflict and fleet-rebind contracts, without changing the target's type to the source's type.

ID: REQ-entity-identity-009
Source: owner approval 2026-11-03; review-parked-entity-merge-approvals/design.md D2; heart-and-soul/vision.md Rule 1
Scope: v1-mandatory

#### Scenario: Person source merges into an organization target

- **WHEN** the owner authorizes a valid merge from a person source into an organization target
- **THEN** the surviving target SHALL remain an organization and the source SHALL be tombstoned into it
- **AND** the type difference alone SHALL NOT cause rejection

#### Scenario: Organization source merges into a person target

- **WHEN** the owner authorizes a valid merge from an organization source into a person target
- **THEN** the surviving target SHALL remain a person and the source SHALL be tombstoned into it
- **AND** the type difference alone SHALL NOT cause rejection

## Source References

- Non-Negotiable Rule 1 (owner sovereignty), `about/heart-and-soul/vision.md`.
- Owner approval on 2026-11-03 explicitly permits cross-type merges retaining the target type.
- `openspec/specs/entity-identity/spec.md`, Entity merge preserves roles and Entity merge rebinds fleet references with durable receipts.
- `review-parked-entity-merge-approvals/design.md`, D2.
