# Tool Call Scorecard

## Purpose
Defines the tool-call accuracy evaluation, per-model scoring, per-butler breakdown, and detail capture contract for cross-model benchmark tool-call analysis.

## Requirements

### Requirement: Tool-call accuracy evaluation
For each scenario with `expected_tool_calls`, the harness SHALL retrieve captured tool calls via `consume_runtime_session_tool_calls(session_id)` after the session completes and compare against the expected set using set containment (expected is a subset of actual).

#### Scenario: All expected tools called
- **WHEN** a scenario expects `["calendar_create"]` and the session called `["state_get", "calendar_create", "state_set"]`
- **THEN** the result is recorded as a pass (expected is a subset of actual)

#### Scenario: Missing expected tool call
- **WHEN** a scenario expects `["calendar_create", "notify"]` but the session only called `["calendar_create"]`
- **THEN** the result is recorded as a fail with `notify` listed as missing

#### Scenario: Session timeout before tool calls
- **WHEN** a scenario's session exceeds `timeout_seconds` before completing
- **THEN** the result is recorded as a timeout with whatever tool calls were captured (if any)

### Requirement: Per-model tool-call accuracy score
The harness SHALL compute a tool-call accuracy percentage for each model: `(scenarios_with_all_expected_tools / total_tool_call_scenarios) * 100`.

#### Scenario: Accuracy calculation
- **WHEN** model A successfully calls all expected tools in 15 of 20 tool-call scenarios
- **THEN** the tool-call accuracy for model A is recorded as 75.0%

### Requirement: Per-butler tool-call breakdown
The harness SHALL break down tool-call accuracy by target butler.

#### Scenario: Butler breakdown
- **WHEN** model A has 8 `health` tool scenarios (7 correct) and 6 `relationship` tool scenarios (5 correct)
- **THEN** the breakdown shows health: 87.5%, relationship: 83.3%

### Requirement: Tool-call detail capture
Each result SHALL include the full list of actual tool calls made, not just pass/fail, to support debugging and analysis.

#### Scenario: Detail in results
- **WHEN** a scenario completes with tool calls `["state_get", "log_meal", "state_set"]`
- **THEN** the raw result includes all three tool names, their modules, inputs, and outcomes
