## MODIFIED Requirements

### Requirement: Model pinning via catalog overrides
The harness SHALL pin a single model for every model-resolution key by inserting a catalog entry into `public.model_catalog` and upserting `public.butler_model_overrides` with `priority=999` for every roster butler and for every goal-invocation role (`lead`, `child` and `monitor`), because goal invocations resolve their model by role rather than by butler name.

Source: RFC 0038 D16
Scope: v1-mandatory

#### Scenario: Model pinned for benchmark run
- **WHEN** a benchmark run starts with model `claude-sonnet-4-5-20250514`
- **THEN** a catalog entry is inserted for that model, and `butler_model_overrides` rows are created for all roster butlers and for the roles `lead`, `child` and `monitor` with `priority=999`, causing `resolve_model()` to return the pinned model for every resolution key

#### Scenario: Model override cleanup after run
- **WHEN** a benchmark run completes (success or failure)
- **THEN** all `butler_model_overrides` rows with `priority=999` are deleted and the test catalog entry is removed

#### Scenario: Crash-safe cleanup
- **WHEN** a benchmark run crashes mid-flight leaving override rows
- **THEN** override rows are tagged with `source='e2e-benchmark'` for manual identification and cleanup

### Requirement: In-memory accumulator as intermediary data layer
The harness SHALL accumulate every benchmark scenario result into a single session-scoped in-memory store (the `benchmark_result` accumulator). This in-memory accumulator is the canonical intermediary between test execution and report generation; the harness MUST NOT depend on pytest's `record_property` fixture, JUnit XML, or `pytest_terminal_summary` for benchmark reporting. The harness SHALL NOT record a routing expected/actual/pass metric, because message classification and route dispatch no longer exist.

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Metrics recorded into the in-memory accumulator
- **WHEN** a benchmark scenario runs in benchmark mode
- **THEN** its per-scenario metrics (scenario id, tool-call expected/actual/pass, input/output tokens, duration) are appended to the session-scoped `benchmark_result` accumulator, keyed by model

#### Scenario: Markdown scorecards generated at session end
- **WHEN** a benchmark run completes (success or failure) in benchmark mode
- **THEN** the `pytest_sessionfinish` hook computes per-model scorecards from the in-memory accumulator and writes Markdown scorecards plus a machine-readable `raw-results.json` to a timestamped directory under `.tmp/e2e-scorecards/<timestamp>/` (full output structure defined in the scorecard-reporting capability)

#### Scenario: Cross-model comparison from generated Markdown
- **WHEN** a benchmark run produces scorecards for models A, B, and C
- **THEN** the cross-model `summary.md` and each model's `raw-results.json` provide the comparison data directly, with no re-run of the benchmarks and no parsing of stdout or JUnit XML

#### Scenario: Output directory path surfaced to the terminal
- **WHEN** scorecard generation completes
- **THEN** the harness prints the output directory path to stdout so the human-readable Markdown report can be located, without relying on a `pytest_terminal_summary` hook
