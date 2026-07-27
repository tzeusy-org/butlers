## MODIFIED Requirements

### Requirement: Dashboard Chat-Widget Classification Lanes

The Switchboard SHALL classify `dashboard` source-channel messages (the
owner's floating chat widget) into one of two lanes instead of always calling
`route_to_butler`: Lane A (data statement/correction) or Lane B (bug/system
report). Bug/system reports SHALL NEVER be routed to a domain butler. A
dashboard terminal lane SHALL use the singular durable terminal-action journal
and SHALL never claim that a visible effect was filed or cancelled before its
required child-effect receipts prove that result.

ID: REQ-butler-switchboard-001
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-001; design.md Decisions 1-5
Scope: v1-mandatory

#### Scenario: Lane A data statement routes with deterministic confirm-loop context

- **WHEN** a dashboard message is classified as a data statement or correction
- **THEN** the classification session SHALL call `route_to_butler` exactly as
  for any other channel
- **AND** the routed envelope's `input.context` SHALL deterministically carry
  the conversation's `conversation_id`, its `page_context` (if any), and
  instructions to interpret the statement, apply it, and confirm via
  `conversation_reply` appended in code regardless of classifier prose

#### Scenario: Lane A first successful route stamps sticky routed_butler

- **WHEN** `route_to_butler` for a dashboard-originated message receives an
  `accepted` or `ok` acknowledgement from the target butler
- **THEN** the Switchboard SHALL stamp `routed_butler` on the conversation
  best-effort without failing the route call if stamping fails

#### Scenario: First lane is reserved before irreversible dispatch

- **WHEN** a dashboard classification session first calls `route_to_butler`,
  `file_bug_report`, or reaches the synchronous dead-letter net
- **THEN** the Switchboard SHALL atomically reserve `route_pending`,
  `bug_report`, or `dead_letter` for that immutable user message before it
  dispatches a domain butler, relays QA, or captures a dead letter
- **AND** a definitive `accepted` or `ok` route acknowledgement SHALL promote
  `route_pending` to immutable `route`; it SHALL NOT be replaced by a later,
  conflicting classification tool call
- **AND** the durable terminal-action journal applies to the `bug_report` and
  `dead_letter` reservations; `route` remains governed by the dashboard-turn
  route control record

#### Scenario: Definitive un-dispatched route may become a dead letter

- **WHEN** a `route_pending` reservation receives definitive evidence that every
  route attempt was rejected before target dispatch and had no side effect
- **THEN** the Switchboard SHALL fence a `route_pending` to `dead_letter`
  transition under the same dashboard-turn generation before it captures the
  dead letter
- **AND** it SHALL create one `dead_letter` terminal action and SHALL refuse
  late route success/retry and later bug calls for that message

#### Scenario: Unknown route outcome becomes ambiguity, not dead letter

- **WHEN** a `route_pending` reservation times out or otherwise cannot prove
  whether target dispatch occurred
- **THEN** the Switchboard SHALL preserve the reservation as an explicit
  ambiguous dashboard-turn outcome
- **AND** it SHALL NOT dead-letter, retry the route automatically, or accept a
  later bug action for that message

#### Scenario: Lane B bug report is durably filed to QA and never domain-routed

- **WHEN** a dashboard message is classified as a bug or system report
- **THEN** the classification session SHALL call `file_bug_report` instead of
  `route_to_butler`
- **AND** `file_bug_report` SHALL choose the singular `bug_report` action,
  journal its `qa_report` and `conversation_reply` effects, and relay to QA
  using their stable action identities
- **AND** the message SHALL NOT be routed to a domain butler via
  `route_to_butler`
- **AND** an in-thread reply SHALL say the report was filed only after the QA
  receipt and reply effect are durably complete
- **AND** a pending, failed, cancelled, or ambiguous action SHALL be reported
  with that exact durable outcome rather than a filed claim

#### Scenario: Lane exclusivity refuses a route after a bug call

- **WHEN** a dashboard classification session calls `file_bug_report` and then
  calls `route_to_butler` for the same message
- **THEN** `route_to_butler` SHALL refuse to dispatch a domain butler with
  `status: "refused"` and `reason: "dashboard_lane_conflict"`
- **AND** it SHALL log the conflict with conversation ID and attempted target

#### Scenario: Lane exclusivity refuses a bug after an earlier route reservation

- **WHEN** a dashboard classification session calls `route_to_butler` and then
  calls `file_bug_report` for the same message
- **THEN** `file_bug_report` SHALL refuse with
  `reason: "dashboard_lane_conflict"` and SHALL NOT create a bug action, relay
  QA, or replace the already-reserved route target
- **AND** the owner-visible status SHALL state that the existing domain route is
  still authoritative and that no bug report was filed
- **AND** the guard SHALL remain scoped to dashboard sessions so non-dashboard
  routing flows are unaffected

#### Scenario: Definitively unroutable dashboard message dead-letters truthfully

- **WHEN** a dashboard classification makes no lane decision, raises before any
  route reservation, or a `route_pending` reservation has definitive evidence
  that no route dispatch occurred
- **THEN** the Switchboard SHALL choose or transition to the singular
  `dead_letter` action and journal its `dead_letter_capture` and
  `conversation_reply` effects
- **AND** it SHALL not create a duplicate capture or second terminal reply on
  recovery
- **AND** it SHALL expose a pending, failed, cancelled, or ambiguous action
  truthfully rather than claiming the message was filed for manual review
- **AND** it SHALL NOT silently fall back to routing the message to `general`

#### Scenario: Route acknowledgement remains terminal only for the synchronous dead-letter net

- **WHEN** `route_to_butler` dispatches via `route.execute` and the target
  returns `accepted` or `ok` for a dashboard message
- **THEN** the dead-letter net SHALL treat that acknowledgement as terminal for
  the synchronous route decision only
- **AND** a later session crash, hang, or timeout SHALL remain outside this
  requirement's terminal-action journal, which governs only bug-report and
  dead-letter effects
