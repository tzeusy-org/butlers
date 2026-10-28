## Purpose

Carry an explicitly observed washer or dishwasher cycle into a bounded owner handoff. Preserve the distinction between machine completion, delivery and the owner's confirmation that the resulting household task is done.

## ADDED Requirements

### Requirement: Enrollment requires explicit supported phase evidence
Home SHALL accept owner-authenticated enrollment only for a specific appliance and a validated mapping of explicit `idle`, `running`, `completed` and `error` phases, with trustworthy observation identity, source health and the finite `appliance-v1` response profile in design.md. Enrollment MUST NOT derive cycle completion from power, door state, entity names or an LLM.

ID: REQ-home-appliance-handoff-001
Source: heart-and-soul/vision.md Rules 3, 4 and 6; RFC 0038 D4 and S3; REQ-event-response-contract-002
Scope: v1-mandatory

#### Scenario: Unsupported appliance remains unavailable
- **WHEN** discovery lacks explicit phase or source-freshness evidence for an appliance
- **THEN** enrollment remains unavailable with the missing capability named
- **AND** no cycle or handoff is fabricated from a guessed mapping

#### Scenario: Mapping changes fence old consumers
- **WHEN** the owner changes an enrolled device or phase mapping at its current revision
- **THEN** Home creates a new enrollment generation and fences previous-generation observations and reminder admissions
- **AND** existing handoffs retain their original provenance and can still be marked Done

### Requirement: Machine cycles and human handoffs have separate outcomes
Home SHALL create one handoff only after a complete, fresh, ordered running-to-completed cycle witnessed within the same enrollment generation. Only explicit owner Done SHALL mark that human task complete; missing history, error, source uncertainty or time expiry MUST remain distinct from completion.

ID: REQ-home-appliance-handoff-002
Source: heart-and-soul/vision.md Rule 4; RFC 0038 S3.2 and S6
Scope: v1-mandatory

#### Scenario: Completion opens one pending handoff
- **WHEN** a witnessed cycle completes with valid source continuity
- **THEN** Home records machine completion and opens one pending unload or transfer handoff
- **AND** neither appliance completion, door opening nor message delivery marks the handoff Done

#### Scenario: Missing start or source gap cannot invent completion
- **WHEN** Home receives completion without a witnessed start or after an unreconstructable gap
- **THEN** it records incomplete evidence instead of a completed cycle or new completion handoff
- **AND** recovery requires a new witnessed cycle before normal completion handling resumes

#### Scenario: Error and maximum cycle duration stay honest
- **WHEN** a cycle reports error or exceeds eight hours without supported completion
- **THEN** it ends as failed or unknown respectively
- **AND** the owner sees that outcome rather than a completion handoff

### Requirement: Replay and ordering cannot multiply work
Home SHALL apply cycle reduction, handoff creation and canonical delivery intent idempotently under the enrollment generation and original start identity. Duplicate or late observations MUST NOT create additional handoffs, regress terminal machine phases or reanimate stopped enrollment; conflicting bytes under one source identity MUST make the affected cycle unknown.

ID: REQ-home-appliance-handoff-003
Source: RFC 0038 S3.2, S4 and S6; REQ-event-response-contract-003
Scope: v1-mandatory

#### Scenario: Burst processing preserves the one cycle
- **WHEN** ten thousand redundant samples surround a valid start and completion within one coalescing window
- **THEN** the feature creates one handoff and one canonical completion delivery subject
- **AND** it performs zero model invocations

#### Scenario: Restart and late running preserve terminal state
- **WHEN** completion is replayed after restart and an older running observation follows
- **THEN** the original cycle, handoff and delivery identity remain unchanged
- **AND** expired or compacted historical evidence cannot become a new cycle

### Requirement: Owner controls are revision bound and deterministic
Home SHALL expose authenticated Done, Later and Stop watching actions bound to the handoff's current action generation and expected revision through the canonical owner-action path. Done and Later MUST resolve atomically; Stop watching MUST fence future cycle and reminder admissions without operating the appliance or claiming to undo existing effects. Stop and remapping MUST invalidate prior action tokens and provide fresh Done-only actions for still-open handoffs without restarting their observation or reminder generations.

ID: REQ-home-appliance-handoff-004
Source: RFC 0038 S6 and D12; heart-and-soul/vision.md Rule 3
Scope: v1-mandatory

#### Scenario: Concurrent Done and Later have one winner
- **WHEN** two presentations submit Done and Later against the same handoff revision
- **THEN** exactly one state transition commits and the other receives the current result or a stale-action response
- **AND** every presentation reflects the same canonical state

