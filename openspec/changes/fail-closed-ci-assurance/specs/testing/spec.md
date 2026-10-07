# CI assurance contract

The complete bu-ly3lv5.1 outcome is retained. M1/M2 are protected-landed and M3 is source-released. One actual M2 merge-group report/check observation is retained; every unfinished second coverage observation, hosted timeout control and M4 observer task remains mandatory and unchecked.

## MODIFIED Requirements

### Requirement: Test Framework Configuration
The project SHALL use pytest with pytest-asyncio as the test runner. Configuration SHALL live in `pyproject.toml` under `[tool.pytest.ini_options]`.

ID: REQ-testing-033
Source: bu-ly3lv5.1 timeout slice; pytest configuration; testing-and-verification
Scope: v1-mandatory

#### Scenario: Async test mode
- **WHEN** pytest runs async test functions
- **THEN** `asyncio_mode = "auto"` is enabled so async tests do not require explicit `@pytest.mark.asyncio` decorators
- **AND** `asyncio_default_fixture_loop_scope = "session"` ensures session-scoped async fixtures share a single event loop

#### Scenario: Test paths
- **WHEN** pytest discovers tests
- **THEN** it searches `testpaths = ["tests", "roster"]`
- **AND** uses `--import-mode=importlib` for module resolution

#### Scenario: Warning filters
- **WHEN** tests run
- **THEN** known deprecation warnings from `websockets`, `uvicorn`, `AsyncMock`, `EmailModule`, and `health server` are filtered to avoid noise

#### Scenario: Python tests have a finite default execution timeout
- **WHEN** pytest executes a test without an explicit justified finite timeout override
- **THEN** the project configuration supplies a 300-second per-test timeout
- **AND** an override remains finite, names its slower legitimate operation, and is supported by timing and a positive control
- **AND** a timeout is a non-passing named result; it does not remove, skip, or relabel the selected test
- **AND** collection, worker startup and session finalization remain bounded by the owning job watchdog because an item timeout alone cannot prove those phases terminate

### Requirement: Smoke Tests Run In CI As A Fast Gate
The smoke tier SHALL execute in CI (`.github/workflows/ci.yml`) on every merge-group tree and every backend-applicable
pull request as a fast gate, distinct from and faster than the integration tier,
and MUST NOT pull in the E2E suite or any real LLM dependency.

ID: REQ-testing-034
Source: bu-ly3lv5.1 preserved smoke outcome; existing bu-r5mnn event policy and testing-and-verification
Scope: v1-mandatory

#### Scenario: Dedicated smoke selection in CI
- **WHEN** the CI `check-preflight` job runs
- **THEN** smoke tests are selected via `-m smoke` (excluding `e2e` and any real-LLM
  paths) and run alongside the independent unit and integration shards
- **AND** a smoke failure fails the CI run

#### Scenario: No E2E or real-LLM dependency in the smoke gate
- **WHEN** the smoke step runs in CI
- **THEN** it does not require `ANTHROPIC_API_KEY` or the `claude` CLI
- **AND** `tests/e2e` is excluded from the smoke selection, consistent with the
  existing E2E CI-exclusion mechanisms

#### Scenario: Existing post-queue and docs-only smoke skips are explicit
- **WHEN** a successful classifier identifies a docs/spec-only pull request, or the workflow is a push to main after protected merge-group validation
- **THEN** check-preflight may skip under the existing event policy
- **AND** this skip cannot authorize a missing classifier verdict, a failed/cancelled preflight, a backend-applicable pull request, or a merge-group omission

## ADDED Requirements

### Requirement: Complete fail-closed CI fan-in

The required check context SHALL execute under always(), validate every supplied needed-job result, reject missing or malformed required evidence, and use an explicit job/event/plan skip policy. Changes and guards SHALL succeed; preflight SHALL be required independently of the heavy-shard consistency state. Frontend remains a separate required context and frontend-e2e remains advisory.

ID: REQ-testing-035
Source: bu-ly3lv5.1 original outcome/AC1-2; engineering-bar; testing-and-verification terminal-gate doctrine; bu-tt97y closed scoped-loop invariant.
Scope: v1-mandatory

