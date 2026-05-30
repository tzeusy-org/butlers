"""Tests for butlers.core.state — JSONB key-value state store — condensed."""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with state table cleared between tests.

    Register the JSONB codec so writes match production behaviour
    (``state_set`` passes Python dicts/lists, not pre-serialized JSON strings).
    """
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=5,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE state CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# state_get / state_set / state_delete
# ---------------------------------------------------------------------------


async def test_state_get_set_delete(pool):
    """state_get returns stored value; missing returns None; delete removes key."""
    from butlers.core.state import state_delete, state_get, state_set

    assert await state_get(pool, "nonexistent") is None

    await state_set(pool, "key1", {"a": 1})
    assert await state_get(pool, "key1") == {"a": 1}

    # Update
    await state_set(pool, "key1", "updated")
    assert await state_get(pool, "key1") == "updated"

    # Delete
    await state_delete(pool, "key1")
    assert await state_get(pool, "key1") is None

    # Delete missing key is noop
    await state_delete(pool, "key1")  # should not raise


async def test_state_set_version_tracking(pool):
    """state_set increments version on update; first insert is version 1."""
    from butlers.core.state import state_set

    v1 = await state_set(pool, "versioned", "first")
    assert v1 == 1

    v2 = await state_set(pool, "versioned", "second")
    assert v2 == 2

    v3 = await state_set(pool, "versioned", "third")
    assert v3 == 3


async def test_state_list(pool):
    """state_list returns all entries; prefix filter works."""
    from butlers.core.state import state_list, state_set

    await state_set(pool, "app.x", 1)
    await state_set(pool, "app.y", 2)
    await state_set(pool, "other.z", 3)

    all_entries = await state_list(pool)
    assert len(all_entries) >= 3

    app_entries = await state_list(pool, prefix="app.")
    # state_list returns key strings by default (keys_only=True)
    assert all(e.startswith("app.") for e in app_entries)
    assert len(app_entries) == 2


async def test_state_list_prefix_with_wildcard_is_not_widened(pool):
    """A prefix containing % is treated as a literal, not a LIKE wildcard.

    Regression guard for bu-76w4b: without escape_like_pattern(), a prefix
    like "app%" would match every key (% wildcard), leaking keys that don't
    actually start with "app%".
    """
    from butlers.core.state import state_list, state_set

    # Two keys that do NOT start with the literal "app%"
    await state_set(pool, "app.x", 1)
    await state_set(pool, "other.z", 2)

    # A key that DOES start with the literal "app%"
    await state_set(pool, "app%suffix", 3)

    results = await state_list(pool, prefix="app%")
    # Only the key that literally starts with "app%" should be returned.
    assert results == ["app%suffix"]


# ---------------------------------------------------------------------------
# CAS (compare-and-swap)
# ---------------------------------------------------------------------------


async def test_state_cas_success_and_failure(pool):
    """CAS succeeds on matching version; fails on mismatch; no-op on missing key."""
    from butlers.core.state import state_compare_and_set, state_set

    v1 = await state_set(pool, "cas-key", "initial")
    new_v = await state_compare_and_set(pool, "cas-key", v1, "updated")
    assert new_v == v1 + 1

    # Wrong version fails
    with pytest.raises(Exception):
        await state_compare_and_set(pool, "cas-key", v1, "bad")

    # Missing key fails
    with pytest.raises(Exception):
        await state_compare_and_set(pool, "no-such-key", 1, "val")


async def test_state_cas_concurrent_exactly_one_wins(pool):
    """Concurrent CAS on same key: exactly one succeeds."""
    from butlers.core.state import state_compare_and_set, state_set

    v = await state_set(pool, "race-key", "initial")

    results = await asyncio.gather(
        state_compare_and_set(pool, "race-key", v, "winner-a"),
        state_compare_and_set(pool, "race-key", v, "winner-b"),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, int)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# _decode_jsonb helper
# ---------------------------------------------------------------------------


class TestDecodeJsonb:
    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ({"key": "val"}, {"key": "val"}),  # dict passthrough
            ('{"key": "val"}', '{"key": "val"}'),  # string passthrough (codec decodes JSONB)
            (42, 42),  # int passthrough
            (None, None),  # None passthrough
            ([1, 2], [1, 2]),  # list passthrough
        ],
    )
    def test_decode_jsonb(self, input_val, expected):
        """decode_jsonb is a pass-through since the asyncpg JSONB codec handles decoding."""
        from butlers.core.state import decode_jsonb

        assert decode_jsonb(input_val) == expected

    def test_string_value_passthrough(self):
        """All values are returned as-is; the JSONB codec has already decoded them."""
        from butlers.core.state import decode_jsonb

        # With the JSONB codec, asyncpg returns Python objects directly.
        # decode_jsonb is now a no-op pass-through.
        assert decode_jsonb("any string") == "any string"
        assert decode_jsonb(42) == 42
        assert decode_jsonb(None) is None
