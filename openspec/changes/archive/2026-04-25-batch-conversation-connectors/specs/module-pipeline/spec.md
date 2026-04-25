## ADDED Requirements

### Requirement: Conversation Decomposition Branch
The pipeline SHALL detect `control.payload_type == "conversation_history"` on incoming messages and route them through a decomposition branch instead of standard LLM classification.

#### Scenario: Decomposition branch entry
- **WHEN** `pipeline.process()` receives a message where `raw_payload.control.payload_type == "conversation_history"`
- **AND** `triage_decision` is `pass_through` (no policy bypass)
- **THEN** the pipeline enters the conversation decomposition branch
- **AND** does NOT spawn a standard classification CC session

#### Scenario: Policy bypass still honored
- **WHEN** a conversation history message has `triage_decision == "route_to"` with a target
- **THEN** the policy bypass path is followed (direct routing) and decomposition is skipped
- **AND** this preserves existing deterministic routing behavior

#### Scenario: Skip/metadata_only still honored
- **WHEN** a conversation history message has `triage_decision == "skip"` or `"metadata_only"`
- **THEN** the message is handled by existing early-return logic and decomposition is skipped

### Requirement: Decomposition-to-Routing Fan-Out
After decomposition produces conceptual messages, the pipeline SHALL call `route()` for each target butler, tracking outcomes in `dispatch_outcomes`.

#### Scenario: Sequential fan-out routing
- **WHEN** decomposition produces N conceptual messages targeting different butlers
- **THEN** `route()` is called sequentially for each conceptual message
- **AND** each call passes the cherry-picked excerpts as the routed payload

#### Scenario: Dispatch outcomes recorded
- **WHEN** fan-out routing completes (success or partial failure)
- **THEN** `dispatch_outcomes` on the `message_inbox` row is updated with per-butler results
- **AND** format matches existing dispatch_outcomes schema: `{butler_name: {status, error, timestamp}}`

#### Scenario: Lifecycle state after decomposition
- **WHEN** decomposition and fan-out complete successfully
- **THEN** `lifecycle_state` is set to `"routed"` (same as standard routing)
- **AND** `decomposition_output` contains the full signal-extraction result

### Requirement: Empty Decomposition Short-Circuit
When decomposition returns no signals, the pipeline SHALL log the result and terminate processing without invoking any LLM classification.

#### Scenario: No signals terminates processing
- **WHEN** the decomposition step returns `[]`
- **THEN** `decomposition_output` is set to `{"signals": [], "reason": "no_signals_extracted"}`
- **AND** `lifecycle_state` is set to `"decomposed_empty"`
- **AND** no `route()` calls are made
- **AND** no standard LLM classification session is spawned

#### Scenario: Metrics emitted for empty decomposition
- **WHEN** decomposition returns empty
- **THEN** a counter metric `butlers.pipeline.decomposition_empty` is incremented with labels `source_channel` and `connector_type`