#### Scenario: A failed or cancelled prerequisite cannot be hidden
- **WHEN** a declared needed job reports failure, cancelled, an unknown result, or missing/malformed result evidence
- **THEN** check fails and names the needed job
- **AND** this applies to changes, guards, plan when applicable, preflight and every selected test lane, even if all other lanes succeeded

#### Scenario: Scoped success excludes preflight from heavy-shard classification
- **WHEN** an admitted backend PR has a successful scoped planner, successful preflight and successful check-affected with every heavy shard skipped
- **THEN** check succeeds
- **AND** preflight success never makes the heavy shards appear to have run

#### Scenario: Docs-only PR admits the existing complete backend skip
- **WHEN** a pull request has a successful classifier with backend false and successful guards
- **THEN** plan, preflight, every heavy shard and check-affected report skipped and check succeeds
- **AND** missing or malformed classification, partial heavy execution, or failed/cancelled evidence cannot authorize that skip

#### Scenario: A full backend PR requires independent preflight and heavy evidence
- **WHEN** a pull request has a successful classifier with backend true, successful guards and a successful planner with mode full
- **THEN** preflight and every heavy shard report success, check-affected reports skipped and check succeeds
- **AND** a missing or invalid planner mode, partial heavy skip or any unsuccessful required result fails check

#### Scenario: A scoped backend PR preserves the affected-only pairing
- **WHEN** a pull request has a successful classifier with backend true, successful guards and a successful planner with mode scoped
- **THEN** preflight and check-affected report success, every heavy shard reports skipped and check succeeds
- **AND** preflight never contributes to heavy-ran consistency; a heavy success or missing affected success fails check

#### Scenario: Main push admits post-queue skips
- **WHEN** the event is push to refs/heads/main and classification and guards succeed with backend true
- **THEN** plan, preflight, every heavy shard and check-affected report skipped and check succeeds
- **AND** a non-main push or unknown event is not an allowed skip context

#### Scenario: Merge-group requires the complete terminal matrix
- **WHEN** the event is merge_group and classification and guards succeed with backend true
- **THEN** preflight and every heavy shard report success, plan and check-affected report skipped and check succeeds
- **AND** no scoped planner, cached result or unexpected skip can replace the full selected terminal population

#### Scenario: An added needed job defaults to fail closed
- **WHEN** a later workflow adds a needed job
- **THEN** the table-driven consumer reads its result without a separately maintained per-job environment loop
- **AND** success is accepted while skip has no permission until an explicit reviewed policy row exists; failure/cancellation/unknown results fail

### Requirement: CI checker movement preserves enforcement

Lock, lint, format and SQL-safety checks SHALL move to guards with their current command scopes, run as mandatory checks, and feed a failed guards job into required check. Exact-once, budget and smoke/release checks SHALL remain in preflight until independently adopted replacement assurance actually lands. Every moved or removed checker SHALL name its enforcing survivor.

ID: REQ-testing-036
Source: bu-ly3lv5.1 slice2/Assurance gate; ci-preflight-guards move1.
Scope: v1-mandatory

#### Scenario: A deliberate format or Ruff violation fails both required contexts
- **WHEN** an isolated canary introduces a real violation in the current enforcement scope
- **THEN** the moved checker fails guards and check fails from guards' result
- **AND** healthy and scoped PR companions remain eligible; no real secrets/provider/DB action is needed to plant the canary

#### Scenario: Remaining preflight evidence cannot pass advisory
- **WHEN** exact-once ownership, budget, or smoke fails
- **THEN** preflight and check are non-successful
- **AND** smoke retains exact command, SHA, duration, outcome, skipped classes and sanitized artifact controls

### Requirement: Coverage reporting is independent of required verdicts

