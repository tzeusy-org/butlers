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
- **Quarantined tests are deselected by node, not by marker.** `addopts` also loads
  `-p tests.quarantine`, which deselects the flaky tests listed in `tests/quarantine.json`; see
  [Flaky-Test Quarantine](quarantine.md).
- **Leaked butler DB log handlers are detached after every test.** `addopts` also loads
  `-p tests.log_isolation`, whose autouse fixture removes any `ButlerDBLogHandler` a test
  attached to the root logger (for example by running `run_startup` without shutting the daemon
  down). Handlers that were already attached when the test started stay.
- **`e2e` is not deselected by marker.** Every gate keeps it out with `--ignore=tests/e2e`, and its
  own conftest skips without credentials; see the [E2E suite](e2e/README.md). `benchmark`,
  `routing_accuracy` and `tool_accuracy` subdivide that suite.
- **`pg_clock` and `faketime_fragile`** are deselected only from the nightly faketime legs. The
  first mixes Postgres and Python clocks; the shared testcontainer now runs under the same offset,
  so new DB tests do not need it and the existing marks are awaiting re-admission. The second waits
  on a timed thread primitive, needs a PostgreSQL statement or lock timeout to fire, or needs a live
  TLS certificate check, none of which works under a shifted clock. Everywhere else they run.
- **`smoke`** is the fast operational gate with no real LLM; **`contract`** marks architectural
  invariant tests drawn from doctrine and RFCs.

## Root Conftest Fixtures

The root `conftest.py` (at the repository root) provides fixtures visible to all test trees.

### `postgres_container` (session scope)

A shared Postgres testcontainer for all DB-backed tests in the pytest session. Uses the `pgvector/pgvector:pg17` image. The container is started once and reused across all tests; isolation is achieved at the database level (each test gets a fresh DB).

`_configure_postgres_container` gives it a 1 GB `/dev/shm`, because Docker's 64 MB default runs out once one container has served a large share of the suite. When the pytest process runs under libfaketime (`FAKETIME` set and `libfaketime` in `LD_PRELOAD`), the container gets the same library and offset, so SQL `now()` matches Python's shifted clock.

```python
@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    container = _configure_postgres_container(
        PostgresContainer("pgvector/pgvector:pg17"), os.environ
    )
    with container as pg:
        yield pg
```

### `provisioned_postgres_pool` (function scope)

Creates a fresh database and an **administrative** asyncpg pool for a single test. Each invocation creates a database with a random name (`test_{uuid_hex[:12]}`), ensuring no table or schema leakage between tests. The container login owns this pool; it intentionally supports partial, legacy, and absent-table setup. It does not establish a runtime role and is not evidence of runtime authorization or isolation. Use migrated databases and real `SET ROLE` identities for that proof.

Optional `schema=` validates and sets the administrative search path before provisioning; it neither creates that schema nor grants access. The pool retains the normal JSONB codec, finite SSL fallback, and context-owned cleanup on failure or cancellation. Runtime `Database` role refusals remain unchanged.

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

## Explicit test-kit helpers

Import DB-free helpers directly from `butlers.testing`; these are not root
fixtures and do not provide SQL, JSONB, transaction, role or RLS evidence.
Use a real migrated database when the behavior depends on those boundaries.

- `records.row(**columns)` and `records.records(iterable_of_mappings)` return
  fresh `Record` snapshots. Key/index lookup and `dict(record)` work; iteration
  yields **values**, as asyncpg does. Missing keys and attribute access refuse.
- `fake_pool.fake_pool(fetch=[...], execute="UPDATE 1")` configures only those
  async calls. Unconfigured methods raise `AssertionError`; explicit `None`
  is a configured result. Pass `AsyncMock(side_effect=...)` for queues or
  failures. Pool and acquired connection share call assertions; `acquire()`
  and connection `transaction()` create fresh `AsyncCtx` objects. Context
  exits release once, including body exceptions and cancellation. Callable
  SQL dispatch is refused.
- `migration.load_migration(path, name=None)` imports a fresh module from an
  explicit file without calling `upgrade` or `downgrade`. The default name is
  `_migration_<file stem>`; use the existing caller's explicit name when its
  identity matters. Opt-in `register=True` temporarily registers the module
  for import-time consumers such as dataclasses and restores the prior
  `sys.modules` entry even on failure. It does not provision a database.
- `clock.FakeClock(aware_instant, monotonic=0)` exposes `now()` and
  `monotonic()`. `advance(seconds_or_timedelta)` advances both; negative or
  nonfinite advances refuse without changing either. The default origin is
  fixed UTC. Inject these methods at existing seams, retaining purpose-specific
  datetime adapters when needed; never patch event-loop, timeout or SQL clocks.

