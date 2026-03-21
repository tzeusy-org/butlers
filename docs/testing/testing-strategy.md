# Testing Strategy

> **Purpose:** Document the test pyramid, execution model, and quality gates for Butlers.
> **Audience:** Developers writing tests, CI/CD maintainers, code reviewers.
> **Prerequisites:** [Markers and Fixtures](markers-and-fixtures.md).

## Overview

Butlers follows a layered testing strategy organized as a test pyramid: many fast unit tests at the base, fewer integration tests in the middle, and selective end-to-end tests at the top. The framework uses pytest with pytest-asyncio (`asyncio_mode = "auto"`), pytest-xdist for parallel execution, and testcontainers for Docker-based integration tests. Benchmarks run separately and are excluded from default CI.

## Test Pyramid

### Unit Tests (`@pytest.mark.unit`)

Pure unit tests that require no Docker, no external services, and no network access. These test individual functions, classes, and modules in isolation using mocks and in-memory fixtures.

- **Speed:** Fast (milliseconds per test).
- **Dependencies:** None beyond Python packages.
- **Isolation:** Complete -- each test is independent.
- **Use for:** Business logic, data transformations, utility functions, config parsing, module contracts.

### Integration Tests (`@pytest.mark.integration`)

Tests that require Docker via testcontainers. The primary integration target is PostgreSQL (`pgvector/pgvector:pg17`). A shared Postgres container is started once per pytest session, and each test gets a fresh database with a random name for isolation.

- **Speed:** Moderate (seconds per test, container startup amortized).
- **Dependencies:** Docker daemon must be running.
- **Isolation:** Per-test database ensures no row/schema leakage.
- **Use for:** Database operations, migrations, query behavior, module startup/shutdown, credential store operations.

### Database Tests (`@pytest.mark.db`)

A subset of integration tests specifically for database operations. These use testcontainers with real PostgreSQL and test migration chains, schema isolation, and query correctness.

### End-to-End Tests (`@pytest.mark.e2e`)

Full-stack tests that require an authenticated CLI runtime (claude, codex), Docker, and optionally a real LLM API. These test the complete trigger-to-response flow.

- **Speed:** Slow (seconds to minutes per test).
- **Dependencies:** Authenticated CLI binary, Docker, LLM API access.
- **Isolation:** Uses real infrastructure; tests must be idempotent.
- **Use for:** Spawner integration, session lifecycle, routing accuracy, tool call chains.

### Benchmark Tests (`@pytest.mark.bench`)

Performance benchmarks that iterate over models, accumulate results, and generate scorecards. These are excluded from default CI via the `addopts` marker filter and must be run explicitly with `-m bench`.

Sub-markers:
- `@pytest.mark.discretion_bench` -- Discretion layer FORWARD/IGNORE classification benchmarks.
- `@pytest.mark.switchboard_bench` -- Switchboard routing accuracy benchmarks.

### Nightly Tests (`@pytest.mark.nightly`)

Long-running tests excluded from default CI. Run with `-m nightly`. These cover stress tests, extended integration scenarios, and cross-butler workflow validation.

## Parallel Execution

Tests run in parallel via pytest-xdist with 3 workers (`-n 3`), using `loadfile` distribution to keep tests from the same file on the same worker. This ensures module-scoped fixtures (shared FastAPI app, module-scoped DB pools) are not torn down mid-module.

```ini
addopts = --import-mode=importlib -m 'not nightly and not bench' -n 3 --dist loadfile --ignore=tests/benchmarks
```

The `importlib` import mode avoids module-name collisions when multiple butler test directories contain identically-named files (e.g., `test_tools.py` in different roster directories).

## Test Paths

Tests live in two locations:

- **`tests/`** -- Core framework tests (modules, API, database, connectors, spawner).
- **`roster/*/tests/`** -- Butler-specific tests for tools, API routes, and migrations.

Both paths are declared in `testpaths` in `pyproject.toml`.

## Quality Gates

The recommended quality gate sequence for agent runs:

```bash
# Lint
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise

# Format check
uv run ruff format --check src/ tests/ roster/ conftest.py -q

# Test (excluding DB and migration tests for speed)
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py \
  -q --maxfail=1 --tb=short
```

For final pre-merge validation, run the full suite:

```bash
make check  # lint + full test suite
```

## Test Execution Policy

- During active development, prefer targeted `pytest` runs for fast feedback.
- Run the full suite only when branch changes are finalized for pre-merge validation.
- Increase test scope gradually; do not default to full-suite runs early.

## Suppressed Warnings

The `pyproject.toml` `filterwarnings` section suppresses known noise:

- **DeprecationWarning from websockets/uvicorn** -- tracked upstream.
- **RuntimeWarning from AsyncMock** -- mock coroutine artifacts, not production leaks.
- **PytestUnhandledThreadExceptionWarning** -- port conflicts during parallel execution.

## Related Pages

- [Markers and Fixtures](markers-and-fixtures.md) -- Detailed fixture reference
- [Benchmark Report](benchmark-report.md) -- Performance benchmark results
- [Test Audit Report](test-audit-report.md) -- Coverage audit
