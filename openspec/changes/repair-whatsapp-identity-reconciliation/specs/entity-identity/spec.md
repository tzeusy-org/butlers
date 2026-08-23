## ADDED Requirements

### Requirement: Transport identifiers are not fact-storage entity names

Fact-storage entity creation MUST reject WhatsApp JID or LID values used as canonical person names
when structured sender identity is available, and downstream fact extraction MUST use the excerpt's
entity anchor instead.

ID: REQ-entity-identity-001
Source: heart-and-soul/architecture.md deterministic identity boundary
Scope: v1-mandatory

#### Scenario: Fact extraction uses the structured speaker entity

- **WHEN** a conceptual excerpt supplies `sender_entity_id` for a fact about that speaker
- **THEN** the fact MUST be anchored to that entity UUID
- **AND** no new entity may be created from `sender`, `sender_identity`, a JID, or a LID

#### Scenario: JID-shaped fact-storage creation is rejected

- **WHEN** a runtime calls memory entity creation with a WhatsApp JID or LID as a canonical person name
  and fact-storage provenance
- **THEN** the call MUST return an actionable structured error without inserting an entity
- **AND** the error MUST direct the caller to a structured speaker entity anchor without echoing the
  identifier

#### Scenario: Ordinary named entity creation remains unchanged

- **WHEN** fact extraction creates a properly named person, organization, place, or other entity after
  resolution returns no exact candidate
- **THEN** the existing transitory entity convention MUST remain available
- **AND** email-like or at-sign-containing non-WhatsApp names MUST NOT be rejected by the guard

#### Scenario: Missing speaker anchor fails closed

- **WHEN** a fact concerns a transport-identified speaker but no structured entity anchor is available
- **THEN** fact storage MUST reject the write rather than create or borrow an entity
- **AND** routing of the original message MAY remain fail-open

### Requirement: Guarded WhatsApp transitory reconciliation

The system MUST provide an explicit content-blind operator workflow that identifies false WhatsApp
transitory shells, plans only unambiguous reference-free pairs, and refuses every mutation unless the
operator supplies the exact current plan digest.

ID: REQ-entity-identity-002
Source: docs/superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md §Reconciliation Command
Scope: v1-mandatory

#### Scenario: Reconciliation defaults to dry-run

- **WHEN** the operator invokes WhatsApp reconciliation without apply authorization
- **THEN** the command MUST perform no writes
- **AND** it MUST report only content-blind category counts and an opaque plan digest

#### Scenario: Apply requires the exact reviewed plan

- **WHEN** the operator requests apply mode
- **THEN** the command MUST require an explicit apply flag and the exact digest of the recomputed plan
- **AND** missing, stale, or mismatched authorization MUST produce zero mutations

#### Scenario: Unsafe source is never planned

- **WHEN** a transitory source is unmatched, ambiguous, owner/system-linked, previously rejected, or
  referenced by any protected entity relation
- **THEN** the source MUST be classified content-blindly and excluded from the apply plan
- **AND** the command MUST NOT partially move or delete its state

#### Scenario: Apply revalidates under lock

- **WHEN** an authorized planned pair is about to be reconciled
- **THEN** source and target state and the reference-free invariant MUST be revalidated under
  deterministic row locks
- **AND** any drift MUST abort before that pair is mutated

#### Scenario: Successful reconciliation remains auditable

- **WHEN** an authorized reference-free source is reconciled into its unique confirmed target
- **THEN** the source MUST be tombstoned to that target through the audited entity lifecycle
- **AND** the target MUST remain live, no source references may remain, and one merged review outcome
  MUST record the pair

#### Scenario: Reconciliation never runs automatically

- **WHEN** migrations, daemons, connectors, deployments, or schedulers start
- **THEN** they MUST NOT invoke apply-mode WhatsApp reconciliation
- **AND** existing unmatched transitory entities MUST remain available for owner review