#### Scenario: Stop remains available during overload
- **WHEN** the owner stops watching while events or reminders are queued
- **THEN** the generation is fenced within two seconds of authenticated command receipt under the supported profile
- **AND** queued work cannot create new deliveries after that fence
- **AND** the owner may still mark an existing handoff Done without restarting observation

#### Scenario: Expired or foreign actions have no effect
- **WHEN** a callback has the wrong owner, stale generation, changed arguments or expired token
- **THEN** Home refuses it without altering the handoff or issuing a delivery

#### Scenario: Done remains available after watching stops
- **WHEN** watching stops or its mapping changes while an existing handoff remains open
- **THEN** a refreshed authenticated Done action can complete that handoff using its new action generation
- **AND** prior-generation callbacks and Later actions cannot resume its observation or reminders

### Requirement: Routine care is bounded and model free
Home SHALL enforce the `appliance-v1` profile and REQ-event-response-contract-001 through 005 for enrollment monitoring, completion, errors, fixed reminders and callbacks. These paths MUST consume zero model invocations and zero model input/output tokens, including alternate wellness or summarization routes; source failures MUST preserve stale or unknown status without quiet-hour bypass.

ID: REQ-home-appliance-handoff-005
Source: heart-and-soul/vision.md Rules 4 and 5; REQ-event-response-contract-001 through 005
Scope: v1-mandatory

#### Scenario: Source expiry and response misses are visible
- **WHEN** observation age exceeds 120 seconds, source health expires, or the ten-second eligible-delivery deadline is missed
- **THEN** current source reads become unknown immediately when stale and the corresponding stale or missed state propagates within five seconds
- **AND** late success does not erase the miss or renew source timestamps

#### Scenario: Reminders and handoffs expire without false completion
- **WHEN** a handoff remains pending after its bounded reminder allowance or its 24-hour deadline
- **THEN** no further automatic reminder is admitted after allowance exhaustion and the handoff expires at its deadline without becoming Done
- **AND** quiet-hour or unavailable-provider holds remain visible and cannot extend that deadline

### Requirement: Owner surfaces expose the real state and recovery
Home SHALL expose enrollment in Devices and show machine phase, human task state, source freshness and next reminder independently. Controls MUST acknowledge interaction immediately, remain keyboard accessible, and report success only after a durable receipt; current status MUST remain readable while mutations or delivery are pending.

ID: REQ-home-appliance-handoff-006
Source: openspec/specs/dashboard-design-language/spec.md Interaction Affordances and Interface Copy; RFC 0038 S6
Scope: v1-mandatory

#### Scenario: A pending command does not pretend success
- **WHEN** the owner selects Done or Stop watching
- **THEN** the control acknowledges the interaction within 100 milliseconds without asserting committed success
- **AND** repeated clicks reuse the same action identity and a failed request offers retry against current state

#### Scenario: Unsupported and stale states guide recovery
- **WHEN** no supported appliance is enrolled or its source becomes stale
- **THEN** the surface explains the required explicit-cycle mapping or source recovery action respectively
- **AND** status uses text as well as color and controls work with visible keyboard focus

### Requirement: Retention and rollback preserve fences
Home SHALL retain unresolved handoffs until their finite expiry, retain terminal domain receipts for 30 days and preserve minimal replay fences for each enrollment until it is removed. Removal MUST prevent old generations from becoming valid again; rollback MUST disable new admissions without deleting receipts or re-enabling stale callbacks.

ID: REQ-home-appliance-handoff-007
Source: heart-and-soul/vision.md Rules 1 and 3; RFC 0038 S3.2 and S6
Scope: v1-mandatory

#### Scenario: Compacted history cannot reappear as new work
- **WHEN** old cycle detail is removed under retention and its events or callbacks are replayed
- **THEN** the generation fence and source watermark reject them without a new handoff
- **AND** canonical delivery and authorization receipts retain their own governing retention rules

## Source References

- `about/heart-and-soul/vision.md`, Rules 1, 3, 4, 5 and 6; `roster/home/MANIFESTO.md`, Device Orchestration and precision.
- RFC 0038, `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md`, S3/S4/S6, deterministic domain care and owner actions.
- `openspec/changes/specify-realtime-feature-contracts/specs/event-response-contract/spec.md`, adopted shared response requirements.
- `openspec/specs/dashboard-design-language/spec.md`, Dispatch interaction and copy contracts.
