# Model Benchmark Harness

## Purpose
Defines the model pinning, configurable model list, sequential iteration, and dual execution mode contract for cross-model benchmarking of the butler ecosystem.

## Requirements

### Requirement: Model pinning via catalog overrides
The harness SHALL pin a single model for all butlers (including switchboard) by inserting a catalog entry into `public.model_catalog` and upserting `public.butler_model_overrides` for every butler with `priority=999`.

#### Scenario: Model pinned for benchmark run
- **WHEN** a benchmark run starts with model `claude-sonnet-4-5-20250514`
- **THEN** a catalog entry is inserted for that model, and `butler_model_overrides` rows are created for all roster butlers with `priority=999`, causing `resolve_model()` to return the pinned model for every butler

#### Scenario: Model override cleanup after run
- **WHEN** a benchmark run completes (success or failure)
- **THEN** all `butler_model_overrides` rows with `priority=999` are deleted and the test catalog entry is removed

#### Scenario: Crash-safe cleanup
- **WHEN** a benchmark run crashes mid-flight leaving override rows
- **THEN** override rows are tagged with `source='e2e-benchmark'` for manual identification and cleanup

### Requirement: Configurable model list
The harness SHALL accept a list of models to benchmark, configurable via environment variable or pytest CLI option.

#### Scenario: Models specified via environment variable
- **WHEN** `E2E_BENCHMARK_MODELS` is set to `claude-sonnet-4-5-20250514,gpt-4o,gemini-2.0-flash`
- **THEN** the harness iterates over those three models, pinning each in sequence

#### Scenario: Models specified via CLI
- **WHEN** `--benchmark-models=claude-sonnet-4-5-20250514,gpt-4o` is passed to pytest
- **THEN** the harness uses those models, overriding any environment variable

#### Scenario: No models specified in benchmark mode
- **WHEN** benchmark mode is activated but no model list is provided
- **THEN** the harness fails with a clear error message listing the configuration options

### Requirement: Sequential model iteration
The harness SHALL run the full scenario corpus for one model before moving to the next. It MUST NOT interleave scenarios across models.

#### Scenario: Three-model benchmark run
- **WHEN** models `[A, B, C]` are configured with 10 scenarios each
- **THEN** all 10 scenarios run with model A pinned, then all 10 with model B, then all 10 with model C, for 30 total executions, no interleaving

### Requirement: Two execution modes
The suite SHALL support `validate` mode (default, pass/fail assertions) and `benchmark` mode (scorecard generation, no hard failures).

#### Scenario: Validate mode
- **WHEN** tests run without `--benchmark` flag
- **THEN** scenarios execute with the currently configured model, using standard pytest assertions that fail on mismatch

#### Scenario: Benchmark mode
- **WHEN** tests run with `--benchmark` flag
- **THEN** scenarios execute for each model in the list, accumulating results without hard assertion failures, and scorecards are generated at the end

### Requirement: In-memory accumulator as intermediary data layer
The harness SHALL accumulate every benchmark scenario result into a single session-scoped in-memory store (the `benchmark_result` accumulator). This in-memory accumulator is the canonical intermediary between test execution and report generation; the harness MUST NOT depend on pytest's `record_property` fixture, JUnit XML, or `pytest_terminal_summary` for benchmark reporting.

#### Scenario: Metrics recorded into the in-memory accumulator
- **WHEN** a benchmark scenario runs in benchmark mode
- **THEN** its per-scenario metrics (scenario id, routing expected/actual/pass, tool-call expected/actual/pass, input/output tokens, duration) are appended to the session-scoped `benchmark_result` accumulator, keyed by model

#### Scenario: Markdown scorecards generated at session end
- **WHEN** a benchmark run completes (success or failure) in benchmark mode
- **THEN** the `pytest_sessionfinish` hook computes per-model scorecards from the in-memory accumulator and writes Markdown scorecards plus a machine-readable `raw-results.json` to a timestamped directory under `.tmp/e2e-scorecards/<timestamp>/` (full output structure defined in the scorecard-reporting capability)

#### Scenario: Cross-model comparison from generated Markdown
- **WHEN** a benchmark run produces scorecards for models A, B, and C
- **THEN** the cross-model `summary.md` and each model's `raw-results.json` provide the comparison data directly, with no re-run of the benchmarks and no parsing of stdout or JUnit XML

#### Scenario: Output directory path surfaced to the terminal
- **WHEN** scorecard generation completes
- **THEN** the harness prints the output directory path to stdout so the human-readable Markdown report can be located, without relying on a `pytest_terminal_summary` hook
