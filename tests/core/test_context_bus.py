"""Tests for butlers.context_bus — Situational Context Bus — condensed.

Covers:
- ContextSignal enum contract (RFC 0009)
- Write permission enforcement
- TTL clamping per signal type
- set_context / clear_context / get_active_context / is_user_in_context
- format_context_preamble output format
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.context_bus import (
    ContextEntry,
    ContextSignal,
    _check_write_permission,
    _clamp_ttl,
    clear_context,
    format_context_preamble,
    get_active_context,
    is_user_in_context,
    set_context,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _entry(
    signal_type: str = "meeting",
    value: str | None = "standup",
    set_by_butler: str = "general",
    confidence: float = 1.0,
) -> ContextEntry:
    return ContextEntry(
        signal_type=signal_type,
        value=value,
        set_by_butler=set_by_butler,
        set_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        confidence=confidence,
    )


def test_context_signal_and_write_permission_and_ttl():
    """ContextSignal enum; write permission enforcement; TTL clamping per signal type."""
    # ContextSignal enum: all 12 types present; StrEnum semantics; invalid raises
    expected = {
        "traveling",
        "sleeping",
        "meeting",
        "focused",
        "exercising",
        "sick",
        "socializing",
        "commuting",
        "at_home",
        "in_space",
        "away",
        "dnd",
    }
    assert {s.value for s in ContextSignal} == expected
    assert isinstance(ContextSignal.meeting, str)
    assert ContextSignal("traveling") is ContextSignal.traveling
    with pytest.raises(ValueError):
        ContextSignal("partying")

    # Write permission: allowed pairs
    for butler, signal in [
        ("health", "exercising"),
        ("general", "meeting"),
        ("travel", "traveling"),
        ("switchboard", "dnd"),
    ]:
        _check_write_permission(butler, signal)  # must not raise

    # Write permission: denied pairs
    for butler, signal in [
        ("finance", "exercising"),
        ("general", "exercising"),
        ("travel", "exercising"),
    ]:
        with pytest.raises(PermissionError):
            _check_write_permission(butler, signal)

    # TTL clamping: result ≤ signal max
    for signal, max_td in [
        ("meeting", timedelta(hours=4)),
        ("traveling", timedelta(days=30)),
        ("sleeping", timedelta(hours=12)),
        ("commuting", timedelta(hours=3)),
    ]:
        result = _clamp_ttl(signal, _NOW, _NOW + max_td * 2)
        assert abs((result - (_NOW + max_td)).total_seconds()) < 2


def test_format_context_preamble_and_validation():
    """format_context_preamble output; set_context validation without DB."""
    assert format_context_preamble([]) == ""
    assert (
        format_context_preamble([_entry("traveling", value="Paris")])
        == "[User Context: traveling (Paris, explicit)]"
    )
    assert format_context_preamble([_entry("dnd", value=None)]) == "[User Context: dnd (explicit)]"

    entries = [_entry("traveling", "Paris"), _entry("meeting", "standup", confidence=0.8)]
    result = format_context_preamble(entries)
    assert "traveling" in result and "meeting" in result
    assert result.index("traveling") < result.index("meeting")


@pytest.mark.asyncio
async def test_set_context_validation():
    with pytest.raises(ValueError):
        await set_context(MagicMock(), butler_name="general", signal_type="partying")
    with pytest.raises(PermissionError):
        await set_context(MagicMock(), butler_name="finance", signal_type="exercising")


@pytest.mark.asyncio
async def test_dnd_set_requires_stable_mutation_identity() -> None:
    """DND cannot silently fall back to the generic unversioned upsert."""
    pool = MagicMock()

    with pytest.raises(ValueError, match="mutation_id"):
        await set_context(pool, butler_name="general", signal_type="dnd")
    with pytest.raises(ValueError, match="mutation_id"):
        await set_context(
            pool,
            butler_name="general",
            signal_type="dnd",
            mutation_id="raw DND/user text",  # type: ignore[arg-type]
        )

    pool.execute.assert_not_called()
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_dnd_set_uses_atomic_gateway_and_returns_durable_receipt() -> None:
    """Canonical DND writes are delegated to the database receipt boundary."""
    mutation_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    committed_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "mutation_id": mutation_id,
            "generation": 7,
            "writer": "general",
            "operation": "set",
            "correlation": "dnd-action:11111111-1111-1111-1111-111111111111",
            "requested_expires_at": None,
            "effective_expires_at": committed_at + timedelta(hours=2),
            "committed_at": committed_at,
        }
    )

    receipt = await set_context(
        pool,
        butler_name="general",
        signal_type="dnd",
        value="focus time",
        mutation_id=mutation_id,
    )

    assert receipt is not None
    assert receipt.mutation_id == mutation_id
    assert receipt.generation == 7
    assert receipt.correlation_id == "dnd-action:11111111-1111-1111-1111-111111111111"
    args = pool.fetchrow.await_args.args
    assert len(args) == 8
    assert "dnd-action:" not in args[0]
    assert args[1:] == (mutation_id, "general", "set", None, "focus time", 1.0, None)
    assert receipt.operation == "set"
    pool.execute.assert_not_called()
    pool.fetchrow.assert_awaited_once()
    query = pool.fetchrow.await_args.args[0]
    assert "public.context_dnd_mutate" in query
    assert pool.fetchrow.await_args.args[1:4] == (mutation_id, "general", "set")


@pytest.mark.asyncio
async def test_dnd_clear_uses_same_stable_mutation_boundary() -> None:
    mutation_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "mutation_id": mutation_id,
            "generation": 8,
            "writer": "general",
            "operation": "clear",
            "correlation": "dnd-action:22222222-2222-2222-2222-222222222222",
            "requested_expires_at": None,
            "effective_expires_at": None,
            "committed_at": _NOW,
        }
    )

    receipt = await clear_context(
        pool,
        "general",
        "dnd",
        mutation_id=mutation_id,
    )

    assert receipt is not None
    assert receipt.operation == "clear"
    pool.execute.assert_not_called()
    assert receipt.correlation_id == "dnd-action:22222222-2222-2222-2222-222222222222"
    assert pool.fetchrow.await_args.args[2:4] == ("general", "clear")


@pytest.mark.asyncio
async def test_dnd_rejects_a_nonopaque_correlation_returned_by_the_gateway() -> None:
    """A malformed/raw audit correlation never crosses the Python receipt boundary."""
    mutation_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "mutation_id": mutation_id,
            "generation": 9,
            "writer": "general",
            "operation": "set",
            "correlation": "raw DND/user text",
            "requested_expires_at": None,
            "effective_expires_at": _NOW + timedelta(hours=2),
            "committed_at": _NOW,
        }
    )

    with pytest.raises(RuntimeError, match="invalid durable receipt"):
        await set_context(
            pool,
            butler_name="general",
            signal_type="dnd",
            mutation_id=mutation_id,
        )


async def _clear_non_dnd_context(pool: asyncpg.Pool) -> None:
    """Reset fixture-owned context without bypassing the DND table ACL."""
    await pool.execute(
        """
        UPDATE public.user_context
        SET superseded_at = now()
        WHERE signal_type <> 'dnd' AND superseded_at IS NULL
        """
    )


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestContextBusIntegration:
    """Full round-trip tests via testcontainers PostgreSQL."""

    @pytest.fixture
    async def pool(self, migrated_db_url: str):
        """Return an asyncpg pool with user_context table cleared between tests."""
        p = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
        )
        await _clear_non_dnd_context(p)
        yield p
        await p.close()

    async def test_set_clear_get_context(self, pool):
        """set_context inserts/upserts/reactivates; clear_context butler-scoped; get_active_context
        excludes expired/superseded; is_user_in_context filters confidence; metadata persisted."""
        # set_context inserts; upserts value; reactivates after supersede
        await set_context(pool, butler_name="health", signal_type="exercising", value="run")
        row = await pool.fetchrow(
            "SELECT value, superseded_at FROM public.user_context WHERE signal_type = 'exercising'"
        )
        assert row["value"] == "run" and row["superseded_at"] is None

        await set_context(pool, butler_name="health", signal_type="exercising", value="swim")
        rows = await pool.fetch(
            "SELECT * FROM public.user_context WHERE signal_type = 'exercising'"
        )
        assert len(rows) == 1 and rows[0]["value"] == "swim"

        await pool.execute(
            "UPDATE public.user_context SET superseded_at = now() "
            "WHERE signal_type = 'exercising' AND set_by_butler = 'health'"
        )
        await set_context(pool, butler_name="health", signal_type="exercising")
        row2 = await pool.fetchrow(
            "SELECT superseded_at FROM public.user_context WHERE signal_type = 'exercising'"
        )
        assert row2["superseded_at"] is None

        # Metadata persisted
        payload = {"location": "gym", "activity": "weights"}
        await set_context(pool, butler_name="health", signal_type="exercising", metadata=payload)
        raw = (
            await pool.fetchrow(
                "SELECT metadata FROM public.user_context WHERE signal_type = 'exercising'"
            )
        )["metadata"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        assert raw == payload

        # clear_context: different butler noop; correct butler clears
        await clear_context(pool, "general", "exercising")
        assert (
            await pool.fetchrow(
                "SELECT superseded_at FROM public.user_context WHERE signal_type = 'exercising'"
            )
        )["superseded_at"] is None
        await clear_context(pool, "health", "exercising")
        assert (
            await pool.fetchrow(
                "SELECT superseded_at FROM public.user_context WHERE signal_type = 'exercising'"
            )
        )["superseded_at"] is not None

        # get_active_context: excludes expired; is_user_in_context checks confidence
        await _clear_non_dnd_context(pool)
        await set_context(pool, butler_name="general", signal_type="meeting")
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=0.9)

        results = await get_active_context(pool)
        signal_types = {e.signal_type for e in results}
        assert "meeting" in signal_types and "traveling" in signal_types

        await pool.execute(
            "UPDATE public.user_context SET expires_at = now() - interval '1 second' "
            "WHERE signal_type = 'meeting'"
        )
        after = await get_active_context(pool)
        assert not any(e.signal_type == "meeting" for e in after)

        assert await is_user_in_context(pool, "traveling") is True
        assert await is_user_in_context(pool, "traveling", min_confidence=0.95) is False
