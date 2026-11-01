## MODIFIED Requirements

### Requirement: Correction Audit Linkage
The session system SHALL support querying which corrections were performed by a given goal invocation and which corrections target a given session or goal invocation. The correcting invocation's identity SHALL come from the verified broker assertion of the `correct` call, never from spawner-derived session context. This linkage is stored entirely in the `corrections` table (references to the correcting invocation and to the target session or invocation) and does NOT require schema changes to the `sessions` table itself.

Source: RFC 0038 D7 and D17
Scope: v1-mandatory

#### Scenario: Query corrections performed by a session
- **WHEN** `corrections_for_session(pool, correcting_session_id=...)` is called with a goal invocation identifier
- **THEN** all correction records whose correcting invocation matches SHALL be returned, ordered by `created_at`

#### Scenario: Query corrections targeting a session
- **WHEN** `corrections_by_session(pool, target_session_id=...)` is called with a legacy session or goal invocation identifier
- **THEN** all correction records where `target_session_id` matches SHALL be returned, ordered by `created_at`

#### Scenario: Session detail includes correction count
- **WHEN** `sessions_get(pool, session_id)` is called and the session has associated corrections
- **THEN** the response SHALL include `correction_count` indicating how many corrections target this session

#### Scenario: Correcting identity comes from the verified assertion
- **WHEN** a goal invocation calls `correct` on a domain
- **THEN** the correction records the `goal_id` and `invocation_id` from the call's verified assertion as the correcting identity
- **AND** a `correct` call with no valid assertion is refused before any correction is written

### Requirement: Correction Trigger Source
Corrections SHALL be performed by ordinary goals. A correction goal's origin SHALL be whatever admission derived for it (for example `owner`, or `third_party` when the sender is unverified), standing in for the trigger source that Spawner sessions record — there is no special `correction` origin or trigger source. The correction context is captured in the `corrections` table, not in goal or session metadata.

Source: RFC 0038 D5 and D17
Scope: v1-mandatory

#### Scenario: Correction session uses standard trigger source
- **WHEN** a user initiates a correction via an external message
- **THEN** the correcting goal's origin is the one admission derived for that message (`owner` when owner-channel authorization verifies the channel and sender)
- **AND** the correction linkage is recorded in the `corrections` table, not in the goal's origin

### Requirement: Effective system prompt receipt

Every newly launched runtime session — a goal invocation, whose receipt is recorded against
its invocation in the goal ledger, or a Spawner-launched engineering executor, whose receipt is
recorded on its session row — SHALL persist the exact effective system-prompt bytes supplied
to the runtime together with a deterministic SHA-256 digest, total UTF-8 byte count, and an ordered
provenance list naming every composition source and its present, shadowed, or unavailable
state. Provenance metadata SHALL contain no source content or absolute filesystem path, receipts
SHALL NOT be copied into telemetry, and legacy session rows MAY remain without a receipt.

Source: RFC 0038 D15 and D17
Scope: v1-mandatory

#### Scenario: Fixed roster tree produces a deterministic receipt
- **WHEN** the same synthetic roster tree and dynamic composition inputs are composed twice
- **THEN** both receipts contain the same effective prompt, digest, byte count, source order, per-source byte counts, and per-source digests

#### Scenario: Optional source is unavailable
- **WHEN** an optional composition source cannot be read and existing availability policy permits the session to continue
- **THEN** the session receipt names that source as unavailable without content
- **AND** the stored effective prompt and digest describe exactly the bytes supplied to the runtime

#### Scenario: Receipt construction fails before invocation
- **WHEN** the effective bytes cannot be digested or persisted atomically with session creation
- **THEN** the runtime adapter is not invoked
- **AND** no unreceipted new session is presented as executed

#### Scenario: Lead receipt names its goal-runtime sources
- **WHEN** the goal runner launches a lead
- **THEN** its receipt's provenance names the lead policy, the owner profile, the goal input, the domain index and, on recovery, the ledger summary as its sources

### Requirement: Content-blind session purpose lane

