## MODIFIED Requirements

### Requirement: LLM-Driven Routing Contract
Switchboard performs discretionary routing through a pluggable LLM CLI runtime. The router classifies incoming messages and decomposes multi-domain requests into segments routed to specialist butlers. Each segment SHALL include a complexity classification alongside the routing decision.

#### Scenario: Single-domain routing
- **WHEN** a message has one clear domain match (e.g., health, relationship, finance, travel)
- **THEN** Switchboard routes the entire message to that specialist butler via `route_to_butler`
- **AND** the sub-prompt is self-contained with all relevant entities and context
- **AND** the routing output includes a `complexity` classification for the segment

#### Scenario: Multi-domain decomposition
- **WHEN** a message spans multiple domains with clear boundaries
- **THEN** Switchboard decomposes into one sub-prompt per target butler
- **AND** each segment carries self-contained prompt text, segment metadata (sentence span references, character offset ranges, or decomposition rationale), and an independent `complexity` classification

#### Scenario: Domain classification rules
- **WHEN** a message arrives for classification
- **THEN** the LLM classifier applies domain-specific rules: finance for payment/billing/subscription signals, travel for booking/itinerary/flight signals, relationship for contacts/interactions/social, health for medications/measurements/symptoms/diet/nutrition, and general as the catch-all fallback

#### Scenario: Finance vs Travel tie-break rules
- **WHEN** a message contains both financial and travel semantics
- **THEN** finance wins when the primary intent is billing/refund/payment resolution
- **AND** travel wins when the primary intent is itinerary/booking tracking

#### Scenario: Ambiguity fallback to general
- **WHEN** routing confidence is below the configured threshold or LLM output is ambiguous
- **THEN** Switchboard routes the full original message to the `general` butler
- **AND** the ambiguity-triggered fallback is tagged in lifecycle records and observable in metrics
- **AND** the complexity defaults to `medium`

#### Scenario: Classification failure fallback
- **WHEN** classification fails (LLM timeout, parse error, empty response)
- **THEN** Switchboard routes the entire message to the `general` butler with the original text intact
- **AND** the complexity defaults to `medium`

#### Scenario: Runtime model family support
- **WHEN** Switchboard spawns a routing LLM instance
- **THEN** the runtime supports Claude Code, Codex, and Opencode families
- **AND** lightweight, capable models are preferred for fast classification/decomposition

#### Scenario: Conversation history context for routing
- **WHEN** the source channel is a real-time messaging channel (Telegram, WhatsApp, Slack, Discord)
- **THEN** recent conversation history (last 15 minutes or last 30 messages, whichever is more) is provided to the router for context
- **AND** the router only routes the current message, using history only to improve routing accuracy

#### Scenario: Email conversation history
- **WHEN** the source channel is email
- **THEN** the full email chain is provided, truncated to 50,000 tokens (preserving newest messages)
- **AND** the router uses chain context to improve routing but only routes the current message

## ADDED Requirements

### Requirement: Complexity Classification Guidelines
The Switchboard routing prompt SHALL include complexity classification guidelines to produce consistent tier assignments.

#### Scenario: Trivial classification signals
- **WHEN** the routing LLM evaluates message complexity
- **THEN** `trivial` is assigned for: status checks, simple confirmations, single-fact lookups, acknowledgements, and short replies that require no reasoning

#### Scenario: Medium classification signals
- **WHEN** the routing LLM evaluates message complexity
- **THEN** `medium` is assigned for: standard single-domain tasks, straightforward questions, routine data entry, and tasks requiring moderate context but no complex reasoning

#### Scenario: High classification signals
- **WHEN** the routing LLM evaluates message complexity
- **THEN** `high` is assigned for: multi-step reasoning tasks, cross-referencing multiple data sources, analysis requiring judgment, and tasks with nuanced instructions

#### Scenario: Extra-high classification signals
- **WHEN** the routing LLM evaluates message complexity
- **THEN** `extra_high` is assigned for: complex multi-domain analysis, long-horizon planning, tasks requiring extensive research or synthesis, and tasks with ambiguous or open-ended requirements

### Requirement: Complexity in Route Dispatch
The Switchboard SHALL include the classified complexity when dispatching routed requests to downstream butlers.

#### Scenario: Route.v1 envelope carries complexity
- **WHEN** Switchboard constructs a `route.v1` dispatch envelope
- **THEN** the `input` section includes `complexity` with the classified tier value

#### Scenario: Route handler extracts complexity
- **WHEN** a downstream butler's `route.execute` handler receives a `route.v1` envelope
- **THEN** the `complexity` value is extracted from `input.complexity` and passed to `spawner.trigger(complexity=...)`

#### Scenario: Missing complexity in envelope defaults to medium
- **WHEN** a `route.v1` envelope arrives without a `complexity` field in `input`
- **THEN** the handler defaults complexity to `medium`
