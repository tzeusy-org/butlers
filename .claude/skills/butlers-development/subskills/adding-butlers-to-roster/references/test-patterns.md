# Test Patterns Reference

## Test File Template

```python
"""Tests for butlers.tools.<butler_name> — <brief description>."""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

# Skip all tests if Docker is unavailable
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    """Generate a unique database name per test."""
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with butler tables and return a pool."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        schema="<butler-name>",  # Matches butler.toml [butler.db].schema
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    # Create schema and tables matching this butler's Alembic migrations
    # Note: Database.connect() sets search_path to "<schema>, shared, public"
    await p.execute("CREATE SCHEMA IF NOT EXISTS <butler_name>")
    await p.execute("SET search_path TO <butler_name>, public")
    await p.execute("""
        CREATE TABLE IF NOT EXISTS <table_name> (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- columns matching migration
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Add indexes
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_<table>_<col>_gin
        ON <table_name> USING GIN (<jsonb_col>)
    """)

    yield p
    await db.close()
```

## Test Organization

Organize tests by tool function, with section dividers:

```python
# ------------------------------------------------------------------
# thing_create
# ------------------------------------------------------------------

async def test_thing_create(pool):
    """thing_create inserts a new thing and returns its UUID."""
    from butlers.tools.<butler_name> import thing_create
    ...

async def test_thing_create_duplicate(pool):
    """thing_create raises on duplicate name."""
    from butlers.tools.<butler_name> import thing_create
    ...

# ------------------------------------------------------------------
# thing_get
# ------------------------------------------------------------------

async def test_thing_get(pool):
    ...

async def test_thing_get_missing(pool):
    """thing_get returns None for non-existent thing."""
    ...
```

## Import Pattern

Import tool functions INSIDE the test function, not at module level:

```python
# CORRECT
async def test_thing_create(pool):
    from butlers.tools.mybutler import thing_create
    result = await thing_create(pool, "test")
    assert isinstance(result, uuid.UUID)

# WRONG - don't do this
from butlers.tools.mybutler import thing_create  # module-level import

async def test_thing_create(pool):
    result = await thing_create(pool, "test")
```

This avoids import errors when the tools module hasn't been registered yet.

## What to Test

For each tool function, test:

1. **Happy path**: Normal operation with valid inputs
2. **Not-found**: Missing ID returns None (gets) or raises ValueError (mutations)
3. **Constraint violations**: Duplicate names, FK violations
4. **Edge cases**: Empty inputs, None values, boundary values
5. **JSONB round-trip**: Data stored as JSONB comes back correctly parsed

### Example test coverage for a CRUD tool set:

```python
# Create
async def test_create_returns_uuid(pool): ...
async def test_create_without_optional_fields(pool): ...
async def test_create_with_all_fields(pool): ...
async def test_create_duplicate_raises(pool): ...

# Get
async def test_get_existing(pool): ...
async def test_get_missing_returns_none(pool): ...
async def test_get_includes_jsonb_fields(pool): ...

# Update
async def test_update_changes_field(pool): ...
async def test_update_not_found_raises(pool): ...
async def test_update_preserves_unchanged_fields(pool): ...

# Delete
async def test_delete_removes_entity(pool): ...
async def test_delete_not_found_raises(pool): ...
async def test_delete_cascades_children(pool): ...  # if applicable

# Search / List
async def test_search_by_field(pool): ...
async def test_search_no_results(pool): ...
async def test_search_multiple_filters(pool): ...
async def test_list_returns_ordered(pool): ...
```

## Parametrize for JSONB Types

Test that JSONB columns accept various data types:

```python
@pytest.mark.parametrize(
    "data",
    [
        {"string_val": "hello"},
        {"int_val": 42},
        {"float_val": 3.14},
        {"bool_val": True},
        {"null_val": None},
        {"list_val": [1, 2, 3]},
        {"nested": {"deep": {"value": "found"}}},
    ],
    ids=["string", "integer", "float", "boolean", "null", "list", "nested"],
)
async def test_freeform_jsonb_types(pool, data):
    """Things accept various freeform JSONB data types."""
    from butlers.tools.mybutler import thing_create, thing_get

    tid = await thing_create(pool, data=data)
    thing = await thing_get(pool, tid)
    assert thing["data"] == data
```

## Assertion Patterns

```python
# UUID returned from create
assert isinstance(result, uuid.UUID)

# Not-found returns None
result = await thing_get(pool, uuid.uuid4())
assert result is None

# ValueError for mutations on missing
with pytest.raises(ValueError, match="not found"):
    await thing_delete(pool, uuid.uuid4())

# Constraint violation
with pytest.raises(asyncpg.UniqueViolationError):
    await thing_create(pool, name="duplicate")

# Verify DB state directly
row = await pool.fetchrow("SELECT * FROM things WHERE id = $1", thing_id)
assert row is not None
assert row["name"] == "expected"
```

## Running Tests

```bash
# Run all tests
make test

# Run a single butler's tests
uv run pytest roster/<butler-name>/tests/test_tools.py -v

# Run a single test
uv run pytest roster/<butler-name>/tests/test_tools.py::test_thing_create -v

# Run tests from the project root tests/ directory
uv run pytest tests/test_<butler-name>_tools.py -v

# Skip integration tests (no Docker)
uv run pytest -m "not integration" -v
```
