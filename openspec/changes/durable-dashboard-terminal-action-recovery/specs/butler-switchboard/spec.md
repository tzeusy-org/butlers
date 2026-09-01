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
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-001; design.md Decisions 1-5
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
  `accepted` acknowledgement from the target butler
- **THEN** the Switchboard SHALL stamp `routed_butler` on the conversation
  best-effort without failing the route call if stamping fails

#### Scenario: First lane is reserved before irreversible dispatch

- **WHEN** a dashboard classification session first calls `route_to_butler`,
  `file_bug_report`, or reaches the synchronous dead-letter net
- **THEN** the Switchboard SHALL atomically reserve `route_pending`,
  `bug_report`, or `dead_letter` for that immutable user message before it
  dispatches a domain butler, relays QA, or captures a dead letter
- **AND** a definitive `accepted` route acknowledgement SHALL promote
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
  compute the canonical fingerprint, journal its `qa_report` and
  `conversation_reply` effects, and relay the canonical finding to QA using
  their stable action identities
- **AND** the message SHALL NOT be routed to a domain butler via
  `route_to_butler`
- **AND** an in-thread reply SHALL say the report was filed only after the QA
  receipt and reply effect are durably complete and SHALL include the safe case
  reference derived from the first 12 characters of the canonical fingerprint
- **AND** a pending, failed, cancelled, or ambiguous action SHALL be reported
  with that exact durable outcome rather than a filed claim

#### Scenario: Lane exclusivity refuses a route after a bug call

- **WHEN** a dashboard classification session calls `file_bug_report` and then
  calls `route_to_butler` for the same message
- **THEN** `route_to_butler` SHALL refuse to dispatch a domain butler with
  `status: "refused"` and `reason: "dashboard_lane_conflict"`
- **AND** it SHALL log the conflict at WARNING with conversation ID and attempted
  target

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

#### Scenario: First-lane-wins retires dashboard co-occurrence output

- **WHEN** a dashboard message already has a `route_pending` or `route`
  reservation and a later `file_bug_report` call is refused
- **THEN** the result SHALL expose `dashboard_lane_conflict` and SHALL NOT emit
  `co_occurring_dispatched_targets`, `co_occurring_attempted_only_targets`, or
  `co_occurring_route_targets` for that dashboard message
- **AND** the prior dashboard route remains authoritative; this retirement SHALL
  not alter non-dashboard route-result behavior

#### Scenario: Definitively unroutable dashboard message dead-letters truthfully

- **WHEN** a dashboard classification makes no lane decision, raises before any
  route reservation, or a `route_pending` reservation has definitive evidence
  that no route dispatch occurred
- **THEN** the Switchboard SHALL choose or transition to the singular
  `dead_letter` action and journal its `dead_letter_capture` and
  `conversation_reply` effects
- **AND** it SHALL not create a duplicate capture or second terminal reply on
  recovery
- **AND** once its capture and reply effects are durably complete, the in-thread
  reply SHALL include the safe dead-letter case/capture reference
- **AND** it SHALL expose a pending, failed, cancelled, or ambiguous action
  truthfully rather than claiming the message was filed for manual review
- **AND** it SHALL NOT silently fall back to routing the message to `general`

#### Scenario: Route acknowledgement remains terminal only for the synchronous dead-letter net

- **WHEN** `route_to_butler` dispatches via `route.execute` and the target
  returns `accepted` for a dashboard message
- **THEN** the dead-letter net SHALL treat that acknowledgement as terminal for
  the synchronous route decision only
- **AND** a later session crash, hang, or timeout SHALL remain outside this
  requirement's terminal-action journal, which governs only bug-report and
  dead-letter effects
- **AND** it SHALL NOT capture that later failure in the dead-letter queue or
  persist an additional in-thread reply on its behalf

#### Scenario: Lane A — data statement routes with deterministic confirm-loop context

- **WHEN** a dashboard message is classified as a data statement or correction
- **THEN** the classification session SHALL call `route_to_butler` exactly as for any other channel
- **AND** the routed envelope's `input.context` SHALL deterministically carry the conversation's `conversation_id`, its `page_context` (if any), and instructions to interpret the statement, apply it, and confirm via `conversation_reply` — appended in code regardless of what the classification session itself wrote into `context` or `prompt`

#### Scenario: Lane A — first successful route stamps sticky routed_butler

