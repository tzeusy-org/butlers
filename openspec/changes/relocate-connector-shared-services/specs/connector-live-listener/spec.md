## MODIFIED Requirements

### Requirement: Discretion Layer

ID: REQ-connector-live-listener-006
Source: REQ-connector-base-spec-007; bu-ep60gq.3 adopted path-only relocation
Scope: v1-mandatory

The live-listener SHALL use the shared discretion layer (`butlers.connectors.discretion`) to evaluate transcribed utterances in context and decide whether they warrant butler attention. See `connector-base-spec` for the full shared discretion contract.

#### Scenario: Shared discretion module
- **WHEN** the live-listener connector uses the discretion layer
- **THEN** it imports `DiscretionEvaluator` from `butlers.connectors.discretion` and `DiscretionDispatcher` from `butlers.core.discretion_dispatcher`
- **AND** it creates a single `DiscretionDispatcher(pool=db_pool)` and injects it into each per-mic `DiscretionEvaluator`
- **AND** the dispatcher resolves the discretion model from the shared model catalog (`complexity_tier='discretion'`) via the RuntimeAdapter interface

#### Scenario: Sliding context window
- **WHEN** an utterance is transcribed
- **THEN** it is appended to a per-mic sliding context window
- **AND** the window retains the last `window_size` utterances (default: 10) or utterances within the last `window_seconds` (default: 300), whichever produces fewer entries
- **AND** each entry in the window includes: transcribed text, timestamp, and source name (mic name)

#### Scenario: Discretion evaluation with weight
- **WHEN** a new utterance is added to the context window
- **THEN** the discretion layer sends the context window plus the latest utterance to the catalog-resolved LLM via the `DiscretionDispatcher`
- **AND** the LLM MUST respond with a structured verdict: `FORWARD` (with a one-line reason) or `IGNORE`
- **AND** the model used is determined by the `discretion` tier in the shared model catalog (default seed: `qwen3.5:9b` via opencode/ollama)
- **AND** all voice utterances use `weight=1.0` (no sender identity available for ambient audio), meaning the LLM is always called and failures always fail-open

#### Scenario: Discretion verdicts
- **WHEN** the discretion LLM responds `FORWARD`
- **THEN** the utterance proceeds to `ingest.v1` envelope construction and Switchboard submission
- **AND** the discretion reason is included in `payload.raw.discretion_reason`
- **WHEN** the discretion LLM responds `IGNORE`
- **THEN** the utterance is discarded and `connector_live_listener_discretion_total{mic, verdict="ignore"}` is incremented
- **AND** the IGNORE verdict is persisted to `connectors.filtered_events` via a per-mic `FilteredEventBuffer` (see `connector-filtered-events`), with `filter_reason` labeled `discretion:ignore:<kind>` (`classify_ignore_kind` distinguishes a genuine LLM verdict from each fail-closed default — auth failure, timeout, parse error, same-tier failover exhaustion, or other error)
- **AND** the persisted `full_payload.raw` is empty and `subject_or_preview` is the transcript truncated to 200 chars (metadata-tier — ambient audio can capture bystanders who never opted into the connector)

#### Scenario: Discretion layer failure
- **WHEN** the discretion LLM call fails (timeout, connection error, malformed response)
- **THEN** the connector defaults to `FORWARD` (fail-open, because voice weight=1.0 >= weight_fail_open threshold)
- **AND** `connector_live_listener_discretion_failures_total{mic, error_type}` is incremented
- **AND** the failed evaluation is logged at WARNING level

#### Scenario: Discretion latency constraint
- **WHEN** the discretion LLM is called
- **THEN** the call MUST use a timeout of `LIVE_LISTENER_DISCRETION_TIMEOUT_S` (default: 3 seconds)
- **AND** if the timeout is exceeded, the utterance is treated as `FORWARD` (fail-open) and the timeout is logged

#### Scenario: Privacy boundary
- **WHEN** the discretion model catalog entry uses an `opencode` runtime with a local Ollama model (e.g. `ollama/qwen3.5:9b`)
- **THEN** transcribed text does not leave the local network until an utterance is forwarded to the Switchboard
- **WHEN** the discretion model catalog entry uses a cloud-hosted runtime (e.g. `claude`, `codex`)
- **THEN** ALL transcribed text (including ignored utterances) is sent to the cloud LLM for evaluation
- **AND** operators can control this privacy boundary by selecting the appropriate model in the Settings UI

## Source References
- Existing shared discretion contract: REQ-connector-base-spec-007
- Adopted behavior-preserving relocation: bu-ep60gq.3 S2
- about/craft-and-care/interfaces-and-dependencies.md (one home per concept)