- `mcp.registered_tools(server)` and `await mcp.tool_fn(server, name)` use a
  real `FastMCP` server's public async registration API. Await both helpers;
  the first returns a name-to-function snapshot, while the second looks up the
  current registered `Tool.fn` and raises `KeyError(name)` for an unknown name.
  Each lookup retains current wrappers and gates without unwrapping or caching.
  Direct handler calls do not prove MCP transport validation or authorization.
  Keep domain recorders and intentional registration failures; if real FastMCP
  rejects a subject's signature, report the rejection instead of loosening it.

## Testcontainer Resilience

### Hosted image acquisition

The existing serialized Testcontainers start boundary in root `conftest.py`
preloads a known image only when an actual container starts on GitHub Actions.
This covers `pgvector/pgvector:pg17` and the locked Ryuk image in unit,
integration, affected, smoke and nightly pytest jobs, including direct container
fixtures. Mock-only scopes, collection-only and docs-only jobs acquire nothing.
Ryuk reaches this same boundary only under its unchanged enabled/disabled policy.
The standalone owner-auth browser runner preloads its existing
`postgres:16-alpine` and, when enabled with the pinned default alias, Ryuk images on GitHub Actions. That
standalone helper is separate from the ordinary frontend Playwright jobs.

The script pins the original aliases to independently resolved Linux/amd64
manifest and configuration digests. It inspects the actual local image content,
platform and PostgreSQL major before reusing the ordinary Docker image store.
Otherwise it attempts that exact content from Google's [public Docker Hub
mirror](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
then tries the original Docker Hub repository once if the mirror is unavailable.
Google documents daemon-configured mirror use. This direct content-verified
preload is an optional implementation attempt, not that documented integration;
no daemon configuration is changed and availability is not assumed.
There are no registry logins, daemon mirror settings, new accounts, generic error
retries or saved test/container data. Docker's [digest
pull](https://docs.docker.com/reference/cli/docker/image/pull/) fixes the version;
verified content is tagged with the unchanged fixture alias only after inspection.

Wrong/corrupt content, failed alias installation and unavailable required images
remain failures. The total preparation envelope is 360 seconds, including its
own-child cleanup reserve, inside the unchanged job watchdog. Closed diagnostics
report the attempted source and fixed failure category without raw Docker output.
Each CLI invocation terminates its own process group on success and failure;
direct-parent completion cannot leave a TERM-ignoring descendant running. The
owner-browser preload requests pinned default Ryuk only when that exact image is
enabled in the SDK. Custom reaper images keep their ordinary acquisition behavior.
This does not certify migrations, roles, SQL outcomes or a test population. Those
still require the ordinary exact-head gates and independent receipts. Pin updates
must independently resolve the same upstream alias, manifest/configuration and
platform, then pass the actual fresh PostgreSQL/pgvector/Ryuk owning controls.

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

Root conftest retains the own-source guard before the first Butlers import and imports the
canonical shared fixtures once. It does not discover registries or preload roster jobs/routers.
The owning modules, jobs and API packages resolve supported roster namespaces on demand;
explicit inventory tests call complete discovery themselves and cannot pass with empty coverage.

`tests/contracts/test_import_budget.py` uses separate interpreters to enforce unused-import
absence of the embedding stack and a five-second calibrated root-conftest cap. Its forty-second
child timeout is independent. Recorded original/eager and slow controls establish that both
assertions can fail. Local elapsed/RSS samples do not claim a CI wall-clock gain.

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
  preflight commands and local `make check-guards`. Fresh collection, the lane-group totals, both budgets, smoke/release
  cmd/SHA/duration/status/skips and sanitized artifacts belong to preflight. Required check contains only the verdict step.
  The independent owned `coverage-report` job waits for all eleven covered groups and preflight,
  reads each raw CoverageData database and its checkout/run/attempt/lane/group/config/digest
  metadata, requires the
  complete measured source population and compatible tracing, then combines and publishes
  the report; only scheduled/manual main success may update the badge. Missing, empty,
  corrupt, stale or mismatched inputs fail
  reporting before publication. Coverage reporting never changes required test verdicts.
- Protected merge-group and ordinary PR heavy shards receive exact `CI_COVERAGE=0`;
  the nightly coverage jobs (scheduled or manual) receive `1`. The standalone
  runner defaults to `1`; any supplied value other than literal `0` or `1` fails. Only
  enabled runs require `COVERAGE_FILE` and produce/upload raw coverage plus metadata. PRs
  retain the same paths, markers, workers, maxfail and sanitized JUnit evidence without
  instrumentation. Affected-only runs remain without coverage. Direct local CI targets
  retain their existing coverage behavior. Historical merge-group uploads and timings remain separately retained
  observation obligations; M1/M2 source changes claim no wall-clock gain. M3 supplies finite source bounds; its hosted timeout/cold-cache observations and the M4
  calibration in `fail-closed-ci-assurance` remain unfinished.
- Pytest's configured default is 300 seconds per item. Existing justified finite marks stay;
  a named timeout fails the item. Node's two `node --test` commands use the same 300-second
  bound; Vitest and Playwright retain their existing shorter defaults and retry settings.
  Collection, worker setup and session finalization still need the owning workflow watchdog.
  A killed or incomplete receipt remains UNKNOWN, never a pass.
- Every workflow job has a finite positive `timeout-minutes`, inventoried in
  `scripts/ci-job-timeouts.json`. Named healthy before samples, provisional rare-job debt,
  full UV recovery allowance and browser/cold-setup reserves are recorded separately. They
  are initial bounds, not current p95 calibration or proof that no job exceeds sixty minutes.
  Faketime retains its existing 75-minute job and 3600-second ABRT plus 30-second KILL
  watchdog until healthy measurements justify a separate change.
- Browser caches require the exact runner OS, architecture, package-lock hash and installed
  locked Playwright version. Cache failure is advisory; installation is mandatory on hits
  and misses and always installs the OS dependencies first, as their own bounded
  `install-deps` phase (apt under sudo; two attempts, 150/160 s) so a slow apt cannot consume
  the browser download's 110/120 s deadline. The deps supervisor runs under noninteractive
  sudo, starts apt in its own process group, and terminates and reaps that group's members
  before permitting a retry. Cancellation or parent EOF triggers the same cleanup; an
  unproven cleanup fails closed without starting another attempt. Reserved supervisor exit
  outcomes 80/81/82/83 establish cleaned success/failure/deadline/cancellation; generic zero,
  nonzero or signal exits never authorize retry. Cancellation/EOF (83) is never retryable,
  including a direct supervisor cancellation with the Node parent still alive. Only a
  confirmed cleaned elapsed deadline (82) selects the official-Ubuntu preference for the
  existing second attempt; ordinary clean failure (81) retries the default route. The healthy
  first attempt is unchanged. Recovery accepts only the witnessed Ubuntu24 noble deb822
  source with the exact local mirror URI and three recognized Azure/archive/security
  candidates. It changes only that copied Ubuntu URI and mirror priority, preserving all
  source/trust fields, third-party bytes and ordinary dpkg/index/archive state. Unknown
  layouts, ambiguous fields, links, writable/untrusted inputs and modified endpoints fail
  closed. Copied configuration stays root-private; only nonsecret mirror data is sandbox
  readable. The actual APT configuration reader compares ordinary settings and effective
  private source paths, rejecting later source overrides, binary path overrides and rerooting.
  Its child shares the existing process group/deadline; it never prints configuration data.
  Partial setup removes only its own files, and a full overlay remains until child cleanup
  is confirmed. Local fixture-reader controls do not prove Ubuntu24 parsing, sandbox access
  or signed package transport; those require explicit actual-Ubuntu/hosted evidence.
  Setup shares the child's cleanup lifetime. Non-reaping leader
  observation retains the group identity through its last signal, before reaping. `EPERM` never crashes
  the parent or establishes that a process is gone. The installed-version reader checks all
  three Playwright packages against the lock. Each attempt installs and launches the locked
  Chromium, rejects a missing/corrupt/wrong-version executable and closes it. Attempts two
  and three force repair; test retries are unchanged.
  The installer has a 720-second outer budget: two dependency attempts (TERM150/KILL160,
  up to two seconds each for cleanup, one ten-second backoff), then three browser attempts
  (TERM110/KILL120, two ten-second backoffs). It cleans ordinary descendants even when
  their shell exits first; these bounds do not establish
  containment of deliberately detached hostile processes. Local synthetic install controls
  prove the supervisor, not actual OS-package installation or hosted cache behavior. The
  frontend-e2e job cap is provisionally 20 minutes: ceil((2*228s historical whole-job envelope
  + 720s full installer reserve)/60). This source-derived adjustment retains observed setup
  and does not establish current healthy p95 calibration. See the active
  [failure matrix](playwright-installer-failure-matrix.md).
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

### Owned coverage pipeline validation

The nightly workflow (`.github/workflows/nightly.yml`) owns daily/manual full
coverage; dispatch it on a branch to validate a coverage-pipeline change (owner
decision bu-w72da2 = A retired the former PR-path workflow). It runs the same five unit and
six integration pytest-split groups, the lane totals against fresh collect-only
counts and the smoke command before the strict CoverageData reader. Coverage
metadata preserves the actual event, checkout, run, attempt, lane and group. Both
preflight and reporting fail visibly when any prerequisite fails, skips or is
unknown. Every run uploads the full report artifact; only scheduled/manual main
success can update the badge. Its exact Python/cache/frozen repair comes from
the local setup composite, with existing finite job caps retained per row.

The required queue now executes every shard untraced and has no coverage reporter.
The owned workflow passed genuine eleven-input PR assurance run 38672598762 before
this source cutover; it is mechanism evidence for that earlier revision, not final
queue or timing proof. Local standalone coverage still defaults on and malformed
CI_COVERAGE flags still refuse. Covered weights may truthfully age into fallback
until genuine covered-workflow observer refresh is implemented.
