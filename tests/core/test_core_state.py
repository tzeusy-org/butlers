"""Tests for butlers.core.state — JSONB key-value state store."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so the pool (created in the session
    # fixture loop via asyncio_default_fixture_loop_scope=session) is usable.
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


# Use the session-scoped postgres_container from root conftest (not a local override)
# so the event loop is shared across the whole session, avoiding asyncpg loop mismatch.


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with the state table and return a pool.

    Uses the session-scoped postgres_container from root conftest.
    Each test gets a fresh database for full isolation.
    """
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=5,
    )
    await db.provision()
    p = await db.connect()

    # Create the state table (mirrors Alembic core migration + core_005 version column)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            version INTEGER NOT NULL DEFAULT 1
        )
    """)

    yield p
    await db.close()


# ------------------------------------------------------------------
# state_get
# ------------------------------------------------------------------


async def test_get_existing_key(pool):
    """state_get returns the stored JSONB value for an existing key."""
    from butlers.core.state import state_get, state_set

    await state_set(pool, "greeting", "hello")
    result = await state_get(pool, "greeting")
    assert result == "hello"


async def test_get_missing_key(pool):
    """state_get returns None for a key that does not exist."""
    from butlers.core.state import state_get

    result = await state_get(pool, "nonexistent_key")
    assert result is None


# ------------------------------------------------------------------
# state_set — version tracking
# ------------------------------------------------------------------


async def test_set_inserts_new_key(pool):
    """state_set inserts a brand-new key-value pair."""
    from butlers.core.state import state_get, state_set

    await state_set(pool, "new_key", {"a": 1})
    result = await state_get(pool, "new_key")
    assert result == {"a": 1}


async def test_set_returns_version_1_on_insert(pool):
    """state_set returns version=1 when inserting a new key."""
    from butlers.core.state import state_set

    version = await state_set(pool, f"vers_insert:{uuid.uuid4().hex}", "value")
    assert version == 1


async def test_set_increments_version_on_update(pool):
    """state_set increments version on each subsequent upsert."""
    from butlers.core.state import state_set

    key = f"vers_update:{uuid.uuid4().hex}"
    v1 = await state_set(pool, key, "first")
    assert v1 == 1

    v2 = await state_set(pool, key, "second")
    assert v2 == 2

    v3 = await state_set(pool, key, "third")
    assert v3 == 3


async def test_set_updates_existing_key(pool):
    """state_set upserts — updates the value and updated_at on conflict."""
    from butlers.core.state import state_get, state_set

    await state_set(pool, "counter", 1)
    assert await state_get(pool, "counter") == 1

    await state_set(pool, "counter", 2)
    assert await state_get(pool, "counter") == 2

    # Verify updated_at was refreshed
    row = await pool.fetchrow("SELECT updated_at FROM state WHERE key = $1", "counter")
    assert row is not None


async def test_existing_rows_have_default_version(pool):
    """Rows inserted before the version column existed get version=1 by default."""
    key = f"legacy:{uuid.uuid4().hex}"
    # Simulate a legacy insert without specifying the version column
    await pool.execute(
        "INSERT INTO state (key, value, updated_at) VALUES ($1, $2::jsonb, now())",
        key,
        '"legacy_value"',
    )
    row = await pool.fetchrow("SELECT version FROM state WHERE key = $1", key)
    assert row is not None
    assert row["version"] == 1


# ------------------------------------------------------------------
# state_delete
# ------------------------------------------------------------------


async def test_delete_existing_key(pool):
    """state_delete removes the row for the given key."""
    from butlers.core.state import state_delete, state_get, state_set

    await state_set(pool, "doomed", "bye")
    assert await state_get(pool, "doomed") == "bye"

    await state_delete(pool, "doomed")
    assert await state_get(pool, "doomed") is None


async def test_delete_missing_key_is_noop(pool):
    """state_delete does not raise when the key is absent."""
    from butlers.core.state import state_delete

    # Should complete without error
    await state_delete(pool, "never_existed")


# ------------------------------------------------------------------
# state_list
# ------------------------------------------------------------------


async def test_list_all_entries(pool):
    """state_list with keys_only=True returns list of key strings."""
    from butlers.core.state import state_list, state_set

    # Use a unique prefix to avoid collisions from other tests
    await state_set(pool, "list_all:a", 1)
    await state_set(pool, "list_all:b", 2)
    await state_set(pool, "list_all:c", 3)

    # Default behavior: keys_only=True
    keys = await state_list(pool, prefix="list_all:")
    assert isinstance(keys, list)
    assert all(isinstance(k, str) for k in keys)
    assert "list_all:a" in keys
    assert "list_all:b" in keys
    assert "list_all:c" in keys


async def test_list_with_prefix_filter(pool):
    """state_list with prefix only returns matching keys."""
    from butlers.core.state import state_list, state_set

    await state_set(pool, "proj:alpha", "a")
    await state_set(pool, "proj:beta", "b")
    await state_set(pool, "other:gamma", "g")

    # Default behavior: keys_only=True
    keys = await state_list(pool, prefix="proj:")
    assert "proj:alpha" in keys
    assert "proj:beta" in keys
    assert "other:gamma" not in keys


async def test_list_keys_only_false(pool):
    """state_list with keys_only=False returns dicts with key and value."""
    from butlers.core.state import state_list, state_set

    await state_set(pool, "compat:x", {"val": 10})
    await state_set(pool, "compat:y", {"val": 20})

    entries = await state_list(pool, prefix="compat:", keys_only=False)
    assert isinstance(entries, list)
    assert all(isinstance(e, dict) for e in entries)
    assert all("key" in e and "value" in e for e in entries)

    keys = [e["key"] for e in entries]
    assert "compat:x" in keys
    assert "compat:y" in keys

    # Verify values are included
    for e in entries:
        if e["key"] == "compat:x":
            assert e["value"] == {"val": 10}
        elif e["key"] == "compat:y":
            assert e["value"] == {"val": 20}


# ------------------------------------------------------------------
# JSONB value types
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "a string",
        42,
        3.14,
        {"nested": {"key": "value"}},
        [1, "two", 3],
        True,
        False,
        None,
    ],
    ids=["string", "integer", "float", "object", "array", "true", "false", "null"],
)
async def test_jsonb_value_types(pool, value):
    """state_set/state_get round-trips all JSON-compatible value types."""
    from butlers.core.state import state_get, state_set

    key = f"type_test:{type(value).__name__}:{value}"
    await state_set(pool, key, value)
    result = await state_get(pool, key)
    assert result == value


# ------------------------------------------------------------------
# state_compare_and_set — CAS success
# ------------------------------------------------------------------


async def test_cas_success(pool):
    """state_compare_and_set succeeds when the expected version matches."""
    from butlers.core.state import state_compare_and_set, state_get, state_set

    key = f"cas_success:{uuid.uuid4().hex}"
    v1 = await state_set(pool, key, "initial")
    assert v1 == 1

    v2 = await state_compare_and_set(pool, key, expected_version=1, new_value="updated")
    assert v2 == 2

    result = await state_get(pool, key)
    assert result == "updated"


async def test_cas_returns_incremented_version(pool):
    """state_compare_and_set returns the new (incremented) version on success."""
    from butlers.core.state import state_compare_and_set, state_set

    key = f"cas_version:{uuid.uuid4().hex}"
    await state_set(pool, key, "v1")  # version = 1
    await state_set(pool, key, "v2")  # version = 2

    new_version = await state_compare_and_set(pool, key, expected_version=2, new_value="v3")
    assert new_version == 3


# ------------------------------------------------------------------
# state_compare_and_set — CAS failure
# ------------------------------------------------------------------


async def test_cas_fails_on_version_mismatch(pool):
    """state_compare_and_set raises CASConflictError when version does not match."""
    from butlers.core.state import CASConflictError, state_compare_and_set, state_set

    key = f"cas_mismatch:{uuid.uuid4().hex}"
    await state_set(pool, key, "initial")  # version = 1

    with pytest.raises(CASConflictError) as exc_info:
        # Caller mistakenly believes version is 99
        await state_compare_and_set(pool, key, expected_version=99, new_value="should_fail")

    err = exc_info.value
    assert err.key == key
    assert err.expected_version == 99
    assert err.actual_version == 1  # actual version stored


async def test_cas_fails_on_missing_key(pool):
    """state_compare_and_set raises CASConflictError when the key does not exist."""
    from butlers.core.state import CASConflictError, state_compare_and_set

    key = f"cas_missing:{uuid.uuid4().hex}"

    with pytest.raises(CASConflictError) as exc_info:
        await state_compare_and_set(pool, key, expected_version=1, new_value="value")

    err = exc_info.value
    assert err.key == key
    assert err.actual_version is None


async def test_cas_does_not_overwrite_on_failure(pool):
    """Failed CAS must not modify the stored value."""
    from butlers.core.state import CASConflictError, state_compare_and_set, state_get, state_set

    key = f"cas_no_overwrite:{uuid.uuid4().hex}"
    await state_set(pool, key, "original")

    with pytest.raises(CASConflictError):
        await state_compare_and_set(pool, key, expected_version=42, new_value="corrupted")

    # Value must be unchanged
    assert await state_get(pool, key) == "original"


async def test_cas_conflict_error_message(pool):
    """CASConflictError has a helpful human-readable message."""
    from butlers.core.state import CASConflictError, state_compare_and_set, state_set

    key = f"cas_errmsg:{uuid.uuid4().hex}"
    await state_set(pool, key, "x")

    with pytest.raises(CASConflictError) as exc_info:
        await state_compare_and_set(pool, key, expected_version=5, new_value="y")

    msg = str(exc_info.value)
    assert key in msg
    assert "5" in msg  # expected version
    assert "1" in msg  # actual version


# ------------------------------------------------------------------
# state_compare_and_set — concurrent CAS
# ------------------------------------------------------------------


async def test_cas_concurrent_exactly_one_wins(pool):
    """Under concurrent CAS from two tasks, exactly one succeeds and one raises."""
    from butlers.core.state import CASConflictError, state_compare_and_set, state_set

    key = f"cas_concurrent:{uuid.uuid4().hex}"
    await state_set(pool, key, "initial")  # version = 1

    results: list[int | Exception] = []

    async def _attempt(value: str) -> None:
        try:
            v = await state_compare_and_set(pool, key, expected_version=1, new_value=value)
            results.append(v)
        except CASConflictError as e:
            results.append(e)

    # Fire both attempts concurrently
    await asyncio.gather(_attempt("writer_a"), _attempt("writer_b"))

    successes = [r for r in results if isinstance(r, int)]
    failures = [r for r in results if isinstance(r, CASConflictError)]

    assert len(successes) == 1, f"Expected exactly 1 success, got: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got: {results}"
    assert successes[0] == 2  # new version after successful CAS