The required check SHALL contain only verdict evaluation and SHALL neither install coverage dependencies nor await coverage reporting. PR shard runs SHALL execute the same selected tests without --cov or coverage uploads. Merge-group shard runs SHALL retain independent coverage files and retry-safe artifacts; a separate non-required coverage job SHALL combine the complete same-run ten-shard population, retain its report and perform the existing badge update. Missing, empty, mismatched or corrupt coverage evidence SHALL fail reporting rather than create a misleading partial report.

ID: REQ-testing-037
Source: bu-ly3lv5.1 folded deep-seven-minute-plan move1 and measure-ci-critical-path move1; retained whole coverage fan-in obligation in bu-gvut0.5.
Scope: v1-mandatory

#### Scenario: PR assurance survives without coverage instrumentation
- **WHEN** PR shard or affected tests run
- **THEN** markers, paths, workers, JUnit and sanitization remain unchanged, while coverage arguments/uploads are absent
- **AND** coverage environment opt-in is exact and deliberate; malformed supplied configuration fails, with no ambient accidental PR instrumentation

#### Scenario: Complete terminal coverage survives in a non-required job
- **WHEN** all merge_group shards finish successfully on the same exact tree
- **THEN** coverage downloads all ten distinct non-empty shard artifacts, combines them, uploads the existing combined report and updates the badge
- **AND** coverage is not a prerequisite or required status of check/guards/frontend
- **AND** every input is read and validated individually before combination; combine warnings or a nine-of-ten partial success cannot publish a report or badge
- **AND** each input is bound to the actual checkout, workflow run and attempt, declared lane/shard and current selected manifest by producer metadata and a raw-file digest
- **AND** the exact ten-file population has readable CoverageData, the complete current measured source population and compatible tracing; extra, stale or mixed evidence fails before report/badge outputs

#### Scenario: Reporting failure remains visible without falsifying gate evidence
- **WHEN** a planted shard coverage artifact is missing/empty/corrupt or the reporting step fails
- **THEN** coverage is non-successful and does not publish a partial success report/badge
- **AND** check still depends on real selected-test/preflight/guard outcomes, rather than declaring those tests failed or granting a coverage threshold exception

### Requirement: CI hangs are finite and diagnosable

Every workflow job and test execution SHALL have a finite positive timeout. Routine job values SHALL derive from named stable p95 samples with explicit setup/retry headroom, including the complete declared UV and browser installer recovery intervals unless their first-attempt allowances are already explicitly included; missing healthy data SHALL be labelled provisional and remain an unresolved calibration obligation. A deliberately sleeping test SHALL fail with its actual name before the owning job is cancelled. Existing broader worker/session watchdogs and their nonzero/UNKNOWN semantics SHALL survive. Browser installation SHALL use bounded retries and a version/OS/lock-bound browser cache while installing required OS dependencies on cache hits as well as misses.

ID: REQ-testing-038
Source: bu-ly3lv5.1 folded flake-and-queue move2 and AC4; performance-discipline measured enforcement; nightly existing watchdog.
Scope: v1-mandatory

#### Scenario: A stuck item is reported rather than silently lost
- **WHEN** a real test exceeds its per-test bound
- **THEN** the runner emits a named non-passing outcome within the bound
- **AND** a normal finite positive companion completes; collection/worker/finalizer hangs still hit the job/watchdog and never become PASS

#### Scenario: Browser install retry and cache do not bypass prerequisites
- **WHEN** browser cache is cold, warm, invalid, or installation fails/stalls
- **THEN** the install path verifies/installs the locked Chromium revision and OS dependencies with at most three bounded attempts
- **AND** each120-second installation allowance includes forced process-group termination (TERM at110s and KILL by120s); two10-second backoffs keep the declared retry interval at380s
- **AND** exhausted attempts fail the job; cache misses fall back to installation; no retry-until-green test policy is introduced

#### Scenario: Timing and long-tail promises retain their evidence window
- **WHEN** source milestones land but ten-run or two-week data is incomplete
- **THEN** the original remains open with exact remaining observations or explicit slice deferrals in coordinator notes
- **AND** two real merge_group coverage runs, ten named before/after check samples, ten merge_group timeout/p95 samples, and a complete two-week count of jobs longer than sixty minutes remain separate whole-outcome obligations
