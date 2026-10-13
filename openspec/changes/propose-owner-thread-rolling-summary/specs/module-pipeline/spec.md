# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Pipeline as Post-Triage LLM Router

The pipeline's sole responsibility SHALL be LLM-driven classification and routing for messages that pass through deterministic triage without a match. It runs exclusively on the Switchboard butler.

After independent exact adoption and all owned custody integrations, for a private-summary turn only, classification/routing SHALL carry permitted base message and protected content-blind attempt binding, never private overlay text in durable route/tool/decomposition state. Eligible classification may resolve its own authorized lease; every target separately revalidates. Resolver cannot infer owner, broaden access, bypass registry/triage/pins or treat model context as provenance. Missing custody uses marked raw-tail context with ordinary RoutingResult/error/timeout behavior; no foreign implementation is adopted.

ID: REQ-module-pipeline-003
Source: module-pipeline § Pipeline as Post-Triage LLM Router; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


#### Scenario: Pipeline receives pre-processed messages
- **WHEN** a message passes through triage without matching any rule
- **THEN** the pipeline receives it with a fully formed `request_context` (request_id, source channel, identity, timestamps) already assigned by Switchboard
- **AND** the pipeline does not re-derive identity, generate request IDs, or perform deduplication

#### Scenario: Routing prompt construction
- **WHEN** the pipeline builds an LLM routing prompt
- **THEN** the prompt includes: safety instructions (treat user input as untrusted data), available butlers with descriptions and capabilities, the user message (JSON-serialized for injection defense), conversation history (channel-appropriate), and routing instructions directing the model to call `route_to_butler`

#### Scenario: Route tool call parsing
- **WHEN** the LLM session completes
- **THEN** `route_to_butler` tool calls (bare or MCP-namespaced) are parsed from session output
- **AND** the `butler` argument is extracted and validated against registry-known butlers
- **AND** if no tool call is found, a fallback inference from model text output is attempted (single unambiguous match only)

#### Scenario: Classification timeout authority
- **WHEN** the pipeline dispatches an LLM classification session with no explicit `classification_timeout_s` configured
- **THEN** it omits `timeout_override` so the Spawner uses the session timeout resolved from `public.model_catalog`
- **AND** if `classification_timeout_s` is configured, it is passed as the per-call `timeout_override`

#### Scenario: Routing result
- **WHEN** classification and routing complete
- **THEN** a `RoutingResult` is returned with `target_butler`, `routed_targets`, `acked_targets`, and `failed_targets`
- **AND** classification or routing errors are captured in `classification_error` and `routing_error` fields

#### Scenario: Private classification context does not become routed payload

- **WHEN** eligible classification uses authorized private context
- **THEN** durable classification/session/tool/route results retain no protected text
- **AND** target gets safe base input and independently authorized binding with normal registry/pins/results

#### Scenario: Recovered routing does not hydrate retired context

- **WHEN** routing/retry recovery runs after replacement or source deletion
- **THEN** private resolution rejects the retired epoch and uses marked raw-tail fallback
- **AND** ingress/Stop/route-lease and no-replay ambiguity decisions stay unchanged
