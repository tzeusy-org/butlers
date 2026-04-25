# Testing — Delta for Adapter Integration Test Suites

## MODIFIED Requirements

### Requirement: Test Markers and Categories
Tests are classified into four tiers with increasing scope, cost, and infrastructure requirements. Markers control which tiers execute in which environment.

#### Scenario: Unit tests (default, unmarked)
- **WHEN** a test has no marker or is marked `@pytest.mark.unit`
- **THEN** it runs with no external dependencies (no Docker, no API keys, no network)
- **AND** it validates isolated logic: parsing, validation, data transformations, pure functions

#### Scenario: Integration tests
- **WHEN** a test is marked `@pytest.mark.integration`
- **THEN** it requires Docker for testcontainers (PostgreSQL)
- **AND** if Docker is unavailable, the test is skipped via the `docker_available` check
- **AND** all tests under `roster/` are auto-marked as integration tests via `roster/conftest.py`

#### Scenario: Nightly tests (adapter integration)
- **WHEN** a test is marked `@pytest.mark.nightly`
- **THEN** it is excluded from default CI via `addopts = "-m 'not nightly'"`
- **AND** it requires the adapter's CLI binary on PATH (skipped via `skipif` when missing)
- **AND** it requires valid LLM API credentials in the environment
- **AND** it validates parser correctness against real CLI output (structural assertions only)

#### Scenario: End-to-end tests
- **WHEN** a test is marked `@pytest.mark.e2e`
- **THEN** it requires Docker (testcontainers), `ANTHROPIC_API_KEY` (real LLM calls), and the `claude` CLI binary on PATH
- **AND** it is excluded from CI/CD via three independent mechanisms: `pytest.mark.e2e` marker, environment guard (`ANTHROPIC_API_KEY` check), and explicit `--ignore=tests/e2e` in CI configuration

## ADDED Requirements

### Requirement: Adapter Integration Test Infrastructure
Shared fixtures and helpers SHALL be provided in `tests/adapters/conftest.py` for adapter integration tests. These reduce boilerplate and enforce consistent patterns across all adapter nightly test suites.

#### Scenario: run_cli fixture
- **WHEN** an integration test needs to invoke a real CLI binary
- **THEN** it SHALL use the `run_cli(binary, prompt, timeout=120)` fixture from conftest
- **AND** the fixture SHALL return `(stdout, stderr, returncode)` via `subprocess.run()`
- **AND** the fixture SHALL default `cwd` to `/tmp` to avoid polluting the working directory

#### Scenario: parse_jsonl_events helper
- **WHEN** an integration test needs to parse raw CLI JSON output into event dicts
- **THEN** it SHALL use the `parse_jsonl_events(stdout)` helper from conftest
- **AND** the helper SHALL skip non-JSON lines and return a list of parsed dicts

#### Scenario: Binary availability markers
- **WHEN** an adapter's CLI binary is not installed on PATH
- **THEN** all integration tests for that adapter SHALL be skipped via `@pytest.mark.skipif(not shutil.which("<binary>"), reason="<binary> not on PATH")`
