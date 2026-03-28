"""Tests for butlers.context_bus — Situational Context Bus.

Covers:
- ContextSignal enum membership and values
- ContextEntry dataclass construction
- _check_write_permission: authorized and unauthorized writers
- _clamp_ttl: default TTL, max TTL clamping, within-bounds pass-through
- set_context: upsert, permission rejection, invalid signal, TTL clamping,
  re-activation of a superseded signal
- clear_context: soft-delete, butler isolation, idempotency
- get_active_context: ordering, exclusion of expired / superseded signals
- is_user_in_context: confidence threshold, absent signal
- format_context_preamble: single/multi signal, no value, empty list

Unit tests (no Docker) use mock pools.  Integration tests use testcontainers.
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
    _confidence_label,
    clear_context,
    format_context_preamble,
    get_active_context,
    is_user_in_context,
    set_context,
)

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.asyncio(loop_scope="session")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _entry(
    signal_type: str = "meeting",
    value: str | None = "standup",
    set_by_butler: str = "general",
    confidence: float = 1.0,
    metadata=None,
) -> ContextEntry:
    now = _now()
    return ContextEntry(
        signal_type=signal_type,
        value=value,
        set_by_butler=set_by_butler,
        set_at=now,
        expires_at=now + timedelta(hours=1),
        confidence=confidence,
        metadata=metadata,
    )


# ===========================================================================
# Unit tests — no Docker required
# ===========================================================================


class TestContextSignalEnum:
    """ContextSignal enum sanity checks."""

    def test_all_eleven_signal_types_present(self):
        values = {s.value for s in ContextSignal}
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
        assert values == expected

    def test_signal_is_str_subclass(self):
        assert isinstance(ContextSignal.traveling, str)

    def test_signal_value_equals_name(self):
        for sig in ContextSignal:
            assert sig.value == sig.name


class TestCheckWritePermission:
    """_check_write_permission enforces the permission mapping."""

    def test_authorized_writer_passes(self):
        # health butler may write exercising
        _check_write_permission("health", ContextSignal.exercising)

    def test_unauthorized_writer_raises(self):
        with pytest.raises(PermissionError, match="finance"):
            _check_write_permission("finance", ContextSignal.exercising)

    def test_general_butler_writes_most_signals(self):
        # Per spec, general is authorized for: traveling, sleeping, meeting, focused,
        # sick, socializing, commuting, at_home, away, dnd — but NOT exercising.
        general_allowed = {
            ContextSignal.traveling,
            ContextSignal.sleeping,
            ContextSignal.meeting,
            ContextSignal.focused,
            ContextSignal.sick,
            ContextSignal.socializing,
            ContextSignal.commuting,
            ContextSignal.at_home,
            ContextSignal.away,
            ContextSignal.dnd,
        }
        for signal in general_allowed:
            _check_write_permission("general", signal)  # must not raise

    def test_general_butler_cannot_write_exercising(self):
        # exercising is restricted to health only
        with pytest.raises(PermissionError):
            _check_write_permission("general", ContextSignal.exercising)

    def test_error_message_includes_signal_type(self):
        with pytest.raises(PermissionError, match="exercising"):
            _check_write_permission("finance", ContextSignal.exercising)

    def test_travel_butler_writes_traveling(self):
        _check_write_permission("travel", ContextSignal.traveling)

    def test_switchboard_writes_dnd(self):
        _check_write_permission("switchboard", ContextSignal.dnd)


class TestClampTtl:
    """_clamp_ttl enforces maximum TTL.

    Note: default-TTL application (when expires_at is None) is handled by
    set_context, not by _clamp_ttl directly.  These tests only cover the
    clamping behaviour for non-None expires_at values.
    """

    def test_within_bounds_passes_through(self):
        set_at = _now()
        requested = set_at + timedelta(hours=2)
        result = _clamp_ttl(ContextSignal.meeting, set_at, requested)
        assert result == requested

    def test_exceeds_max_is_clamped(self):
        set_at = _now()
        requested = set_at + timedelta(hours=10)  # max for meeting is 4h
        result = _clamp_ttl(ContextSignal.meeting, set_at, requested)
        expected_max = set_at + timedelta(hours=4)
        assert abs((result - expected_max).total_seconds()) < 1

    def test_exact_max_passes_through(self):
        set_at = _now()
        requested = set_at + timedelta(hours=4)  # exactly meeting max
        result = _clamp_ttl(ContextSignal.meeting, set_at, requested)
        assert result == requested

    def test_traveling_max_30_days(self):
        set_at = _now()
        # Request 60 days — should clamp to 30
        requested = set_at + timedelta(days=60)
        result = _clamp_ttl(ContextSignal.traveling, set_at, requested)
        expected_max = set_at + timedelta(days=30)
        assert abs((result - expected_max).total_seconds()) < 1


class TestConfidenceLabel:
    """_confidence_label maps floats to human-readable labels."""

    def test_explicit_at_1_0(self):
        assert _confidence_label(1.0) == "explicit"

    def test_high_confidence(self):
        assert _confidence_label(0.9) == "high confidence"
        assert _confidence_label(0.8) == "high confidence"

    def test_medium_confidence(self):
        assert _confidence_label(0.7) == "medium confidence"
        assert _confidence_label(0.5) == "medium confidence"

    def test_low_confidence(self):
        assert _confidence_label(0.4) == "low confidence"
        assert _confidence_label(0.0) == "low confidence"


class TestFormatContextPreamble:
    """format_context_preamble formats signals for LLM prompt injection."""

    def test_empty_list_returns_empty_string(self):
        assert format_context_preamble([]) == ""

    def test_single_signal_with_value(self):
        entry = _entry("traveling", value="Paris", confidence=1.0)
        result = format_context_preamble([entry])
        assert result == "[User Context: traveling (Paris, explicit)]"

    def test_single_signal_without_value(self):
        entry = _entry("dnd", value=None, confidence=1.0)
        result = format_context_preamble([entry])
        assert result == "[User Context: dnd (explicit)]"

    def test_multiple_signals_comma_separated(self):
        entries = [
            _entry("traveling", value="Paris", confidence=1.0),
            _entry("meeting", value="standup", confidence=0.8),
        ]
        result = format_context_preamble(entries)
        expected = "[User Context: traveling (Paris, explicit), meeting (standup, high confidence)]"
        assert result == expected

    def test_medium_confidence_label(self):
        entry = _entry("focused", value=None, confidence=0.6)
        result = format_context_preamble([entry])
        assert "medium confidence" in result

    def test_low_confidence_label(self):
        entry = _entry("sick", value=None, confidence=0.3)
        result = format_context_preamble([entry])
        assert "low confidence" in result


class TestSetContextValidation:
    """set_context raises ValueError/PermissionError without DB access."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_signal_type_raises_value_error(self):
        pool = MagicMock()
        with pytest.raises(ValueError, match="partying"):
            await set_context(pool, butler_name="general", signal_type="partying")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unauthorized_butler_raises_permission_error(self):
        pool = MagicMock()
        with pytest.raises(PermissionError, match="finance"):
            await set_context(pool, butler_name="finance", signal_type="exercising")


