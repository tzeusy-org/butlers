# Pytest Runtime Profile (butlers-qkx.4)

Date: 2026-02-13
Issue: `butlers-qkx.4`

## Scope

This issue targets Postgres-backed test modules that previously duplicated
container/bootstrap fixtures:

- `roster/general/tests/test_tools.py`
- `roster/health/tests/test_tools.py`
- `roster/relationship/tests/test_contact_info.py`
- `roster/relationship/tests/test_tools.py`
- `roster/switchboard/tests/test_tools.py`
- `tests/integration/test_post_mail.py`
- `tests/tools/test_decomposition.py`
- `tests/tools/test_extraction.py`
- `tests/tools/test_relationship_types.py`
- `tests/tools/test_tools_extraction_queue.py`

## Fixture Strategy

A shared root fixture strategy is now used from `conftest.py`:

- `postgres_container` (`scope="session"`): one Docker Postgres container per pytest run.
- `provisioned_postgres_pool` (function fixture returning async context manager): each use provisions a
  new random database name via `Database.provision()` and opens a fresh asyncpg pool.

Per-module `pool` fixtures now only apply module-specific schema setup and no longer create their own
container or duplicate database provisioning logic.

## Isolation Contract

- Shared across the session: container process and Postgres server instance.
- Reset per test fixture usage: each `async with provisioned_postgres_pool()` call creates a unique
  database (`test_<uuid>`) and isolated pool.
- Result: no cross-test schema/row leakage between modules or test cases, while removing repeated
  container startup overhead.

## Benchmark

Command:

```bash
uv run pytest \
  roster/general/tests/test_tools.py \
  roster/health/tests/test_tools.py \
  roster/relationship/tests/test_contact_info.py \
  roster/relationship/tests/test_tools.py \
  roster/switchboard/tests/test_tools.py \
  tests/integration/test_post_mail.py \
  tests/tools/test_decomposition.py \
  tests/tools/test_extraction.py \
  tests/tools/test_relationship_types.py \
  tests/tools/test_tools_extraction_queue.py \
  -q --durations=10 --maxfail=1
```

### Baseline (before fixture consolidation)

- Source log: `.tmp/test-logs/butlers-qkx4-baseline-before.log`
- Result: `491 passed in 181.23s`
- Wall clock: `186.00s`

### After (shared fixture strategy)

- Source run: 2026-02-13 local run in this worktree
- Result: `491 passed in 148.44s`
- Wall clock: `150.96s`

### Delta

- Pytest runtime improvement: `32.79s` faster (`181.23s` -> `148.44s`, ~18.1%)
- Wall clock improvement: `35.04s` faster (`186.00s` -> `150.96s`, ~18.8%)
