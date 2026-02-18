# LLM CLI Spawner (Delta)

Delta spec for the `llm-cli-spawner` capability. Updates session logging to capture token usage and model information from the Claude Code SDK response.

---

## MODIFIED Requirements

### Requirement: Session logging

The LLM CLI Spawner SHALL log every runtime invocation to the butler's `sessions` table, regardless of whether the invocation succeeded or failed. The session record MUST include the prompt, recorded tool calls, output, success status, error details (if any), duration, and trace ID. The session record MUST also include `input_tokens`, `output_tokens`, and `model` extracted from the Claude Code SDK response.

#### Scenario: Successful session is logged

WHEN a runtime instance completes successfully with output "Done. 3 tasks checked."
THEN a session record SHALL be inserted into the `sessions` table
AND the record's `success` field SHALL be `true`
AND the record's `output` field SHALL contain "Done. 3 tasks checked."
AND the record's `error` field SHALL be `null`

#### Scenario: Failed session is logged

WHEN a runtime instance fails with error "SDK timeout after 300s"
THEN a session record SHALL be inserted into the `sessions` table
AND the record's `success` field SHALL be `false`
AND the record's `error` field SHALL contain "SDK timeout after 300s"

#### Scenario: Session records duration

WHEN a runtime instance runs for 12.5 seconds
THEN the session record's duration SHALL reflect approximately 12.5 seconds

#### Scenario: Session records tool calls

WHEN a runtime instance calls `state_get(key="tasks")` and `state_set(key="last_check", value="2026-02-09")`
THEN the session record's `tool_calls` list SHALL contain both tool call records

#### Scenario: Successful session captures token usage from SDK response

- **WHEN** a runtime instance completes successfully and the SDK response contains `input_tokens: 1500`, `output_tokens: 800`, and `model: "claude-sonnet-4-20250514"`
- **THEN** the session record MUST have `input_tokens` set to `1500`, `output_tokens` set to `800`, and `model` set to `"claude-sonnet-4-20250514"`

#### Scenario: Failed session still captures partial token data if available

- **WHEN** a runtime instance fails with an error but the SDK response includes partial token data (e.g., `input_tokens: 500` but no `output_tokens`)
- **THEN** the session record MUST have `input_tokens` set to `500`
- **AND** `output_tokens` MUST be NULL
- **AND** `model` MUST be set to the model ID if available in the SDK response, otherwise NULL
- **AND** `success` MUST be `false` and `error` MUST contain the error message

#### Scenario: Model field records the model ID used

- **WHEN** a runtime instance is spawned and the SDK response indicates the model used was `"claude-sonnet-4-20250514"`
- **THEN** the session record's `model` field MUST be set to `"claude-sonnet-4-20250514"`
- **AND** the value MUST be the exact model identifier string returned by the SDK, not a normalized or shortened form
