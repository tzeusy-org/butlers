## MODIFIED Requirements

### Requirement: Scorecard output directory structure
After a benchmark run, the harness SHALL write scorecards to `.tmp/e2e-scorecards/<timestamp>/` with per-model subdirectories and a cross-model summary, and SHALL NOT write a routing scorecard.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Directory structure created
- **WHEN** a benchmark run completes for models `[sonnet, haiku]`
- **THEN** the output directory contains:
  - `sonnet/tool-call-scorecard.md`
  - `sonnet/cost-summary.md`
  - `sonnet/raw-results.json`
  - `haiku/tool-call-scorecard.md`
  - `haiku/cost-summary.md`
  - `haiku/raw-results.json`
  - `summary.md`

### Requirement: Cross-model summary table
The `summary.md` file SHALL contain a comparison table with one row per model and columns for tool-call accuracy, total cost, and total tokens.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Summary table generated
- **WHEN** models `[sonnet, haiku]` complete all scenarios
- **THEN** `summary.md` contains a markdown table like:
  | Model | Tool-Call Accuracy | Input Tokens | Output Tokens | Est. Cost |
  with one row per model, sorted by tool-call accuracy descending

### Requirement: Machine-readable results
Each model's `raw-results.json` SHALL contain the full result set with a versioned schema and no routing fields.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: JSON schema versioned
- **WHEN** results are written
- **THEN** `raw-results.json` includes a `schema_version` field (e.g., `"1.0"`) at the top level

#### Scenario: JSON contains per-scenario results
- **WHEN** results are written for 20 scenarios
- **THEN** `raw-results.json` contains an array of 20 entries, each with: `scenario_id`, `tool_calls_expected`, `tool_calls_actual`, `tool_calls_pass`, `input_tokens`, `output_tokens`, `duration_ms`
