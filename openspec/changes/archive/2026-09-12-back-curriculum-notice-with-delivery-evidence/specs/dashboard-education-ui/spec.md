## MODIFIED Requirements


### Requirement: Curriculum request outcome receipt

After a curriculum request is accepted, the Education page SHALL render a receipt panel for the tracked request, fed by `GET /curriculum-requests/{request_id}` (falling back to `GET /curriculum-requests/latest` when no request has been submitted in this session).

The panel SHALL render four distinct states and SHALL NOT collapse any of them into another:
- **accepted / running** — the request was accepted and work is in flight. The panel SHALL say so and SHALL NOT claim the curriculum exists or that the owner has been contacted.
- **completed** — the receipt carries terminal evidence. The panel SHALL name the curriculum topic and SHALL distinguish `calibration_ready_at` being set (the teaching flow has started calibrating) from it being unset, rather than implying calibration in both cases. The panel SHALL state what became of the calibration notice as a line separate from calibration, so that a reader cannot take calibration having started as evidence of having been messaged.
- **failed** — the panel SHALL render the terminal `failure_reason` in owner-readable language and SHALL offer a retry.
- **unavailable** — when `receipts_available` is `false`, the panel SHALL say the status could not be read, and SHALL NOT render an all-clear or an empty "no request" state.

The panel SHALL claim that a message went out only from `calibration_notice_outcome`, and only for the value `delivered`. That claim SHALL be worded as the delivery channel having accepted the message, because that is the strongest thing the notification path attests; the panel SHALL NOT state or imply that the owner received or read anything.

For every other outcome — a recorded `failed`, `suppressed`, `deferred` or `coalesced` dispatch, a `no_record` absence, an `unproven` read, a null outcome, or an outcome the frontend does not recognise — the panel SHALL say that contact is not confirmed, and SHALL direct the owner to this panel rather than to their messages. An unrecognised outcome SHALL degrade to "not confirmed" and never to an implied yes, so a new backend outcome cannot silently become a delivery claim in the UI.

The panel SHALL provide doors to the evidence it names: a link to the session (`/sessions/{session_id}`) whenever `session_id` is present, including on the failure path, and a control that opens the correlated curriculum whenever `mind_map_id` is present.

While the receipt is non-terminal the query SHALL poll; once terminal it SHALL stop polling.

In fallback mode (no request submitted in this session, so the panel reads the latest request), a receipt that settled longer than the recency window ago SHALL NOT be rendered — otherwise a curriculum created weeks ago would keep a permanent card on the page. A request tracked by `request_id` from this session's 202 SHALL always be rendered, however long ago it settled.

The panel SHALL be announced to assistive technology as a live status region, and every door SHALL be a keyboard-reachable control with an accessible name.

#### Scenario: Accepted request does not claim completion

- **WHEN** the tracked receipt has status `accepted`
- **THEN** the panel SHALL describe the request as accepted and in progress
- **AND** SHALL NOT state that a curriculum was created or that the butler has messaged the owner

#### Scenario: Completed request opens its doors

- **WHEN** the tracked receipt has status `completed` with a `mind_map_id` and a `session_id`
- **THEN** the panel SHALL offer a control that selects that curriculum
- **AND** SHALL offer a link to `/sessions/{session_id}`

#### Scenario: Calibration started does not become "the butler messaged you"

- **WHEN** the tracked receipt has status `completed` with `calibration_ready_at` set
- **AND** `calibration_notice_outcome` is `failed`, `suppressed`, `no_record`, `unproven`, null, or a value the frontend does not recognise
- **THEN** the panel SHALL state that contact is not confirmed and point the owner at this panel
- **AND** SHALL NOT state that a message reached the owner
- **AND** SHALL still state that calibration has started

#### Scenario: Channel acceptance is stated as channel acceptance

- **WHEN** the tracked receipt carries `calibration_notice_outcome: "delivered"`
- **THEN** the panel SHALL say the owner's messaging channel accepted the butler's starting message
- **AND** SHALL NOT describe it as read, seen, or received by the owner

#### Scenario: Failed request is legible and retryable

- **WHEN** the tracked receipt has status `failed` with `failure_reason: "trigger_unreachable"`
- **THEN** the panel SHALL explain that the butler could not be reached
- **AND** SHALL offer a retry control
- **AND** SHALL still link the session when `session_id` is present

#### Scenario: Unavailable status is not an all-clear

- **WHEN** the status read returns `receipts_available: false`
- **THEN** the panel SHALL state that the request status could not be read
- **AND** SHALL NOT render a success, a failure, or an empty state

#### Scenario: Polling stops at a terminal state

- **WHEN** the tracked receipt reaches `completed` or `failed`
- **THEN** the receipt query SHALL stop refetching

#### Scenario: A long-settled fallback receipt is not parked on the page

- **WHEN** no request has been submitted in this session
- **AND** the latest receipt settled longer than the recency window ago
- **THEN** the panel SHALL render nothing

#### Scenario: A request tracked in this session stays visible

- **WHEN** a request submitted in this session settled longer than the recency window ago
- **THEN** the panel SHALL still render its terminal outcome