- **WHEN** `route_to_butler` for a dashboard-originated message receives an `accepted` status from the target butler
- **THEN** the Switchboard SHALL stamp `routed_butler` on the conversation (best-effort; a stamping failure SHALL NOT fail the route call)

#### Scenario: Lane B — bug/system report is filed to QA, never routed to a domain butler

- **WHEN** a dashboard message is classified as a bug or system report (e.g. "the concentration chart is empty for child-of")
- **THEN** the classification session SHALL call `file_bug_report` instead of `route_to_butler`
- **AND** `file_bug_report` SHALL compute a canonical fingerprint and relay a finding to the QA staffer via the internal `route()` function targeting `report_finding` (the same plumbing QA canary injection uses)
- **AND** the message SHALL NOT be routed to any domain butler via `route_to_butler`
- **AND** the tool SHALL post a `conversation_reply` acknowledgment containing the case reference (the fingerprint's first 12 characters), whether or not the QA relay itself succeeded

#### Scenario: Lane exclusivity is enforced at the tool layer, not just the classification prompt

- **WHEN** a dashboard classification session calls `file_bug_report` and then calls `route_to_butler` for the same session (regardless of what the classification prompt instructs)
- **THEN** `route_to_butler` SHALL refuse to dispatch to a domain butler and SHALL return a structured refusal (`status: "refused"`, `reason: "dashboard_lane_conflict"`) instead of invoking `route.execute`
- **AND** the refusal SHALL be logged at WARNING with the conversation id and the attempted target butler
- **WHEN** a dashboard classification session calls `route_to_butler` and then calls `file_bug_report` for the same session
- **THEN** `file_bug_report` SHALL still file the bug report (bug reports are terminal and are never suppressed)
- **AND** the co-occurrence SHALL be logged at WARNING with the conversation id and outcome-qualified targets, and SHALL be surfaced in `file_bug_report`'s own result (`dashboard_lane_conflict`) and in the pipeline's `RoutingResult.route_result` rather than being hidden by tool-call extraction that stops at the first matching call
- **AND** acknowledged domain-butler dispatches SHALL appear only in `co_occurring_dispatched_targets`; failed or refused calls with no acknowledgement in the classification session SHALL appear only in `co_occurring_attempted_only_targets`; the two lists SHALL be mutually exclusive and the ambiguous `co_occurring_route_targets` field SHALL NOT be emitted
- **AND** this exclusivity guard SHALL be scoped to dashboard-source sessions only (a `dashboard_context` carrying a `conversation_id`) — non-dashboard Switchboard flows (e.g. domain-butler-initiated `route_to_butler` calls, QA canary injection) SHALL be unaffected

#### Scenario: Unroutable dashboard message dead-letters and notifies the owner

- **WHEN** a dashboard message's classification session calls neither `route_to_butler` nor `file_bug_report` (e.g. an ambiguous or unclassifiable message), the classification spawn raises an exception, or `route_to_butler` was attempted but no target acknowledged the route because every `route.execute` dispatch failed
- **THEN** the Switchboard SHALL capture the request to the dead-letter queue (`source_table="message_inbox"`)
- **AND** SHALL persist an in-thread `conversation_reply` telling the owner a lane decision could not be made, referencing the dead-letter case id
- **AND** SHALL NOT silently fall back to routing the message to the `general` butler — that fallback is specific to non-dashboard channels

#### Scenario: Route acknowledgement is terminal for the dead-letter net; downstream session completion is out of scope

- **WHEN** `route_to_butler` dispatches via `route.execute` and the target butler returns an `accepted` (or `ok`) status for a dashboard-originated message
- **THEN** the dead-letter net gate SHALL treat that acknowledgement as terminal success for the synchronous reply contract — the target butler has confirmed only that it accepted the dispatch, not that the spawned downstream session will run to completion
- **AND** if that downstream session subsequently crashes, hangs, or times out after acknowledgement, this contract SHALL NOT capture the failure to the dead-letter queue and SHALL NOT persist an additional in-thread reply on its behalf; the owner is left with whatever live-only signal (e.g. a widget-side `SESSION_TIMEOUT`) the caller surfaces independently
- **AND** this ack-terminal boundary is a deliberate, accepted scope decision for the synchronous reply contract, not an oversight — closing the last hop (e.g. a reply-watch timeout that persists an in-thread failure note when an acknowledged downstream session never completes) is a possible future extension, not a current requirement
