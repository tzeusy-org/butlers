## ADDED Requirements

### Requirement: Degenerate Session Guardrails
The spawner SHALL evaluate a session for degenerate behavior after the runtime invocation returns and the tool-call records have been merged, and SHALL terminate the session (by raising `RuntimeError`) when any guardrail is exceeded. Detection is runtime-agnostic: the checks consume the same merged tool-call list and token usage the spawner already reads for session logging, so every registered adapter (`claude`, `codex`, `gemini`, `opencode`) is covered uniformly.

Guardrails are evaluated as **post-session checks**, not in-flight cancellation. The runtime subprocess has already exited by the time the checks run; the guardrail decides whether the completed session counts as a success or a typed failure. The checks run in a fixed order and the first to trip wins: degenerate loop → tool-call budget → token budget.

Three independent budgets SHALL be enforced, each OR-combined with the others:

1. **Consecutive-identical-call count** (`_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD`, default `6`): The maximum run of back-to-back tool calls sharing one `(name, input_fingerprint)` signature. Only *adjacent* duplicates count; any non-identical call resets the streak. The signature uses the call's `input_fingerprint` when present, otherwise a canonical fingerprint of the call's `input`/`args`/`arguments`/`parameters` payload.
2. **Cumulative tool-call count** (`max_tool_calls`, default `_DEFAULT_MAX_TOOL_CALLS = 0`): Total tool calls observed in the merged session list. A value of `0` disables the check (the shipped default leaves it off).
3. **Cumulative input tokens** (`max_token_budget`, default `None`): Sum of `input_tokens` reported by the adapter for the session. `None` disables the check.

These thresholds are spawner-level parameters / module constants, not `RuntimeConfigAccessor` HOT fields and not `RuntimeSeedConfig` columns. The defaults above are the shipped values; callers MAY pass overrides through `trigger()` / the internal invoke path.

#### Scenario: Consecutive identical tool calls trip the loop detector
- **WHEN** a completed session's merged tool-call list contains `_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD` or more consecutive calls sharing one `(name, input_fingerprint)` signature
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `degenerate_tool_loop:` and names the looping tool
- **AND** the session SHALL be recorded with `success=False` and the guardrail message in the session `error` column
- **AND** the tool calls that ran SHALL be preserved on the failed session record

#### Scenario: Non-identical intervening call resets the streak
- **WHEN** a session issues identical calls `A, A, A`, then a different call `B`, then `A` again
- **THEN** the consecutive-identical streak SHALL reset at `B`
- **AND** the session SHALL NOT be flagged as a degenerate loop unless a single uninterrupted run of identical calls reaches the threshold

#### Scenario: Tool-call budget exceeded
- **WHEN** the cumulative tool-call count for a session exceeds a configured non-zero `max_tool_calls`
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `tool_call_budget_exceeded:`
- **AND** the session SHALL be recorded with `success=False`

#### Scenario: Tool-call budget disabled by default
- **WHEN** `max_tool_calls` is `0` (the shipped default `_DEFAULT_MAX_TOOL_CALLS`)
- **THEN** the tool-call budget check SHALL be skipped regardless of how many tool calls the session made

#### Scenario: Input-token budget exceeded
- **WHEN** the session's reported cumulative `input_tokens` exceeds a configured `max_token_budget`
- **THEN** the spawner SHALL raise `RuntimeError` whose message begins with `token_budget_exceeded:`
- **AND** the session SHALL be recorded with `success=False`

#### Scenario: Token budget disabled when unset or usage unknown
- **WHEN** `max_token_budget` is `None`, or the adapter reported no `input_tokens` for the session
- **THEN** the token-budget check SHALL be skipped

#### Scenario: Guardrail termination suppresses same-tier failover
- **WHEN** a guardrail raises `RuntimeError` from the post-invocation success path
- **THEN** the failover classifier SHALL recognise the guardrail marker substring in the exception message (`degenerate_tool_loop`, `tool_call_budget_exceeded`, `token_budget_exceeded`)
- **AND** SHALL classify the failure as not failover-eligible so no automatic retry is attempted
- **AND** the suppressed-failover metric SHALL be recorded for observability
