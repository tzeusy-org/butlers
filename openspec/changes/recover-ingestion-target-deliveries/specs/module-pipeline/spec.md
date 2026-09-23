## MODIFIED Requirements

### Requirement: Decomposition-to-Routing Fan-Out

After decomposition produces conceptual messages, the pipeline SHALL call `route()` for each target butler, tracking outcomes in `dispatch_outcomes`. For ordinary non-dashboard `route.execute` concepts, it SHALL commit the complete decomposed target plan and all segment intents before the first such call. The first dispatch attempt for each segment of one event SHALL follow committed concept ordinal sequentially; a failed, waiting, or ambiguous first attempt SHALL release the next segment rather than block unrelated fan-out indefinitely. Later safe retries and receipt reconciliation SHALL be independent per intent and need not repeat the original sequence. Per-butler `dispatch_outcomes` remain a compatibility aggregate; the durable intents retain authoritative per-segment state when several concepts name the same butler.

ID: REQ-module-pipeline-002
Source: RFC 0003 §Conversation-History Decomposition and Fan-Out; design.md Decisions 1 and 4
Scope: v1-mandatory

#### Scenario: Sequential fan-out routing

- **WHEN** decomposition produces N conceptual messages targeting different butlers
- **THEN** the first `route()` attempt is made sequentially in committed concept order for each conceptual message of that event
- **AND** each call passes the cherry-picked excerpts as the routed payload
- **AND** ordinary `route.execute` calls begin only after all of their segment intents and the complete decomposition result commit
- **AND** a prior segment's failed, waiting, or ambiguous first attempt does not prevent the next segment's first attempt; subsequent retries are independently fenced

#### Scenario: Dispatch outcomes recorded

- **WHEN** fan-out routing completes (success or partial failure)
- **THEN** `dispatch_outcomes` on the `message_inbox` row is updated with per-butler results
- **AND** format matches existing dispatch_outcomes schema: `{butler_name: {status, error, timestamp}}`
- **AND** those fields summarize, rather than replace, the authoritative per-segment intent states and receipts

#### Scenario: Lifecycle state after decomposition

- **WHEN** decomposition and fan-out complete successfully
- **THEN** `lifecycle_state` is set to `"routed"` (same as standard routing)
- **AND** `decomposition_output` contains the full signal-extraction result

#### Scenario: Several concepts share one target

- **WHEN** two ordinary concepts for one butler have different segment identities and only one is accepted
- **THEN** the per-butler compatibility outcome and parent lifecycle SHALL remain non-complete rather than overwrite the unresolved segment with the accepted one
- **AND** the two per-segment intent states and receipts SHALL remain independently queryable

## ADDED Requirements

### Requirement: Classification decisions precede ordinary target dispatch

For non-dashboard ingestion-to-domain `route.execute`, the pipeline SHALL finish and durably record the classified target plan before any target call. Model tool calls, deterministic triage bypasses, and fallback inference SHALL all use the same delivery-intent boundary; classification failure SHALL NOT create a speculative target delivery.

ID: REQ-module-pipeline-001
Source: RFC 0003 §Pre-Classification Triage Pipeline; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Model names a target through a tool call

- **WHEN** a non-dashboard classification session calls `route_to_butler`
- **THEN** the pipeline SHALL record the target decision without performing its `route.execute` side effect during classification
- **AND** target dispatch SHALL begin only after the classified plan and all corresponding intents commit

#### Scenario: Bypass and fallback use the same boundary

- **WHEN** deterministic triage or no-tool-call fallback selects an ordinary domain target
- **THEN** that target SHALL enter the same persisted intent and acceptance path before dispatch
- **AND** no fallback SHALL replace an already committed plan during retry

#### Scenario: Classification fails before a target decision

- **WHEN** classification fails or times out before a valid target set is complete
- **THEN** no ordinary target call SHALL occur and no target intent SHALL falsely claim that classification succeeded

#### Scenario: Dashboard lane remains synchronous

- **WHEN** the source channel is `dashboard`
- **THEN** the existing dashboard lane, acknowledgement, and dead-letter contract SHALL remain in force without entering this delivery-intent path
