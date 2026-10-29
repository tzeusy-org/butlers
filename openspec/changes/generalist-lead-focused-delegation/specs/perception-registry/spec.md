## ADDED Requirements

### Requirement: One typed registry preserves state and freshness
The representation owner SHALL implement every S3 registry/projection field, four-state freshness rule, nullable-subject uniqueness, primitive bound and domain-source ownership. The public projections SHALL NOT become competing life-record/clock/presence truth or expose raw coordinates/arbitrary prose.
The initial kind/value/subject/writer/visibility and twelve-signal aliases SHALL be exactly S3.1, including the eight git mode IDs and in-flight posture distinction. An alias SHALL NOT create an additional store, direct writer, mode or permission; source-specific legacy receipts/expiry and unknown conversion remain intact.

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

#### Scenario: All legacy names resolve without a second truth
- **WHEN** each of the twelve existing context signals and owner.presence/owner_clock/system.dep/expect aliases is migrated or queried
- **THEN** it resolves through S3.1's canonical kind/subject and named writer with original source evidence/expiry; unsafe mappings stay unknown and block activation, not silently absent

#### Scenario: The mode wire names remain a closed set
- **WHEN** a caller supplies asleep/travel/house_unattended/sick/focus/hosting/holiday/emergency, or an unknown mode/in-flight label
- **THEN** only the eight IDs are admitted to owner_modes; in-flight is posture and activity aliases cannot activate a mode or bypass step-up/owner policy

### Requirement: Trusted event CAS is the sole projection writer
The representation SHALL expose S3's exact trusted writer/event/CAS API with role-derived kind/source authority, immutable replay identity, generation conflict and atomic transition receipt. Dots owner.place and other named consumers SHALL use it without a parallel observation/reducer API.
The writer SHALL implement S3.2's immutable at/confirmed_at, derived late flag and stable target-bound retraction wire. Dots remains the sole place reducer; re-entry assertion, derived history retraction and single slot CAS/receipts SHALL be atomic and replay-stable, without deleting the leave or adding a competing lock/store.

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

#### Scenario: Confirmed re-entry retracts one earned leave without deleting it
- **WHEN** Home Dots confirms re-entry within ten minutes of its original owner.place leave
- **THEN** one CAS appends enter plus the target-bound retraction/receipts using stable keys; the original leave stays immutable and a retry returns both original receipts

#### Scenario: A retraction is foreign unearned or out of window
- **WHEN** a request targets another owner/source/kind, a non-leave, absent event or leave outside the ten-minute window
- **THEN** identity/FK/authority checks refuse before any slot/event mutation; no model or alternate reducer gains a retraction API

#### Scenario: Late confirmation is derived from immutable times
- **WHEN** confirmed_at-at is exactly fifteen minutes, then greater than fifteen, or confirmed_at precedes at
- **THEN** exactly fifteen is not late, greater is late and negative time refuses; retry cannot refresh either fact or turn legacy-unknown confirmation into a live event

#### Scenario: Retraction storage fails or writers race
- **WHEN** the retraction insert fails after candidate enter storage, or two writers use one expected generation
- **THEN** the entire slot/event/receipt transaction rolls back or one CAS wins; no orphan retraction, duplicated target or deleted history remains

### Requirement: Dark representation has named activation and privacy owners
Perception readers SHALL return S3/S5 source/age/state/labels and availability-only restrictions. Representation SHALL remain dark until the separately named producer/reader activation, with .9 registry, Dots .17 owner.place, .21 clock/modes and .14 health ownership preserved.
Readers SHALL implement S3.2's consistent target relation even across pagination/windows, exposing operation/target/confirmation/late/retracted state to owner history. Default real-time transition/evaluator reads SHALL skip late/retracted assertions and never fire on the retraction record. Activation/rollback remain .21-owned and cannot drop history or reverse already committed effects.

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

#### Scenario: The retraction is outside the current query page
- **WHEN** owner history or a transition reader loads a leave whose target retraction lies outside its selected page/window
- **THEN** the consistent target relation still marks it retracted; history retains the link/late metadata and live evaluation skips it without replaying or undoing effects

#### Scenario: A legacy reader lacks retraction or late semantics
- **WHEN** consumer parity finds that an old reader treats a late/retracted edge or retraction record as a new transition
- **THEN** .21 activation remains blocked for that reader; rollback holds it and preserves canonical events/receipts rather than restoring a competing presence store
