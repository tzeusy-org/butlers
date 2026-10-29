## ADDED Requirements

### Requirement: One subject owns all owner presentations
Delivery SHALL implement the complete S6 subject/presentation schema, exact owner route, unique retry identity and state/receipt semantics. It SHALL reference existing goal/effect/decision authority and SHALL NOT create another approval authority or permanent double-writing path.

ID: REQ-owner-delivery-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A subject is presented repeatedly
- **WHEN** a worker retries one subject/surface/generation after a crash
- **THEN** the same presentation identity and receipt are reused, with provider ambiguity preserved

#### Scenario: A model supplies a recipient surface
- **WHEN** a notify/action request contains a model-provided owner/channel claim
- **THEN** recipient identity comes from the trusted live owner/route or delivery refuses

#### Scenario: Old and new stores overlap during preparation
- **WHEN** delivery migration is shadow-reading old RFC0023/attention/approval rows
- **THEN** the explicit source-to-subject map proves parity before one sole-writer switch and old IDs remain traceable

### Requirement: Subject action tokens cannot authorize stale work
Every owner action SHALL validate S6's owner/surface/subject/action/digest/generation/nonce/expiry bindings and serialize canonical decision/ack with the existing effect fence. Ack SHALL NOT approve an effect, and stale UI SHALL NOT retain authority.

ID: REQ-owner-delivery-002
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Concurrent decisions contend
- **WHEN** two valid surfaces act on one subject generation
- **THEN** one action/receipt wins atomically and all other tokens are stale

#### Scenario: A binding or verifier is stale
- **WHEN** arguments/recipient digest, owner/surface, generation, expiry or token verifier mismatches
- **THEN** the callback refuses with no effect or policy change

#### Scenario: Retraction fails after a decision
- **WHEN** the provider cannot retract a losing presentation
- **THEN** its stale callback remains harmless and the failure is honestly receipted

#### Scenario: A duplicate callback is replayed
- **WHEN** the same winning token is delivered again
- **THEN** the original result is returned without another approval or dispatch

### Requirement: Pending asks recover only through a fresh owner action
Pending-ask recovery SHALL use S6's unique failure identity, typed cause, coalesced Run/Drop subject and24h stale boundary under current admission rules. Rollback SHALL preserve subject/effect/token generations and all ambiguous outcomes.

ID: REQ-owner-delivery-003
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: Dependency recovery follows an undelivered owner failure
- **WHEN** one failed ask becomes recoverable
- **THEN** one coalesced Run/Drop subject is offered, with no automatic model call

#### Scenario: Run is chosen twice or after expiry
- **WHEN** duplicate Run or a more-than24h-old ask is submitted
- **THEN** valid Run admits at most one fresh goal, while stale/Drop never replays it

#### Scenario: Delivery rollback occurs after an action
- **WHEN** new delivery/actions are disabled
- **THEN** receipts and old-ID mapping persist and stale tokens cannot become executable again

### Requirement: Push references reveal no private subject content
The later push interface SHALL permit only a fixed non-sensitive cause and opaque subject reference, requiring normal owner authentication for details/actions. Provisioning, urgency, step-up and presence/desk-first policy SHALL remain separately owned and unactivated.

ID: REQ-owner-delivery-004
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6/S7; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A locked-screen payload is prepared
- **WHEN** a subject contains sensitive location/health/message data or an action token
- **THEN** none enters push title/body/URL; only the permitted fixed cause/reference may leave

#### Scenario: A push reference is opened
- **WHEN** a caller has only the opaque reference
- **THEN** normal owner authentication and current subject fence are required before details/actions

#### Scenario: Push provisioning is absent
- **WHEN** the software interface exists without platform/credential authorization
- **THEN** no provisioning or delivery is inferred and existing reply-target behavior remains
