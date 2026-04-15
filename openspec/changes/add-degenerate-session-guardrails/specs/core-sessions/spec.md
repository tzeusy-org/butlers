## ADDED Requirements

### Requirement: Typed Session Termination Error Taxonomy
Sessions completed with `success=False` SHALL record a typed `error` string drawn from a published taxonomy. The taxonomy values are string literals in the existing `sessions.error` column — no schema change is required to accept them, but the values SHALL be stable so the dashboard, alerting, and runbooks can treat them as enum values.

The taxonomy SHALL include at least the following values, with their semantics:

| Error value | Cause |
|---|---|
| `degenerate_tool_loop` | The spawner's loop detector tripped: the session issued the same `(tool_name, canonical_args)` tuple for more than the configured consecutive-repeat threshold. |
| `tool_call_budget_exceeded` | The spawner's cumulative tool-call counter exceeded the configured per-session cap. |
| `token_budget_exceeded` | The spawner's cumulative `input_tokens` counter exceeded the configured per-session cap. |
| `timeout` | The runtime adapter's wall-clock timeout fired and the subprocess was terminated. |
| `crash` | The runtime subprocess exited non-zero or raised an unhandled exception from within the adapter. |

Additional taxonomy values MAY be added by future changes. Consumers (dashboards, alerts, runbooks) SHALL treat any unrecognized value as `crash`-equivalent until the taxonomy is updated.

#### Scenario: Degenerate loop termination records typed error
- **WHEN** a session is terminated by the spawner's degenerate-loop detector
- **THEN** `session_complete` SHALL be called with `success=False` and `error="degenerate_tool_loop"`
- **AND** the `sessions.error` column SHALL store exactly that string

#### Scenario: Tool-call budget termination records typed error
- **WHEN** a session is terminated because `max_tool_calls_per_session` was reached
- **THEN** `session_complete` SHALL be called with `success=False` and `error="tool_call_budget_exceeded"`

#### Scenario: Token budget termination records typed error
- **WHEN** a session is terminated because `max_input_tokens_per_session` was reached
- **THEN** `session_complete` SHALL be called with `success=False` and `error="token_budget_exceeded"`

#### Scenario: Dashboard renders typed error values
- **WHEN** the dashboard session list or detail view renders a failed session
- **AND** the session's `error` column matches one of the taxonomy values
- **THEN** the UI SHALL render a human-readable label for the value
- **AND** SHALL allow filtering the session list by `error` value

#### Scenario: Unrecognized error string falls back gracefully
- **WHEN** a consumer (dashboard, alerting) reads an `error` value not in the taxonomy
- **THEN** the consumer SHALL treat it as a generic failure without erroring
- **AND** SHALL surface the raw string for diagnostic purposes
