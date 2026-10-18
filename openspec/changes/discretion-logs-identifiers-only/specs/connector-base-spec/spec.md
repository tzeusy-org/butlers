# connector-base-spec

## MODIFIED Requirements

### Requirement: Shared Discretion Layer
Connectors that need noise filtering before Switchboard ingestion SHALL use the shared LLM-based filter (`butlers.connectors.discretion`) that evaluates messages in context and decides whether they warrant butler attention (FORWARD) or should be silently discarded (IGNORE).

The discretion layer uses the project's RuntimeAdapter interface via a dedicated `DiscretionDispatcher` (`butlers.connectors.discretion_dispatcher`), which resolves models from the shared model catalog at the `specialty` complexity tier. This unifies all LLM interaction within the repository under explicit adapter resolution — the same Settings UI and model catalog that manages butler session models also manages discretion models.

ID: REQ-connector-base-spec-007
Source: bu-1wwjrb; [Observed] `src/butlers/connectors/discretion.py` evaluator log sites
Scope: v1-mandatory

#### Scenario: Discretion module location
- **WHEN** a connector integrates the discretion layer
- **THEN** it imports `DiscretionEvaluator` from `butlers.connectors.discretion` (shared module, not connector-specific)
- **AND** it imports `DiscretionDispatcher` from `butlers.connectors.discretion_dispatcher`
- **AND** it creates a `DiscretionDispatcher(pool=db_pool)` and injects it into each `DiscretionEvaluator`

#### Scenario: Discretion model resolution via catalog
- **WHEN** a discretion evaluation requires an LLM call
- **THEN** the `DiscretionDispatcher` resolves the model from `public.model_catalog` using `complexity_tier='specialty'`
- **AND** the resolved `(runtime_type, model_id, extra_args)` tuple is passed to the appropriate `RuntimeAdapter` via the standard adapter registry
- **AND** the default seed entry is `discretion-qwen3.5-9b` (runtime_type=`opencode`, model_id=`ollama/qwen3.5:9b`) — a local Ollama model accessed via the OpenCode adapter
- **AND** operators can change the discretion model at any time via the Settings UI at `/butlers/settings` without code changes or restarts

#### Scenario: Discretion dispatcher concurrency
- **WHEN** a `DiscretionDispatcher` is instantiated
- **THEN** it maintains its own `asyncio.Semaphore` (default 4 concurrent calls), independent from any butler's spawner semaphore
- **AND** the concurrency limit is configurable per dispatcher instance

#### Scenario: Discretion configuration
- **WHEN** a connector uses the discretion layer
- **THEN** the `DiscretionEvaluator` accepts configuration for: `window_size` (default: 10), `window_seconds` (default: 300), `weight_bypass` (default: 1.0), `weight_fail_open` (default: 0.5), `group_size_bypass_max` (default: `None`, disabled), and `system_prompt`
- **AND** model selection is handled entirely by the model catalog

#### Scenario: Fail-open by default
- **WHEN** the discretion LLM call fails (timeout, connection error, malformed response)
- **THEN** the default behaviour depends on the sender's weight: weight >= `weight_fail_open` threshold → FORWARD (fail-open), weight < threshold → IGNORE (fail-closed)

#### Scenario: Primary personal channel fails open on discretion infra failure
- **WHEN** a primary personal 1:1 messaging channel (e.g. WhatsApp) configures `weight_fail_open` at or below the `unknown` tier weight (0.3) — every sender resolves to at least `unknown`, so every sender fails open
- **THEN** when the discretion LLM cannot render a verdict (e.g. same-tier failover exhaustion, timeout, provider error) the message FORWARDs rather than being silently dropped, so the owner's private-chat messages are not lost while the discretion model is degraded
- **AND** a genuine model-judged IGNORE still drops (fail-open governs only the error path, never the LLM's own verdict)
- **AND** because no sender on that channel is below the threshold, the failover-exhausted suppression ledger row (below) is not produced for it — the message is forwarded, not suppressed

#### Scenario: Per-channel discretion-drop visibility
- **WHEN** a connector records a discretion IGNORE to `connectors.filtered_events`
- **THEN** it increments a low-cardinality `discretion_ignore_total{channel, kind}` Prometheus counter, where `kind` is the `classify_ignore_kind` result (`llm_verdict` for a genuine model-judged noise drop vs a fail-closed default such as `failover_exhausted`)
- **AND** this makes per-channel over-filtering — and specifically the split between genuine noise and fabricated infra drops — observable per channel without re-sampling private payloads or a schema change

#### Scenario: Discretion evaluator logs carry identifiers, not content
- **WHEN** the `DiscretionEvaluator` logs an evaluation (input, result, timeout, error, unparseable response, contact-weight resolution or ledger-write failure)
- **THEN** the log record carries only identifiers: source, channel, weight, prompt and response lengths, short keyed sha256 prefixes, latency, the exception class, the dispatcher's error-code token and the raise site
- **AND** it SHALL NOT carry prompt or message text, raw model output, exception messages, tracebacks (`exc_info`) or channel values such as phone numbers or handles

