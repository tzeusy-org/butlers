## Purpose

Provide finite Home comfort sessions whose outcomes are supported by fresh room observations. Keep requested settings, command receipts and achieved comfort separate, and preserve owner control throughout the session.

## ADDED Requirements

### Requirement: Sessions require a finite explicit room contract
Home SHALL accept an owner-authenticated session only with one explicitly mapped room-temperature sensor, a valid inclusive target band, finite expiry and the complete `comfort-v1` profile in design.md. Observe-only SHALL be the default; optional control MUST name one exact climate entity and setpoint and pass existing broker and physical-risk authorization.

ID: REQ-home-comfort-session-001
Source: heart-and-soul/vision.md Rules 3, 4 and 6; roster/home/MANIFESTO.md Comfort Management; RFC 0028; REQ-event-response-contract-002
Scope: v1-mandatory

#### Scenario: Missing or unsupported mapping refuses activation
- **WHEN** the chosen sensor lacks trustworthy freshness, known units or an explicit room mapping
- **THEN** activation refuses with the missing prerequisite named
- **AND** Home does not substitute a thermostat setpoint or infer a room from its name

#### Scenario: Observe-only grants no device action
- **WHEN** the owner starts an observe-only session
- **THEN** Home monitors the target band until its finite expiry without any HA service invocation
- **AND** later enabling control requires a new explicitly authorized session contract

### Requirement: Optional control has one exact bounded effect
A control session SHALL perform at most one exact `climate.set_temperature` action under the existing Home risk map, broker grants and approval checks. Session creation MUST NOT authorize new targets, repeated correction, mode/range changes or automatic restoration; unknown dispatch outcome MUST remain unverified and MUST NOT trigger an automatic resend.

ID: REQ-home-comfort-session-002
Source: RFC 0028 physical actuation contract; RFC 0038 D4, D5 and D12; heart-and-soul/vision.md Rule 3
Scope: v1-mandatory

#### Scenario: Device acknowledgement is not achieved comfort
- **WHEN** a live readback verifies that the climate entity accepted the requested setpoint
- **THEN** the session records command acceptance separately from room-band evidence
- **AND** comfort remains unachieved until the independent room sensor satisfies the observation contract

#### Scenario: Uncertain dispatch is not retried
- **WHEN** a timeout or crash leaves dispatch outcome uncertain
- **THEN** the session reports unverified action and permits no further automatic device action
- **AND** it keeps any actual effect receipt and requires owner reconciliation before a new control session can claim the actuator

#### Scenario: Late approval cannot resurrect a session
- **WHEN** approval arrives after Stop, expiry, manual override or a session revision change
- **THEN** the action refuses against the current session fence without sending a device command

#### Scenario: Approval does not override stale physical preconditions
- **WHEN** an approved action reaches execution with stale room or HA source evidence, changed units or a mismatched expected actuator state
- **THEN** dispatch refuses without a physical send and records the failed precondition
- **AND** receiving approval does not refresh evidence or authorize a later automatic retry

#### Scenario: Denied or failed control still permits honest observation
- **WHEN** approval is rejected or expires, a physical precondition fails, or the single command definitively fails
- **THEN** a session with no terminal fence continues only as observation until Stop or expiry with the specific control outcome visible
- **AND** Stop, expiry or confirmed manual override retains its terminal outcome instead of being downgraded to an active observer
- **AND** no further device attempt occurs and measured attainment never implies the requested setting was applied

### Requirement: Room evidence alone establishes band attainment
Home SHALL report observed-in-band only after fresh room samples remain within the inclusive target band for five continuous minutes with at least six samples and no sample interval greater than 60 seconds. Any outside-band sample or invalid coverage MUST end the current in-band claim immediately; historical attainment MUST remain separately visible without implying current comfort.

ID: REQ-home-comfort-session-003
Source: roster/home/MANIFESTO.md precision and reliable outcomes; RFC 0038 S3.2; REQ-event-response-contract-003 and 005
Scope: v1-mandatory

#### Scenario: Small changes crossing the band remain visible
- **WHEN** individually small sensor changes collectively cross either band boundary
- **THEN** the session reflects the crossing using the authoritative observations despite generic significance filtering
- **AND** sample processing invokes no model

#### Scenario: An observation gap resets attainment
- **WHEN** an out-of-band sample or a gap greater than 60 seconds interrupts the attainment window
- **THEN** the five-minute window restarts from fresh qualifying evidence
- **AND** source age over 90 seconds or invalid health makes the current outcome unmeasurable immediately

#### Scenario: A missed achievement deadline is retained
- **WHEN** the room has not satisfied the observation contract by the session's achievement deadline
- **THEN** Home reports not-achieved with the last valid observation and records a deadline miss
- **AND** later attainment before expiry is labelled late and does not erase the original miss

### Requirement: Stop override and expiry fence further effects
Home SHALL enforce one active session per room and one control claim per actuator, with atomic admission and revision fences. Stop, manual/external actuator override and expiry MUST prevent further physical effects and monitoring/reminder work under that session; only one canonical terminal report under existing owner-delivery authority MAY remain. Stop uses its solicited action receipt instead of a new proactive terminal delivery. None of these outcomes MUST claim to reverse already transferred heat or change a previous setpoint without a separate authorized action.

