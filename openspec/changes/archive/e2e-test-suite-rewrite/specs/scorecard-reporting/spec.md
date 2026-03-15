## ADDED Requirements

### Requirement: Scorecard output directory structure
After a benchmark run, the harness SHALL write scorecards to `.tmp/e2e-scorecards/<timestamp>/` with per-model subdirectories and a cross-model summary.

#### Scenario: Directory structure created
- **WHEN** a benchmark run completes for models `[sonnet, haiku]`
- **THEN** the output directory contains:
  - `sonnet/routing-scorecard.md`
  - `sonnet/tool-call-scorecard.md`
  - `sonnet/cost-summary.md`
  - `sonnet/raw-results.json`
  - `haiku/routing-scorecard.md`
  - `haiku/tool-call-scorecard.md`
  - `haiku/cost-summary.md`
  - `haiku/raw-results.json`
  - `summary.md`

### Requirement: Cross-model summary table
The `summary.md` file SHALL contain a comparison table with one row per model and columns for routing accuracy, tool-call accuracy, total cost, and total tokens.

#### Scenario: Summary table generated
- **WHEN** models `[sonnet, haiku]` complete all scenarios
- **THEN** `summary.md` contains a markdown table like:
  | Model | Routing Accuracy | Tool-Call Accuracy | Input Tokens | Output Tokens | Est. Cost |
  with one row per model, sorted by routing accuracy descending

### Requirement: Machine-readable results
Each model's `raw-results.json` SHALL contain the full result set with a versioned schema.

#### Scenario: JSON schema versioned
- **WHEN** results are written
- **THEN** `raw-results.json` includes a `schema_version` field (e.g., `"1.0"`) at the top level

#### Scenario: JSON contains per-scenario results
- **WHEN** results are written for 20 scenarios
- **THEN** `raw-results.json` contains an array of 20 entries, each with: `scenario_id`, `routing_expected`, `routing_actual`, `routing_pass`, `tool_calls_expected`, `tool_calls_actual`, `tool_calls_pass`, `input_tokens`, `output_tokens`, `duration_ms`

### Requirement: Cost summary per model
Each model's `cost-summary.md` SHALL show total and per-scenario token usage with estimated cost.

#### Scenario: Cost breakdown
- **WHEN** model `sonnet` runs 10 scenarios consuming 50k input tokens and 20k output tokens total
- **THEN** `cost-summary.md` shows total tokens, estimated cost (using configurable per-model pricing), and a per-scenario breakdown table

### Requirement: Scorecard path printed to terminal
After scorecard generation, the harness SHALL print the output directory path to stdout.

#### Scenario: Path printed
- **WHEN** scorecards are written to `.tmp/e2e-scorecards/20260312-143000/`
- **THEN** the terminal shows the full path so the user can navigate to it