Every new runtime session SHALL persist a closed `standard` or `private_content` purpose lane derived from
trusted context without inspecting prompt content: for a goal invocation, the goal's server-held
origin context (the ingest envelope's channel); for a Spawner-launched engineering executor, its
trigger context. WhatsApp and Telegram sourced goals SHALL use `private_content`; unknown or unrelated
sources SHALL use `standard`. Every invocation of a goal, including children, replacement leads and
escalated leads, SHALL carry the goal's lane.

Source: RFC 0038 D5, D9, D14 and D16
Scope: v1-mandatory

#### Scenario: Private routed session is labelled without content parsing
- **WHEN** a goal is admitted from an ingest envelope whose trusted channel is WhatsApp or Telegram
- **THEN** its invocations store `purpose_lane=private_content`
- **AND** no sender, recipient, thread, message, or prompt value is stored in the lane

#### Scenario: Children inherit the goal's lane
- **WHEN** the lead of a `private_content` goal delegates a child or is replaced by recovery or escalation
- **THEN** the child or replacement lead stores `purpose_lane=private_content`
- **AND** its model is resolved under the same private-content model policy as the lead

## REMOVED Requirements

### Requirement: Routed Sessions Carry Ingestion Event Identifier
**Reason**: RFC 0038 D1 and D7 delete `route.execute` and route dispatch, so no session SHALL be created with `trigger_source = "route"`; the ingestion event identifier now belongs to the goal admitted from the envelope.

**Migration**: See "Goals Carry Ingestion Event Identifier" (`REQ-core-sessions-001`) in this spec. Route sessions recorded before cutover keep their `ingestion_event_id` values.

## ADDED Requirements

### Requirement: Goals Carry Ingestion Event Identifier
A goal admitted from an ingest envelope that produced a row in `public.ingestion_events` SHALL persist that ingestion event UUID on the goal, and every invocation, effect and outcome of that goal SHALL be attributable to it through the goal identifier, since it is the join key chronicler and other downstream readers use to attribute work back to a sender, channel, contact, or upstream message. A duplicate submission of the same envelope SHALL return the existing goal rather than admit a second goal for the same ingestion event.

ID: REQ-core-sessions-001
Source: RFC 0038 D5 and D17; RFC 0003 (ingest envelopes)
Scope: v1-mandatory

#### Scenario: Admitted goal links back to its ingestion event
- **WHEN** Switchboard ingest inserts a row in `public.ingestion_events` and admission creates a goal from that envelope
- **THEN** the goal carries that ingestion event UUID
- **AND** the goal's invocations, effects and outcome are attributable to it through the goal identifier

#### Scenario: Duplicate envelope returns the existing goal
- **WHEN** the same ingest envelope is submitted to admission twice
- **THEN** admission returns the existing goal and records no second goal for that ingestion event

#### Scenario: Goals not admitted from an envelope need no ingestion event
- **WHEN** a goal is admitted from an obligation, a caused admission or a staffer contract
- **THEN** its ingestion event identifier MAY be NULL
- **AND** chronicler attribution uses the goal's origin and cause instead

#### Scenario: Pre-cutover route sessions keep their identifiers
- **WHEN** a deployment carries route sessions recorded before cutover
- **THEN** their `ingestion_event_id` values are preserved and remain readable

## ADDED Requirements

### Requirement: Epoch receipts reconstruct bounded continuation
Session receipts SHALL bind goal/epoch/predecessor, immutable plan and label/grant/context digests, wake intent and honest outcome using S2/S5. These SHALL reference the sole goal ledger and preserve existing purpose/prompt/delivery audit rather than creating a second session authority.

ID: REQ-core-sessions-002
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S2/S5; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A replacement lead is reconstructed
- **WHEN** lease loss or escalation replaces a lead
- **THEN** the receipt names the predecessor/new epoch and unchanged effects/budget/labels, with no stale token accepted

#### Scenario: A context source fails during recovery
- **WHEN** a prior receipt exists but its source is unavailable
- **THEN** the source remains explicitly unavailable; no fabricated empty context, lost label or false successful delivery is recorded