ID: REQ-home-comfort-session-004
Source: RFC 0038 D5, D12 and S6; RFC 0028; REQ-event-response-contract-003 and 004
Scope: v1-mandatory

#### Scenario: Concurrent starts cannot own the same actuator
- **WHEN** two sessions attempt to claim the same actuator or room concurrently
- **THEN** at most one session commits and the other receives the existing session reference
- **AND** replacing a session requires an explicit Stop and a newly validated start

#### Scenario: Manual changes relinquish control
- **WHEN** a session still holding a control claim observes an actuator setpoint or mode change outside its exact correlated action, or its unit/mapping changes
- **THEN** the session becomes relinquished and its pending actions are fenced
- **AND** Home does not fight the change or restore an earlier setting

#### Scenario: Stop wins before uncommitted effects
- **WHEN** Stop races with approval or dispatch
- **THEN** the common execution fence permits no new effect after Stop commits
- **AND** any effect already committed retains its actual receipt and is not reported undone

#### Scenario: Restart cannot replay a setpoint
- **WHEN** Home restarts with a previously active session
- **THEN** it resumes only still-valid observation after fresh source reconciliation
- **AND** it never resends the session's consumed or uncertain device effect
- **AND** expired sessions stay expired

### Requirement: Monitoring and outcome delivery have finite cost and timing
Home SHALL enforce the full `comfort-v1` profile and adopted REQ-event-response-contract-001 through 005. Routine samples, outcomes, dwell checks, callbacks and source failures MUST use zero inference, including indirect promotion paths; overload, source silence and exhausted action allowance MUST preserve Stop and honest unknown outcomes.

ID: REQ-home-comfort-session-005
Source: heart-and-soul/vision.md Rules 4 and 5; REQ-event-response-contract-001 through 005
Scope: v1-mandatory

#### Scenario: A long session has no per-sample model cost
- **WHEN** a session receives frequent valid, duplicate or replayed samples throughout its lifetime
- **THEN** it uses zero model invocations and zero model input/output tokens for monitoring and deterministic outcome handling
- **AND** at most its single admitted exact device action may be sent

#### Scenario: Overload cannot conceal stale state or delay Stop behind samples
- **WHEN** the bounded observation queue overflows or the source becomes stale while Stop is received
- **THEN** dependent room conclusions become unknown or unmeasurable and Stop uses the independent control path
- **AND** Stop commits within two seconds under healthy control-plane conditions, with missed or unavailable status surfaced by a live caller within five seconds otherwise

### Requirement: Owner controls and receipts expose distinct outcomes
Home SHALL offer a discoverable room session surface with explicit observe-only/control mode, target band, source age, expiry, command receipt and measured outcome. Start and Stop MUST have immediate pending feedback and authenticated repeat-safe actions; terminal summaries MUST distinguish attained, late-attained, not-achieved, unmeasurable, relinquished, stopped and expired without a fabricated success.

ID: REQ-home-comfort-session-006
Source: openspec/specs/dashboard-design-language/spec.md Interaction Affordances and Interface Copy; RFC 0038 S6
Scope: v1-mandatory

#### Scenario: Pending operations preserve useful current information
- **WHEN** a start or stop request takes longer than 100 milliseconds
- **THEN** the surface shows pending feedback while retaining current room evidence and terminal controls
- **AND** it reports committed state only from the durable receipt and offers an actionable error on failure

#### Scenario: Presentation is accessible and does not overclaim
- **WHEN** a control command succeeds but the room remains outside the band
- **THEN** the surface says the setting was accepted and displays the separate unmet room target
- **AND** status is conveyed in text, controls have visible keyboard focus and no required action is hover-only

### Requirement: Session evidence expires without losing control fences
Home SHALL retain terminal session metadata and bounded attainment summaries for 30 days and MUST NOT retain a second raw sensor stream for this feature. Canonical actuation, approval and delivery receipts SHALL retain their existing policies; compaction or rollback MUST NOT restore session authority or make an expired callback executable.

ID: REQ-home-comfort-session-007
Source: heart-and-soul/vision.md Rules 1 and 3; RFC 0038 S3.2 and S6; RFC 0028
Scope: v1-mandatory

#### Scenario: Old sessions cannot reclaim authority after compaction
- **WHEN** terminal session detail is compacted or the feature is rolled back
- **THEN** expired session identities and action tokens cannot create new observations, controls or reminders
- **AND** retained canonical effect receipts remain the authority for what actually happened

## Source References

- `about/heart-and-soul/vision.md`, Rules 1, 3, 4, 5 and 6; `roster/home/MANIFESTO.md`, Comfort Management and precise observed outcomes.
- RFC 0028, `about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md`, exact physical authority and readback.
- RFC 0038, `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md`, D4/D5/D12 and S3/S6.
- `openspec/changes/specify-realtime-feature-contracts/specs/event-response-contract/spec.md`, adopted shared contract.
- `openspec/specs/dashboard-design-language/spec.md`, Dispatch interaction and copy contracts.
