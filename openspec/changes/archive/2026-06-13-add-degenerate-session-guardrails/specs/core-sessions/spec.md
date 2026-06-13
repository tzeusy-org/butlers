## ADDED Requirements

### Requirement: Guardrail Termination Error Markers
Sessions terminated by a spawner guardrail SHALL be recorded with `success=False` and a session `error` string whose leading marker token is drawn from a stable taxonomy, so the failover classifier, dashboard, alerting, and runbooks can discriminate guardrail terminations from genuine crashes and timeouts.

The guardrail raises a `RuntimeError` whose message *begins with* the marker token; the spawner records the failed session with `error = "<ExceptionType>: <marker>: <human-readable detail>"`. Consumers SHALL match on the marker substring rather than on an exact-equal `error` value, because the stored string is the full exception text, not a bare enum value. No schema change is required — the markers live in the existing free-form `error` column.

The guardrail marker taxonomy SHALL include at least:

| Marker token | Cause |
|---|---|
| `degenerate_tool_loop` | The post-session loop check found a run of consecutive identical `(name, input_fingerprint)` tool calls at or above the configured threshold. |
| `tool_call_budget_exceeded` | The cumulative tool-call count exceeded the configured `max_tool_calls`. |
| `token_budget_exceeded` | The cumulative reported `input_tokens` exceeded the configured `max_token_budget`. |

Other (non-guardrail) failures continue to record free-form `error` strings (adapter crashes, timeouts, quota exhaustion, etc.). Consumers SHALL treat any `error` string lacking a recognised guardrail marker as a generic failure.

#### Scenario: Degenerate loop termination records the marker
- **WHEN** the spawner terminates a session via the degenerate-loop guardrail
- **THEN** the session is recorded with `success=False`
- **AND** the `error` column contains the `degenerate_tool_loop` marker substring

#### Scenario: Tool-call budget termination records the marker
- **WHEN** the spawner terminates a session because the tool-call budget was exceeded
- **THEN** the session is recorded with `success=False`
- **AND** the `error` column contains the `tool_call_budget_exceeded` marker substring

#### Scenario: Token budget termination records the marker
- **WHEN** the spawner terminates a session because the input-token budget was exceeded
- **THEN** the session is recorded with `success=False`
- **AND** the `error` column contains the `token_budget_exceeded` marker substring

#### Scenario: Failover classifier matches markers as substrings
- **WHEN** a guardrail `RuntimeError` reaches the failover classifier
- **THEN** the classifier SHALL match the marker as a lowercased substring of the exception message
- **AND** SHALL suppress same-tier failover for guardrail terminations

#### Scenario: Unrecognised error string is treated as a generic failure
- **WHEN** a consumer (dashboard, alerting, runbook) reads a session `error` string that contains no recognised guardrail marker
- **THEN** the consumer SHALL treat it as a generic failure without erroring
- **AND** SHALL surface the raw string for diagnostics
