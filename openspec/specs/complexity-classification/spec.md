# Complexity Classification

## Purpose

Defines the complexity classification system used for dynamic model routing. Complexity tiers determine which model and runtime adapter are selected at spawn time, enabling cost-efficient model selection proportional to task difficulty.

## ADDED Requirements

### Requirement: Complexity Enum
The system SHALL define a complexity classification enum with six canonical tiers representing model capability and resource requirements. The legacy four-tier vocabulary (`trivial`, `medium`, `high`, `extra_high`) plus `discretion` and `self_healing` was retired in migration `core_093`. Callers that still emit a legacy value are remapped with a loud deprecation warning by `_check_deprecated_tier()` in `src/butlers/core/model_routing.py`.

#### Scenario: Enum values
- **WHEN** a complexity classification is assigned
- **THEN** it MUST be one of: `reasoning`, `workhorse`, `cheap`, `specialty`, `local`, `legacy`

#### Scenario: Enum ordering
- **WHEN** complexity tiers are compared for tier fallthrough during model resolution
- **THEN** the canonical order (highest to lowest capability) is: `reasoning` > `workhorse` > `cheap` > `specialty` > `local` > `legacy`

#### Scenario: Legacy vocabulary remapping
- **WHEN** a caller supplies a retired tier value
- **THEN** it is remapped as follows: `trivial` to `cheap`, `medium` to `workhorse`, `high` to `reasoning`, `extra_high` to `reasoning`, `discretion` to `specialty`, `self_healing` to `specialty`
- **AND** a deprecation warning is logged naming the offending caller

### Requirement: Switchboard Complexity Classification
The Switchboard SHALL classify the complexity of each inbound request as part of its existing LLM-driven routing decision. Classification piggybacks on the routing LLM call — no additional LLM invocation is required.

#### Scenario: Complexity in routing output schema
- **WHEN** the Switchboard's routing LLM produces a routing decision
- **THEN** the structured output includes a `complexity` field per segment alongside `target_butler` and `sub_prompt`
- **AND** the complexity value MUST be a valid complexity enum value

#### Scenario: Single-domain routing with complexity
- **WHEN** a message is routed to a single butler
- **THEN** the routing output includes a single segment with `target_butler`, `sub_prompt`, and `complexity`

#### Scenario: Multi-domain decomposition with per-segment complexity
- **WHEN** a message is decomposed into multiple segments for different butlers
- **THEN** each segment has an independent `complexity` classification
- **AND** different segments of the same message MAY have different complexity levels

#### Scenario: Classification failure defaults to workhorse
- **WHEN** the LLM output omits the `complexity` field or returns an invalid value
- **THEN** the complexity defaults to `workhorse`
- **AND** a warning is logged noting the classification failure

#### Scenario: Deterministic triage bypass preserves complexity default
- **WHEN** a message is routed via deterministic pre-classification triage (rule-based, thread affinity) without LLM classification
- **THEN** the complexity defaults to `workhorse`

### Requirement: Complexity Propagation Through Route Dispatch
The Switchboard SHALL propagate the classified complexity to the target butler when dispatching routed requests.

#### Scenario: Complexity in route.v1 envelope
- **WHEN** Switchboard dispatches a `route.v1` envelope to a downstream butler
- **THEN** the envelope's `input` section includes a `complexity` field with the classified tier value

#### Scenario: Target butler receives complexity
- **WHEN** a butler's `route.execute` handler processes a `route.v1` envelope
- **THEN** the complexity value is extracted and passed to `spawner.trigger(complexity=...)`

### Requirement: Trigger API Complexity Parameter
The manual trigger API SHALL accept an optional complexity parameter for operator-controlled model selection.

#### Scenario: TriggerRequest with complexity
- **WHEN** a `POST /api/butlers/{name}/trigger` request includes a `complexity` field
- **THEN** the value is passed through to `spawner.trigger(complexity=...)`

#### Scenario: TriggerRequest without complexity
- **WHEN** a `POST /api/butlers/{name}/trigger` request omits the `complexity` field
- **THEN** the complexity defaults to `workhorse`

### Requirement: Tick Trigger Complexity
Internal tick-triggered sessions SHALL use a fixed low-cost complexity tier (`cheap`, the canonical successor to the retired `trivial` tier).

#### Scenario: Tick uses cheap complexity
- **WHEN** a butler session is triggered by the tick handler
- **THEN** the complexity is set to `cheap`
