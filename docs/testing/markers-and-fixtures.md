# Markers and Fixtures

> **Purpose:** Reference for pytest markers, shared fixtures, and test infrastructure in Butlers.
> **Audience:** Developers writing tests, anyone debugging test failures.
> **Prerequisites:** [Testing Strategy](testing-strategy.md).

## Overview

Butlers uses a two-level conftest architecture: a root `conftest.py` that makes shared fixtures available to all test trees (including `roster/*/tests/`), and a `src/butlers/testing/shared_fixtures.py` module that provides reusable mock types. This page documents all markers, fixtures, and the parallel execution model.

## Markers

Markers are defined in `pyproject.toml` under `[tool.pytest.ini_options]`:

| Marker | Description | Default CI |
|--------|-------------|------------|
| `unit` | Pure unit tests -- no Docker, no external services | Included |
| `integration` | Require Docker (testcontainers) | Included |
| `e2e` | Require authenticated CLI runtime, claude binary, and Docker | Included |
| `benchmark` | Benchmark mode -- iterate over models, generate scorecards | Excluded |
| `routing_accuracy` | E2E routing accuracy -- verify triage_target matches expected | Included |
| `tool_accuracy` | E2E tool-call accuracy -- verify expected tool names are called | Included |
| `nightly` | Long-running tests excluded from default CI | Excluded |
| `bench` | All Ollama model benchmarks -- require live endpoint | Excluded |
| `discretion_bench` | Discretion layer FORWARD/IGNORE benchmarks | Excluded |
| `switchboard_bench` | Switchboard routing accuracy benchmarks | Excluded |
| `db` | Database integration tests -- testcontainers with real PostgreSQL | Included |

The default `addopts` excludes `nightly` and `bench` markers:
```ini
-m 'not nightly and not bench'
```

## Root Conftest Fixtures

The root `conftest.py` (at the repository root) provides fixtures visible to all test trees.

### `postgres_container` (session scope)

A shared Postgres testcontainer for all DB-backed tests in the pytest session. Uses the `pgvector/pgvector:pg17` image. The container is started once and reused across all tests; isolation is achieved at the database level (each test gets a fresh DB).

```python
@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg17") as pg:
        yield pg
```

### `provisioned_postgres_pool` (function scope)

Creates a fresh database and asyncpg pool for a single test. Each invocation creates a database with a random name (`test_{uuid_hex[:12]}`), ensuring no table or schema leakage between tests.

```python
async with provisioned_postgres_pool() as pool:
    # pool is an asyncpg.Pool connected to a fresh test database
    await pool.execute("CREATE TABLE ...")
```

Parameters:
- `min_pool_size` (default: 1)
- `max_pool_size` (default: 3)

## Shared Fixtures Module

`src/butlers/testing/shared_fixtures.py` exports reusable types for testing spawner behavior.

### `SpawnerResult`

A dataclass representing the result of an LLM CLI spawner invocation:

| Field | Type | Default |
|-------|------|---------|
| `output` | `str \| None` | `None` |
| `success` | `bool` | `False` |
| `tool_calls` | `list[dict]` | `[]` |
| `error` | `str \| None` | `None` |
| `duration_ms` | `int` | `0` |

### `MockSpawner`

A mock LLM CLI spawner that returns configurable results and records invocations:

```python
spawner = MockSpawner()
spawner.enqueue_result(SpawnerResult(success=True, output="Done"))
result = await spawner.spawn(trigger="test", prompt="hello")
assert result.success
assert spawner.invocations == [{"trigger": "test", "prompt": "hello"}]
```

Methods:
- `enqueue_result(result)` -- Queue a result for the next invocation.
- `spawn(**kwargs)` -- Simulate spawning; returns queued result or default.

### `mock_spawner` (function scope)

A pytest fixture that provides a fresh `MockSpawner` instance:

```python
def test_something(mock_spawner):
    mock_spawner.enqueue_result(SpawnerResult(success=True))
    ...
```

## Testcontainer Resilience

The root conftest patches testcontainers with resilient startup and teardown handlers to tolerate transient Docker daemon races, which are common under pytest-xdist parallel execution:

### Startup Resilience

`_install_resilient_testcontainers_startup()` wraps `DockerClient.__init__` to retry on transient Docker API errors (e.g., "error while fetching server api version", "read timed out") up to 3 attempts with 0.5s delay.

### Teardown Resilience

`_install_resilient_testcontainers_stop()` and `_patch_testcontainers_stop_with_retry()` wrap container removal to tolerate races like "did not receive an exit event", "no such container", "removal of container is already in progress". These are retried up to 4 times with exponential backoff.

Both patches are idempotent -- they check for sentinel attributes to avoid double-patching.

## Parallel Execution Details

| Setting | Value | Rationale |
|---------|-------|-----------|
| `-n 3` | 3 xdist workers | Avoids OOM when polecats run alongside k3s |
| `--dist loadfile` | File-level distribution | Preserves module-scoped fixtures |
| `--import-mode=importlib` | Importlib mode | Avoids name collisions across `roster/*/tests/` |

## Module Discovery

The root conftest triggers roster module discovery at import time:

```python
from butlers.modules.registry import default_registry as _default_registry
_default_registry()
```

This ensures dynamically-loaded modules are available in `sys.modules` before test collection, preventing import errors in butler-specific test files.

## Docker Availability Check

The conftest checks for Docker availability:

```python
docker_available = shutil.which("docker") is not None
```

Tests can use this to skip gracefully when Docker is not installed.

## Related Pages

- [Testing Strategy](testing-strategy.md) -- Test pyramid and quality gates
- [Benchmark Report](benchmark-report.md) -- Performance results
- [Test Audit Report](test-audit-report.md) -- Coverage analysis
