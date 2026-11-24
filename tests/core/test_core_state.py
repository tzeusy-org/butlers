"""Tests for butlers.core.state — JSONB key-value state store — condensed."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from butlers.core.state import CASConflictError

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# state_get / state_set / state_delete
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_state_get_set_delete(db_pool):
    """state_get returns stored value; missing returns None; delete removes key."""
    from butlers.core.state import state_delete, state_get, state_set

    assert await state_get(db_pool, "nonexistent") is None

    await state_set(db_pool, "key1", {"a": 1})
    assert await state_get(db_pool, "key1") == {"a": 1}

    # Update
    await state_set(db_pool, "key1", "updated")
    assert await state_get(db_pool, "key1") == "updated"

    # Delete
    await state_delete(db_pool, "key1")
    assert await state_get(db_pool, "key1") is None

    # Delete missing key is noop
    await state_delete(db_pool, "key1")  # should not raise


@_asyncio_session
async def test_state_set_version_tracking(db_pool):
    """state_set increments version on update; first insert is version 1."""
    from butlers.core.state import state_set

    v1 = await state_set(db_pool, "versioned", "first")
    assert v1 == 1

    v2 = await state_set(db_pool, "versioned", "second")
    assert v2 == 2

    v3 = await state_set(db_pool, "versioned", "third")
    assert v3 == 3


@_asyncio_session
async def test_state_list(db_pool):
    """state_list returns all entries; prefix filter works."""
    from butlers.core.state import state_list, state_set

    await state_set(db_pool, "app.x", 1)
    await state_set(db_pool, "app.y", 2)
    await state_set(db_pool, "other.z", 3)

    all_entries = await state_list(db_pool)
    assert len(all_entries) >= 3

    app_entries = await state_list(db_pool, prefix="app.")
    # state_list returns key strings by default (keys_only=True)
    assert all(e.startswith("app.") for e in app_entries)
    assert len(app_entries) == 2


@_asyncio_session
async def test_state_list_prefix_with_wildcard_is_not_widened(db_pool):
    """A prefix containing % is treated as a literal, not a LIKE wildcard.

    Regression guard for bu-76w4b: without escape_like_pattern(), a prefix
    like "app%" would match every key (% wildcard), leaking keys that don't
    actually start with "app%".
    """
    from butlers.core.state import state_list, state_set

    # Two keys that do NOT start with the literal "app%"
    await state_set(db_pool, "app.x", 1)
    await state_set(db_pool, "other.z", 2)

    # A key that DOES start with the literal "app%"
    await state_set(db_pool, "app%suffix", 3)

    results = await state_list(db_pool, prefix="app%")
    # Only the key that literally starts with "app%" should be returned.
    assert results == ["app%suffix"]


# ---------------------------------------------------------------------------
# CAS (compare-and-swap)
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_state_cas_success_and_failure(db_pool):
    """CAS succeeds on matching version; fails on mismatch; no-op on missing key."""
    from butlers.core.state import state_compare_and_set, state_set

    v1 = await state_set(db_pool, "cas-key", "initial")
    new_v = await state_compare_and_set(db_pool, "cas-key", v1, "updated")
    assert new_v == v1 + 1

    # Wrong version fails
    with pytest.raises(CASConflictError):
        await state_compare_and_set(db_pool, "cas-key", v1, "bad")

    # Missing key fails
    with pytest.raises(CASConflictError):
        await state_compare_and_set(db_pool, "no-such-key", 1, "val")


@_asyncio_session
async def test_state_cas_concurrent_exactly_one_wins(db_pool):
    """Concurrent CAS on same key: exactly one succeeds."""
    from butlers.core.state import state_compare_and_set, state_set

    v = await state_set(db_pool, "race-key", "initial")

    results = await asyncio.gather(
        state_compare_and_set(db_pool, "race-key", v, "winner-a"),
        state_compare_and_set(db_pool, "race-key", v, "winner-b"),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, int)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# state_claim_if_changed (bu-317s5 review remediation: atomic dedup claim
# for publish_domain_event_once)
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_state_claim_if_changed_wins_on_absent_key(db_pool):
    """Claiming a key that doesn't exist yet wins."""
    from butlers.core.state import state_claim_if_changed, state_get

    won = await state_claim_if_changed(db_pool, "claim-key", "value-a")
    assert won is True
    assert await state_get(db_pool, "claim-key") == "value-a"


@_asyncio_session
async def test_state_claim_if_changed_loses_on_same_value(db_pool):
    """Re-claiming the same value that's already stored loses (no-op)."""
    from butlers.core.state import state_claim_if_changed

    assert await state_claim_if_changed(db_pool, "claim-key", "value-a") is True
    assert await state_claim_if_changed(db_pool, "claim-key", "value-a") is False


@_asyncio_session
async def test_state_claim_if_changed_wins_on_different_value(db_pool):
    """Claiming a new, different value wins even though the key already exists."""
    from butlers.core.state import state_claim_if_changed, state_get

    assert await state_claim_if_changed(db_pool, "claim-key", "value-a") is True
    won = await state_claim_if_changed(db_pool, "claim-key", "value-b")
    assert won is True
    assert await state_get(db_pool, "claim-key") == "value-b"


@_asyncio_session
async def test_state_claim_if_changed_concurrent_same_value_exactly_one_wins(db_pool):
    """Two overlapping claims for the same (key, value) pair: exactly one wins.

    This is the primitive publish_domain_event_once's atomic dedup claim
    relies on to close the check-then-act race two overlapping invocations
    used to hit (both read the pre-update value, both published).
    """
    from butlers.core.state import state_claim_if_changed

    results = await asyncio.gather(
        state_claim_if_changed(db_pool, "race-claim-key", "trip-race"),
        state_claim_if_changed(db_pool, "race-claim-key", "trip-race"),
    )
    assert sorted(results) == [False, True]


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
