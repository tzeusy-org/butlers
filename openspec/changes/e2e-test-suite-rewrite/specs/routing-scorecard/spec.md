## ADDED Requirements

### Requirement: Routing accuracy evaluation
For each scenario with an `expected_routing` value, the harness SHALL compare the `triage_target` returned by `ingest_v1()` against the expected butler name and record a pass/fail result.

#### Scenario: Correct routing
- **WHEN** a scenario expects routing to `health` and `ingest_v1()` returns `triage_target="health"`
- **THEN** the result is recorded as a pass for that scenario/model combination

#### Scenario: Incorrect routing
- **WHEN** a scenario expects routing to `health` but `ingest_v1()` returns `triage_target="general"`
- **THEN** the result is recorded as a fail with both expected and actual values captured

#### Scenario: No routing decision
- **WHEN** a scenario expects routing to `health` but `ingest_v1()` returns `triage_target=None` (pass_through to LLM classification)
- **THEN** the result is recorded as a fail indicating no routing decision was made at the policy level

### Requirement: Per-model routing accuracy score
The harness SHALL compute an accuracy percentage for each model: `(correct_routes / total_routing_scenarios) * 100`.

#### Scenario: Accuracy calculation
- **WHEN** model A correctly routes 18 of 20 routing scenarios
- **THEN** the routing accuracy for model A is recorded as 90.0%

### Requirement: Per-category routing breakdown
The harness SHALL break down routing accuracy by scenario tag (e.g., `email`, `telegram`, `health`, `relationship`).

#### Scenario: Category breakdown
- **WHEN** model A has 10 `email` scenarios (9 correct) and 10 `telegram` scenarios (8 correct)
- **THEN** the breakdown shows email: 90%, telegram: 80%, overall: 85%

### Requirement: Confusion matrix
The harness SHALL produce a confusion matrix showing which butlers were predicted vs. expected for misrouted scenarios.

#### Scenario: Confusion data captured
- **WHEN** 3 scenarios expected `health` but were routed to `general`
- **THEN** the confusion matrix shows `health→general: 3` as a misroute pattern
