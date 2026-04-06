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
from unittest.mock import MagicMock

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
    # ContextSignal enum: all 11 types present; StrEnum semantics; invalid raises
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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestContextBusIntegration:
    """Full round-trip tests via testcontainers PostgreSQL."""

    @pytest.fixture(scope="class")
    async def pool(self, postgres_container):
        from butlers.db import Database

        db = Database(
            db_name=f"test_{uuid.uuid4().hex[:12]}",
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            min_pool_size=1,
            max_pool_size=5,
        )
        await db.provision()
        p = await db.connect()
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.user_context (
                id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                signal_type   TEXT        NOT NULL,
                value         TEXT,
                set_by_butler TEXT        NOT NULL,
                set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at    TIMESTAMPTZ NOT NULL,
                confidence    REAL        NOT NULL DEFAULT 1.0
                                  CHECK (confidence BETWEEN 0.0 AND 1.0),
                metadata      JSONB,
                superseded_at TIMESTAMPTZ,
                CONSTRAINT uq_user_context_signal_butler
                    UNIQUE (signal_type, set_by_butler)
            )
        """)
        yield p
        await db.close()

    @pytest.fixture(autouse=True)
    async def truncate(self, pool):
        await pool.execute("TRUNCATE public.user_context")
        yield

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
        await pool.execute("TRUNCATE public.user_context")
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