class TestClearContextValidation:
    """clear_context is a no-op for unrecognised signal types (no validation)."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_signal_type_is_noop(self):
        pool = MagicMock()
        pool.execute = MagicMock(return_value=None)
        # clear_context does not validate signal vocabulary; unknown types are
        # silently passed to the SQL UPDATE which will match zero rows.
        await clear_context(pool, "general", "partying")
        pool.execute.assert_called_once()


# ===========================================================================
# Integration tests — require Docker + PostgreSQL
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestContextBusIntegration:
    """Full integration tests using a real PostgreSQL via testcontainers."""

    @pytest.fixture(scope="class")
    async def pool(self, postgres_container):
        """Provision a fresh database with shared.user_context and return a pool."""
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

        # Create the shared schema and user_context table
        await p.execute("CREATE SCHEMA IF NOT EXISTS shared")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS shared.user_context (
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

    # Truncate before each test for isolation
    @pytest.fixture(autouse=True)
    async def truncate(self, pool):
        await pool.execute("TRUNCATE shared.user_context")
        yield

    # -----------------------------------------------------------------------
    # set_context
    # -----------------------------------------------------------------------

    async def test_set_context_inserts_new_row(self, pool):
        await set_context(pool, butler_name="health", signal_type="exercising")
        row = await pool.fetchrow(
            "SELECT * FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert row is not None
        assert row["set_by_butler"] == "health"
        assert row["superseded_at"] is None

    async def test_set_context_upserts_existing_signal(self, pool):
        await set_context(pool, butler_name="health", signal_type="exercising", value="run")
        await set_context(pool, butler_name="health", signal_type="exercising", value="swim")
        rows = await pool.fetch(
            "SELECT * FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert len(rows) == 1
        assert rows[0]["value"] == "swim"

    async def test_set_context_clears_superseded_at_on_reactivation(self, pool):
        await set_context(pool, butler_name="health", signal_type="exercising")
        # Manually supersede
        await pool.execute(
            "UPDATE shared.user_context SET superseded_at = now() "
            "WHERE signal_type = 'exercising' AND set_by_butler = 'health'"
        )
        # Re-set should clear superseded_at
        await set_context(pool, butler_name="health", signal_type="exercising")
        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert row["superseded_at"] is None

    async def test_set_context_applies_default_ttl(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting")
        row = await pool.fetchrow(
            "SELECT set_at, expires_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        # Default TTL for meeting is 1 hour
        delta = row["expires_at"] - row["set_at"]
        assert timedelta(minutes=58) < delta <= timedelta(hours=1)

    async def test_set_context_clamps_ttl_to_max(self, pool):
        now = datetime.now(tz=UTC)
        far_future = now + timedelta(hours=100)  # far beyond meeting max (4h)
        await set_context(pool, butler_name="general", signal_type="meeting", expires_at=far_future)
        row = await pool.fetchrow(
            "SELECT set_at, expires_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        delta = row["expires_at"] - row["set_at"]
        assert delta <= timedelta(hours=4) + timedelta(seconds=2)

    async def test_set_context_stores_metadata(self, pool):
        payload = {"location": "gym", "activity": "weights"}
        await set_context(pool, butler_name="health", signal_type="exercising", metadata=payload)
        row = await pool.fetchrow(
            "SELECT metadata FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        raw = row["metadata"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        assert raw == payload

    # -----------------------------------------------------------------------
    # clear_context
    # -----------------------------------------------------------------------

    async def test_clear_context_sets_superseded_at(self, pool):
        await set_context(pool, butler_name="health", signal_type="exercising")
        await clear_context(pool, "health", "exercising")
        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert row["superseded_at"] is not None

    async def test_clear_context_does_not_affect_other_butler(self, pool):
        # health sets exercising; general tries to clear it — should be no-op
        await set_context(pool, butler_name="health", signal_type="exercising")
        await clear_context(pool, "general", "exercising")  # different butler
        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert row["superseded_at"] is None

    async def test_clear_context_nonexistent_is_noop(self, pool):
        # No rows — should not raise
        await clear_context(pool, "health", "exercising")

    async def test_clear_context_idempotent(self, pool):
        await set_context(pool, butler_name="health", signal_type="exercising")
        await clear_context(pool, "health", "exercising")
        await clear_context(pool, "health", "exercising")  # second call — no error

    # -----------------------------------------------------------------------
    # get_active_context
    # -----------------------------------------------------------------------

    async def test_get_active_context_returns_active_signals(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting")
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=1.0)
        results = await get_active_context(pool)
        signal_types = {e.signal_type for e in results}
        assert "meeting" in signal_types
        assert "traveling" in signal_types

    async def test_get_active_context_ordered_by_confidence_desc(self, pool):
        # traveling at confidence 1.0, meeting at 0.8
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=1.0)
        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.8)
        results = await get_active_context(pool)
        confidences = [e.confidence for e in results]
        assert confidences == sorted(confidences, reverse=True)

    async def test_get_active_context_excludes_expired(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting")
        # Manually expire the row
        await pool.execute(
            "UPDATE shared.user_context SET expires_at = now() - interval '1 second' "
            "WHERE signal_type = 'meeting'"
        )
        results = await get_active_context(pool)
        assert not any(e.signal_type == "meeting" for e in results)

    async def test_get_active_context_excludes_superseded(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting")
        await pool.execute(
            "UPDATE shared.user_context SET superseded_at = now() WHERE signal_type = 'meeting'"
        )
        results = await get_active_context(pool)
        assert not any(e.signal_type == "meeting" for e in results)

    async def test_get_active_context_empty_list_when_none(self, pool):
        results = await get_active_context(pool)
        assert results == []

    async def test_get_active_context_returns_context_entries(self, pool):
        await set_context(pool, butler_name="general", signal_type="dnd", value="focus block")
        results = await get_active_context(pool)
        assert len(results) == 1
        entry = results[0]
        assert isinstance(entry, ContextEntry)
        assert entry.signal_type == "dnd"
        assert entry.value == "focus block"
        assert entry.set_by_butler == "general"

    # -----------------------------------------------------------------------
    # is_user_in_context
    # -----------------------------------------------------------------------

    async def test_is_user_in_context_true_when_active(self, pool):
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=0.9)
        result = await is_user_in_context(pool, "traveling")
        assert result is True

    async def test_is_user_in_context_false_when_no_signal(self, pool):
        result = await is_user_in_context(pool, "traveling")
        assert result is False

    async def test_is_user_in_context_filters_low_confidence(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.3)
        result = await is_user_in_context(pool, "meeting", min_confidence=0.5)
        assert result is False

    async def test_is_user_in_context_custom_min_confidence(self, pool):
        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.3)
        result = await is_user_in_context(pool, "meeting", min_confidence=0.2)
        assert result is True

    async def test_is_user_in_context_excludes_expired(self, pool):
        await set_context(pool, butler_name="travel", signal_type="traveling")
        await pool.execute(
            "UPDATE shared.user_context SET expires_at = now() - interval '1 second' "
            "WHERE signal_type = 'traveling'"
        )
        result = await is_user_in_context(pool, "traveling")
        assert result is False

    async def test_is_user_in_context_unknown_signal_returns_false(self, pool):
        # is_user_in_context does not validate signal vocabulary; unknown signal
        # types simply match no rows and return False.
        result = await is_user_in_context(pool, "partying")
        assert result is False
