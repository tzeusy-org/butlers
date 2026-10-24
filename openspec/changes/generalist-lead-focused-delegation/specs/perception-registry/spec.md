## ADDED Requirements

### Requirement: One typed registry preserves state and freshness
The representation owner SHALL implement every S3 registry/projection field, four-state freshness rule, nullable-subject uniqueness, primitive bound and domain-source ownership. The public projections SHALL NOT become competing life-record/clock/presence truth or expose raw coordinates/arbitrary prose.

ID: REQ-perception-registry-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S3; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: An absent subject is written twice
- **WHEN** two writers concurrently target the same kind with null subject
- **THEN** a real NULLS NOT DISTINCT or equivalent indexed rule enforces one slot identity, never a nullable primary-key fiction

#### Scenario: Known evidence expires
- **WHEN** fresh_until passes after a valid observation
- **THEN** the read is unknown with a separately stale last value/age, not currently known

#### Scenario: Configured absence differs from failure
- **WHEN** a configured healthy source has no matching event, while another is unconfigured or all inputs unmeasurable
- **THEN** each state/reason remains distinct and transport failure is not fabricated all-clear

#### Scenario: An untyped payload enters the registry
- **WHEN** a writer submits arbitrary title/URL/prose or raw coordinates as a value
- **THEN** validation refuses it before a slot/event is changed

### Requirement: Trusted event CAS is the sole projection writer
The representation SHALL expose S3's exact trusted writer/event/CAS API with role-derived kind/source authority, immutable replay identity, generation conflict and atomic transition receipt. Dots owner.place and other named consumers SHALL use it without a parallel observation/reducer API.

ID: REQ-perception-registry-002
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S3; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Duplicate and changed event keys are retried
- **WHEN** a source retries an identical event key/digest, then reuses the key with changed data
- **THEN** the identical retry returns its existing receipt and the changed payload refuses without a second event

#### Scenario: Concurrent or stale evidence loses CAS
- **WHEN** two reducers use one expected generation or older evidence arrives
- **THEN** only one current transition commits; stale evidence cannot roll back present state and can only be separately historical

#### Scenario: A model or foreign runtime forges its writer
- **WHEN** a grantable tool or request field claims the registry owner role
- **THEN** the database/service derives real identity and refuses unauthorized kind/source mutation

#### Scenario: Atomic transition storage fails
- **WHEN** event/receipt insertion fails after a candidate slot update
- **THEN** the whole transaction rolls back with no slot generation advance or missing transition

### Requirement: Dark representation has named activation and privacy owners
Perception readers SHALL return S3/S5 source/age/state/labels and availability-only restrictions. Representation SHALL remain dark until the separately named producer/reader activation, with .9 registry, Dots .17 owner.place, .21 clock/modes and .14 health ownership preserved.

ID: REQ-perception-registry-003
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S3/S5; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: The registry lands before its producers
- **WHEN** typed tables/functions are present but active consumers are unchanged
- **THEN** old context/scheduler/notify behavior remains and empty projection data is not a migration-complete claim

#### Scenario: An exposed reader requests situation details
- **WHEN** an exposed or third-party goal requests place, health or routine context
- **THEN** it receives only busy_until or explicit unknown and never detail/raw coordinates

#### Scenario: Rollback disables a new reader
- **WHEN** a successor reader fails parity or must be withdrawn
- **THEN** old compatible reader authority remains without enabling a competing store or losing new receipts
