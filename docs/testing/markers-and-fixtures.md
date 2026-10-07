# Markers and Fixtures

> **Purpose:** Reference for pytest markers, shared fixtures, and test infrastructure in Butlers.
> **Audience:** Developers writing tests, anyone debugging test failures.
> **Prerequisites:** [Testing Strategy](testing-strategy.md).

## Overview

Butlers uses a two-level conftest architecture: a root `conftest.py` that makes shared fixtures available to all test trees (including `roster/*/tests/`), and a `src/butlers/testing/shared_fixtures.py` module that provides reusable mock types. This page documents all markers, fixtures, and the parallel execution model.

## Markers

The marker list and the default `addopts` live in `pyproject.toml` (`[tool.pytest.ini_options]`);
read them there. The semantics that matter:

- **Deselected by default:** `addopts` carries `-m 'not nightly and not bench and not perf'` and
  ignores `tests/benchmarks`, so those tests run only when asked for with `-m`.
- **`e2e` is not deselected by marker.** Every gate keeps it out with `--ignore=tests/e2e`, and its
  own conftest skips without credentials; see the [E2E suite](e2e/README.md). `benchmark`,
  `routing_accuracy` and `tool_accuracy` subdivide that suite.
- **`pg_clock` and `faketime_fragile`** are deselected only from the nightly faketime legs: the
  first mixes Postgres and Python clocks that libfaketime skews apart, the second waits on a timed
  thread primitive that never expires under a shifted clock. Everywhere else they run.
- **`smoke`** is the fast operational gate with no real LLM; **`contract`** marks architectural
  invariant tests drawn from doctrine and RFCs.

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

`_install_resilient_testcontainers_stop()` is the **only** patch on `DockerContainer.stop`, and it wraps the container removal (not the whole `stop`) so the retry cannot also re-run a removal that already succeeded. Failures classify through the single predicate `_is_transient_docker_teardown_error()`: a `requests` read timeout is transient by type, and otherwise the message and any docker-py `.explanation` are matched -- across the whole `__cause__`/`__context__` chain -- against `_TRANSIENT_DOCKER_TEARDOWN_ERROR_MARKERS` ("did not receive an exit event", "tried to kill container", "no such container", "removal of container", "is already in progress", "is dead or marked for removal", "read timed out"). Transient failures are retried 4 times with exponential backoff (0.1s, 0.2s, 0.4s).

A **final transient** failure warns and lets the run finish; that leaks a container, and the `RuntimeWarning` names it so the leak is traceable (see [Orphaned Test Containers](orphaned-testcontainers.md)). A **non-transient** failure raises on the first attempt. Both halves of that decision are pinned by `tests/scripts/test_conftest_teardown_patch.py`, which also fails if a second `DockerContainer.stop` patch is ever reintroduced.

The patches are idempotent -- they check for sentinel attributes to avoid double-patching.

## Parallel Execution Details

| Setting | Value | Rationale |
|---------|-------|-----------|
| `-n 3` | 3 xdist workers | Avoids OOM when polecats run alongside the Compose stack |
| `--dist loadfile` | File-level distribution | Preserves module-scoped fixtures |
| `--import-mode=importlib` | Importlib mode | Avoids name collisions across `roster/*/tests/` |

These live in `addopts`, which pytest prepends to **every** invocation in this repo. So an
omitted `-n` is not "the default" -- it is three workers. Anything that needs a different
mode has to say so on its own command line: `make test-qg-serial` passes `-n 0` for exactly
this reason, and ran on three workers until it did. `-p no:xdist` is not a
substitute; it turns the inherited `-n 3` into an unrecognized-argument error.
`tests/contracts/test_qg_serial_target.py` pins the merged value for both gate targets,
because a grep of the Makefile cannot see it.

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

## Implementation Notes

- CI's required `check` consumes the actual `needs` JSON under `always()`. That scheduling
  condition alone does not enforce a failed dependency. Changes/guards and applicable planner
  verdicts must succeed; preflight is required independently of heavy-shard classification.
  Scoped PRs require successful preflight and affected tests while every heavy shard skips.
  Docs-only PRs and pushes to main admit only their complete backend skip pairing; full backend
  PRs and merge-group trees require every heavy shard. Added needed jobs default to no skip
  permission, and malformed verdict/classifier/planner evidence fails closed.
- Lock, lint, format and SQL-safety checks execute in guards after the existing UV install
  recovery policy and frozen dev synchronization. Their command scopes match the former
  preflight commands and local `make check-guards`. Exact-once manifests, collected budgets,
  smoke/release cmd/SHA/duration/status/skips and sanitized artifacts stay in preflight.
  Coverage still combines in check; extraction and finite-timeout changes in the active
  `fail-closed-ci-assurance` plan remain unfinished. M1 makes no wall-clock gain claim.
- Root `conftest.py` also serialises testcontainers `DockerClient.run()` across xdist workers and
  caps `-n auto` at 3 workers (`PYTEST_XDIST_AUTO_WORKERS` overrides).
- Startup timeouts (before a container starts) are host contention: reduce load and rely on the
  init retry. Teardown races happen in `container.remove()` and are the teardown patch's job.
- DB tests use `testcontainers.postgres.PostgresContainer` with `asyncpg.create_pool()`.
- The guarded core integration modules (`tests/core/test_core_{state,sessions,scheduler}.py`) apply
  session loop scope per async test (`@pytest.mark.asyncio(loop_scope="session")` or the local
  `tests/core/test_core_{state,sessions,scheduler}.py::_asyncio_session` alias), never to a whole
  module or class, because synchronous guards may be
  collected there.
- Root `conftest.py` is the only global registration layer for `shared_fixtures`; nested conftests
  must not re-register them but may define tree-scoped fixtures and hooks.
- Patch testcontainers teardown at exactly one layer: assign `DockerContainer.stop` once and retry
  `container.remove()`, not `stop()`. The transient errors are 404 "no such container", 409
  "removal already in progress" and read timeouts, so match markers anywhere in the exception chain
  (including docker-py's `explanation`) rather than gating on HTTP 500. Swallow the final transient
  failure with a `RuntimeWarning`; fail fast on anything else.

## Related Pages

- [Testing Strategy](testing-strategy.md) -- Test pyramid and quality gates
