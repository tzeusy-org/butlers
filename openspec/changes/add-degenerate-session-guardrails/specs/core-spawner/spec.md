## ADDED Requirements

### Requirement: Degenerate Loop Detection
The spawner SHALL monitor in-flight runtime sessions for degenerate behavior and terminate sessions that exceed any configured guardrail. Detection is runtime-agnostic: the spawner consumes the same tool-call event stream and usage deltas that it already reads for session logging, so every registered adapter (`claude`, `codex`, `gemini`, `opencode`) benefits uniformly.

Three independent budgets SHALL be enforced, each OR-combined with the others:

1. **Repeated-identical-call count** (`degenerate_loop_max_repeats`): Consecutive identical tool calls, where identity is defined as the tuple `(tool_name, canonical_json(args))`. `canonical_json` SHALL sort object keys and normalize `null`/`None`. A non-identical tool call between two identical ones resets the repeat counter.
2. **Cumulative tool-call count** (`max_tool_calls_per_session`): Total tool calls observed in the session, regardless of identity.
3. **Cumulative input tokens** (`max_input_tokens_per_session`): Sum of `input_tokens` reported in adapter usage events across the session. Cache-read input tokens SHALL be included in this sum; the loop-driven token growth mode is driven by replayed conversation context.

All three thresholds SHALL be read from `RuntimeConfigAccessor.get()` per-spawn (HOT fields), so operators can tune them per butler via the dashboard without restarting the daemon. Defaults are provided but are not part of the binding contract — the contract is that the values are accessor-driven and enforced.

#### Scenario: Repeated identical tool call trips the loop detector
- **WHEN** a runtime session issues the same `(tool_name, canonical_json(args))` tuple for `degenerate_loop_max_repeats` consecutive calls
- **THEN** the spawner SHALL cancel the in-flight `runtime.invoke()` task to terminate the subprocess
- **AND** call `session_complete(success=False, error="degenerate_tool_loop", ...)`
- **AND** emit a telemetry event tagged with the terminating reason
- **AND** the self-healing dispatcher SHALL fire as for any other failure

#### Scenario: Non-identical intervening call resets the repeat counter
- **WHEN** a session issues identical calls `A, A, A` and then a different call `B`
- **AND** subsequently issues `A` again
- **THEN** the repeat counter for `A` SHALL be 1 after the second `A` burst starts
- **AND** the session SHALL NOT terminate unless the new burst itself reaches `degenerate_loop_max_repeats`

#### Scenario: Tool-call count cap exceeded
- **WHEN** the cumulative tool-call count for a session reaches `max_tool_calls_per_session`
- **THEN** the spawner SHALL cancel the in-flight `runtime.invoke()` task
- **AND** call `session_complete(success=False, error="tool_call_budget_exceeded", ...)`
- **AND** emit a telemetry event tagged with the terminating reason

#### Scenario: Input-token budget exceeded
- **WHEN** the cumulative `input_tokens` for a session (including cache-read tokens) reaches `max_input_tokens_per_session`
- **THEN** the spawner SHALL cancel the in-flight `runtime.invoke()` task
- **AND** call `session_complete(success=False, error="token_budget_exceeded", ...)`
- **AND** emit a telemetry event tagged with the terminating reason

#### Scenario: Thresholds sourced from accessor per-spawn
- **WHEN** `trigger()` is called
- **THEN** the spawner SHALL read `degenerate_loop_max_repeats`, `max_tool_calls_per_session`, and `max_input_tokens_per_session` from `RuntimeConfigAccessor.get()` at spawn time
- **AND** operators SHALL be able to change these values via the dashboard PATCH endpoint, with the change taking effect on new sessions within the accessor's TTL (30 s)

#### Scenario: Healing sessions exempt from degenerate-loop detection
- **WHEN** `trigger(prompt, trigger_source="healing")` is called
- **THEN** the degenerate loop detector SHALL NOT be enabled for that session
- **AND** the tool-call and token budgets SHALL NOT be enforced
- **AND** healing sessions fall back to the session timeout as their only safety net

#### Scenario: No retry on guardrail termination
- **WHEN** a session is terminated because a guardrail tripped
- **THEN** the spawner SHALL NOT automatically retry the invocation
- **AND** the `SpawnerResult` SHALL report `success=False` with the matching `error` string

#### Scenario: Guardrail-terminated session logs the triggering tool call
- **WHEN** a session is terminated with `error="degenerate_tool_loop"`
- **THEN** the session record SHALL retain enough metadata to identify the looping tool (tool name at minimum)
- **AND** the terminating tool call SHALL be recorded as the final entry in the session's tool-call log
