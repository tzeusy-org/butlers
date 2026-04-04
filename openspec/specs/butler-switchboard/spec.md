### Requirement: Domain Butler Registry

The Switchboard SHALL maintain a registry of all agents (butlers and staffers) and SHALL only route user messages to butler-typed agents. Staffer-typed agents are excluded from user-message classification but remain reachable for butler-to-staffer routing.

#### Scenario: Staffers excluded from user-message classification
- **WHEN** the Switchboard classifies an incoming user message
- **THEN** it SHALL only consider agents with `type = "butler"` as routing candidates
- **AND** agents with `type = "staffer"` SHALL be excluded from the candidate set
- **AND** this exclusion applies to the classification/routing layer only — staffers remain reachable via other mechanisms

#### Scenario: Butler-to-staffer routing preserved
- **WHEN** a butler invokes `notify()` targeting a staffer (e.g., messenger for outbound delivery)
- **THEN** the Switchboard SHALL route the request to the staffer as it does today
- **AND** this routing path is not affected by the user-message classification exclusion

#### Scenario: Staffer registration includes type
- **WHEN** a staffer registers with the Switchboard at startup
- **THEN** the registration payload SHALL include `type = "staffer"`
- **AND** the Switchboard's registry SHALL store this type field alongside the agent's name, port, and liveness state
- **AND** the eligibility sweep SHALL continue to track staffer liveness (staffers are infrastructure-critical and their liveness matters for butler-to-staffer routing)

#### Scenario: Lifestyle domain classification
- **WHEN** the Switchboard classifies an incoming message
- **AND** the message content relates to music, listening, playlists, entertainment (movies, TV, books, games, podcasts), food preferences, favorite restaurants, cuisines, recipes, hobbies, personal interests, leisure activities, or daily routines
- **THEN** the Switchboard SHALL route the message to the `lifestyle` butler at `http://localhost:41109`

#### Scenario: Multi-butler fanout with lifestyle overlap
- **WHEN** a message contains both lifestyle and health signals (e.g., "I've been stress-eating Thai food all week")
- **THEN** the Switchboard SHALL route to both `lifestyle` (food preference: Thai) and `health` (stress eating pattern)
- **AND** each butler SHALL extract domain-relevant facts independently

#### Scenario: Lifestyle vs General disambiguation
- **WHEN** a message could be classified as either lifestyle or general
- **AND** the message relates to taste, preferences, entertainment, or routines
- **THEN** the Switchboard SHALL prefer routing to `lifestyle` over `general`
- **AND** `general` SHALL only receive messages that do not fit any domain butler's scope

#### Scenario: Misroute re-dispatch restricted to butlers
- **WHEN** `correct_route` is called with a `correct_butler` target
- **THEN** the target SHALL be validated as a butler-typed agent (not a staffer)
- **AND** if the target is a staffer, the tool SHALL return `status=failed` with a summary explaining that user messages cannot be re-dispatched to staffers

---

## ADDED Requirements

### Requirement: Misroute Correction Re-dispatch
The Switchboard SHALL expose a `correct_route` MCP tool that accepts a misroute correction request from any butler and re-dispatches the original message to the correct target butler. This tool is called by downstream butlers' `correct` tool when handling `misroute` correction type.

#### Scenario: Successful misroute re-dispatch
- **WHEN** `correct_route` is called with `request_id` (the original ingestion event's request_id), `correct_butler` (the intended target), and `correction_reason` (why the original routing was wrong)
- **THEN** the Switchboard SHALL look up the original ingestion event by `request_id`, construct a new route dispatch to `correct_butler` with the original message content, dispatch it, and return the re-dispatch outcome
- **AND** the original `message_inbox` record SHALL be annotated with `correction_status=rerouted` and `corrected_to_butler` in its metadata

#### Scenario: Re-dispatch with expired ingestion event
- **WHEN** `correct_route` is called with a `request_id` whose ingestion event has been dropped from `message_inbox` (past 1-month retention)
- **THEN** the tool SHALL return `status=failed` with a summary explaining that the original message is no longer available for re-dispatch
- **AND** the summary SHALL suggest the user re-send the message to the correct butler directly

#### Scenario: Re-dispatch to unregistered butler rejected
- **WHEN** `correct_route` is called with a `correct_butler` that is not in the Switchboard's butler registry
- **THEN** the tool SHALL return `status=failed` with a summary listing the available butlers

#### Scenario: Re-dispatch preserves original request context
- **WHEN** a misroute correction re-dispatches a message
- **THEN** the re-dispatched request SHALL carry the original `request_id` and source context (source_channel, source_sender_identity)
- **AND** the re-dispatch SHALL add `correction_id` to the request metadata to link back to the correction audit trail

#### Scenario: Re-dispatch returns new session ID for traceability
- **WHEN** `correct_route` successfully re-dispatches a message to the correct butler
- **THEN** the return value SHALL include `new_session_id` (the UUID of the session created by the re-dispatch on the correct butler)
- **AND** the calling butler's `correct` tool SHALL propagate this `new_session_id` in its own `correction_details` and `summary`

#### Scenario: Original routing outcome updated
- **WHEN** a misroute correction is successfully re-dispatched
- **THEN** the Switchboard's lifecycle record for the original request SHALL be updated to reflect the correction: original routing marked as `corrected`, new routing recorded alongside
