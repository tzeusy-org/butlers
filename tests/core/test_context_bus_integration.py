"""Integration tests for the situational context bus.

Tests set_context(), clear_context(), get_active_context(), and
is_user_in_context() against a real PostgreSQL testcontainer.

Isolation: the shared.user_context table is created inline per-test via the
provisioned_postgres_pool fixture (fresh database per test invocation).

Requires Docker (for the Postgres testcontainer).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# DDL — mirrors alembic/versions/core/core_042_user_context.py (PR #884 / bu-mft2)
# ---------------------------------------------------------------------------

_DDL = """
CREATE SCHEMA IF NOT EXISTS shared;

CREATE TABLE IF NOT EXISTS shared.user_context (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_type   TEXT        NOT NULL,
    value         TEXT,
    set_by_butler TEXT        NOT NULL,
    set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    confidence    REAL        NOT NULL DEFAULT 1.0
                  CHECK (confidence >= 0.0 AND confidence <= 1.0),
    metadata      JSONB,
    superseded_at TIMESTAMPTZ,
    CONSTRAINT uq_user_context_signal_butler
        UNIQUE (signal_type, set_by_butler)
);

CREATE INDEX IF NOT EXISTS idx_user_context_active_signal_type
ON shared.user_context (signal_type)
WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_context_expires_at
ON shared.user_context (expires_at);
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Create a fresh database with the shared.user_context table."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_DDL)
        yield p


# ---------------------------------------------------------------------------
# set_context() integration tests
# ---------------------------------------------------------------------------


class TestSetContext:
    async def test_new_signal_is_inserted(self, pool):
        """set_context() inserts a new row when no existing signal for the key."""
        from butlers.context_bus import set_context

        await set_context(
            pool,
            butler_name="general",
            signal_type="meeting",
            value="standup",
            confidence=0.9,
        )

        row = await pool.fetchrow(
            "SELECT signal_type, value, set_by_butler, confidence, superseded_at "
            "FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        assert row is not None
        assert row["signal_type"] == "meeting"
        assert row["value"] == "standup"
        assert row["set_by_butler"] == "general"
        assert abs(row["confidence"] - 0.9) < 1e-6
        assert row["superseded_at"] is None

    async def test_existing_signal_is_updated(self, pool):
        """set_context() upserts: calling twice updates the existing row."""
        from butlers.context_bus import set_context

        await set_context(pool, butler_name="general", signal_type="meeting", value="first")
        await set_context(pool, butler_name="general", signal_type="meeting", value="second")

        rows = await pool.fetch(
            "SELECT value FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        assert len(rows) == 1
        assert rows[0]["value"] == "second"

    async def test_upsert_clears_superseded_at(self, pool):
        """Re-activating a superseded signal clears superseded_at."""
        from butlers.context_bus import set_context

        # First write
        await set_context(pool, butler_name="general", signal_type="meeting")
        # Manually supersede it
        await pool.execute(
            "UPDATE shared.user_context SET superseded_at = NOW() WHERE signal_type = 'meeting'"
        )
        # Verify it's superseded
        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        assert row["superseded_at"] is not None

        # Re-activate via set_context
        await set_context(pool, butler_name="general", signal_type="meeting", value="re-activated")

        row = await pool.fetchrow(
            "SELECT value, superseded_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        assert row["superseded_at"] is None
        assert row["value"] == "re-activated"

    async def test_invalid_signal_type_raises_value_error(self, pool):
        """set_context() raises ValueError for an unknown signal type."""
        from butlers.context_bus import set_context

        with pytest.raises(ValueError, match="partying"):
            await set_context(
                pool,
                butler_name="general",
                signal_type="partying",
            )

    async def test_invalid_signal_type_does_not_write(self, pool):
        """No database write occurs when the signal type is invalid."""
        from butlers.context_bus import set_context

        try:
            await set_context(pool, butler_name="general", signal_type="invalid_xyz")
        except ValueError:
            pass

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM shared.user_context WHERE signal_type = 'invalid_xyz'"
        )
        assert count == 0

    async def test_unauthorized_butler_raises_permission_error(self, pool):
        """set_context() raises PermissionError for an unauthorised butler."""
        from butlers.context_bus import set_context

        with pytest.raises(PermissionError):
            await set_context(
                pool,
                butler_name="finance",
                signal_type="exercising",
            )

    async def test_unauthorized_butler_does_not_write(self, pool):
        """No database write occurs when the butler lacks permission."""
        from butlers.context_bus import set_context

        try:
            await set_context(pool, butler_name="finance", signal_type="exercising")
        except PermissionError:
            pass

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM shared.user_context "
            "WHERE signal_type = 'exercising' AND set_by_butler = 'finance'"
        )
        assert count == 0

    async def test_ttl_clamped_to_max(self, pool):
        """expires_at is clamped to the signal's max TTL."""
        from butlers.context_bus import set_context

        far_future = datetime.now(tz=UTC) + timedelta(hours=100)  # meeting max is 4h
        await set_context(
            pool,
            butler_name="general",
            signal_type="meeting",
            expires_at=far_future,
        )

        row = await pool.fetchrow(
            "SELECT expires_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        # Should be clamped to ~4 hours from now, not 100 hours
        now = datetime.now(tz=UTC)
        assert row["expires_at"] <= now + timedelta(hours=4, minutes=1)
        assert row["expires_at"] >= now + timedelta(hours=3, minutes=59)

    async def test_default_ttl_applied_when_expires_at_omitted(self, pool):
        """When expires_at is not provided, the default TTL is applied."""
        from butlers.context_bus import set_context

        await set_context(pool, butler_name="general", signal_type="meeting")

        row = await pool.fetchrow(
            "SELECT expires_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        now = datetime.now(tz=UTC)
        # meeting default = 1 hour
        assert row["expires_at"] >= now + timedelta(minutes=55)
        assert row["expires_at"] <= now + timedelta(minutes=65)

    async def test_set_at_updated_on_upsert(self, pool):
        """set_at is updated to the current time on every upsert."""
        import asyncio

        from butlers.context_bus import set_context

        await set_context(pool, butler_name="general", signal_type="meeting", value="first")
        first_row = await pool.fetchrow(
            "SELECT set_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )

        await asyncio.sleep(0.01)  # tiny pause to ensure set_at differs
        await set_context(pool, butler_name="general", signal_type="meeting", value="second")
        second_row = await pool.fetchrow(
            "SELECT set_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )

        assert second_row["set_at"] >= first_row["set_at"]

    async def test_metadata_stored(self, pool):
        """metadata dict is stored as JSONB."""
        import json

        from butlers.context_bus import set_context

        await set_context(
            pool,
            butler_name="travel",
            signal_type="traveling",
            metadata={"destination": "Paris", "mode": "flight"},
        )

        raw = await pool.fetchval(
            "SELECT metadata FROM shared.user_context WHERE signal_type = 'traveling'"
        )
        if isinstance(raw, str):
            parsed = json.loads(raw)
        else:
            parsed = raw
        assert parsed == {"destination": "Paris", "mode": "flight"}


# ---------------------------------------------------------------------------
# clear_context() integration tests
# ---------------------------------------------------------------------------


class TestClearContext:
    async def test_active_signal_is_cleared(self, pool):
        """clear_context() sets superseded_at for an active signal."""
        from butlers.context_bus import clear_context, set_context

        await set_context(pool, butler_name="health", signal_type="exercising")
        await clear_context(pool, butler_name="health", signal_type="exercising")

        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'exercising'"
        )
        assert row["superseded_at"] is not None

    async def test_cleared_signal_not_in_active_context(self, pool):
        """A cleared signal no longer appears in get_active_context() results."""
        from butlers.context_bus import clear_context, get_active_context, set_context

        await set_context(pool, butler_name="health", signal_type="exercising")
        signals = await get_active_context(pool)
        signal_types = [s.signal_type for s in signals]
        assert "exercising" in signal_types

        await clear_context(pool, butler_name="health", signal_type="exercising")
        signals = await get_active_context(pool)
        signal_types = [s.signal_type for s in signals]
        assert "exercising" not in signal_types

    async def test_clearing_another_butlers_signal_is_noop(self, pool):
        """A butler cannot clear a signal set by a different butler."""
        from butlers.context_bus import clear_context, set_context

        await set_context(pool, butler_name="health", signal_type="exercising")
        # general is not a valid writer for exercising, but can attempt to clear
        await clear_context(pool, butler_name="general", signal_type="exercising")

        row = await pool.fetchrow(
            "SELECT superseded_at FROM shared.user_context "
            "WHERE signal_type = 'exercising' AND set_by_butler = 'health'"
        )
        assert row["superseded_at"] is None  # health's signal is unaffected

    async def test_clearing_nonexistent_signal_is_noop(self, pool):
        """Calling clear_context() for a non-existent signal raises no error."""
        from butlers.context_bus import clear_context

        # Should not raise
        await clear_context(pool, butler_name="general", signal_type="meeting")

    async def test_clearing_already_cleared_signal_is_idempotent(self, pool):
        """Clearing an already-cleared signal does not raise and leaves state intact."""
        from butlers.context_bus import clear_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting")
        await clear_context(pool, butler_name="general", signal_type="meeting")
        # Second clear should be a no-op
        await clear_context(pool, butler_name="general", signal_type="meeting")

        rows = await pool.fetch(
            "SELECT superseded_at FROM shared.user_context WHERE signal_type = 'meeting'"
        )
        assert len(rows) == 1
        assert rows[0]["superseded_at"] is not None

    async def test_clear_only_affects_matching_butler(self, pool):
        """clear_context() only clears the row for the specified butler."""
        from butlers.context_bus import clear_context, get_active_context, set_context

        # Two butlers set the same signal type
        await set_context(pool, butler_name="travel", signal_type="traveling")
        await set_context(pool, butler_name="general", signal_type="traveling")

        # Clear only the travel butler's signal
        await clear_context(pool, butler_name="travel", signal_type="traveling")

        signals = await get_active_context(pool)
        active_butlers = {s.set_by_butler for s in signals if s.signal_type == "traveling"}
        assert "general" in active_butlers  # general's signal still active
        assert "travel" not in active_butlers  # travel's signal cleared


# ---------------------------------------------------------------------------
# get_active_context() integration tests
# ---------------------------------------------------------------------------


class TestGetActiveContext:
    async def test_active_signals_returned(self, pool):
        """Active signals are included in the result."""
        from butlers.context_bus import get_active_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.8)
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=1.0)

        signals = await get_active_context(pool)
        signal_types = {s.signal_type for s in signals}
        assert "meeting" in signal_types
        assert "traveling" in signal_types

    async def test_no_active_signals_returns_empty_list(self, pool):
        """When no signals are active, an empty list is returned."""
        from butlers.context_bus import get_active_context

        result = await get_active_context(pool)
        assert result == []

    async def test_expired_signals_excluded(self, pool):
        """Signals with expires_at in the past are excluded."""
        from butlers.context_bus import get_active_context

        # Insert an already-expired signal directly
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        await pool.execute(
            """
            INSERT INTO shared.user_context
                (signal_type, value, set_by_butler, set_at, expires_at, confidence)
            VALUES ('dnd', NULL, 'general', NOW(), $1, 1.0)
            """,
            past,
        )

        result = await get_active_context(pool)
        signal_types = [s.signal_type for s in result]
        assert "dnd" not in signal_types

    async def test_superseded_signals_excluded(self, pool):
        """Signals with superseded_at set are excluded even if not expired."""
        from butlers.context_bus import clear_context, get_active_context, set_context

        await set_context(pool, butler_name="general", signal_type="dnd")
        await clear_context(pool, butler_name="general", signal_type="dnd")

        result = await get_active_context(pool)
        signal_types = [s.signal_type for s in result]
        assert "dnd" not in signal_types

    async def test_ordered_by_confidence_desc(self, pool):
        """Results are ordered by confidence descending."""
        from butlers.context_bus import get_active_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.6)
        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=1.0)
        await set_context(pool, butler_name="health", signal_type="sick", confidence=0.8)

        result = await get_active_context(pool)
        confidences = [s.confidence for s in result]
        assert confidences == sorted(confidences, reverse=True)

    async def test_result_is_list_of_context_entries(self, pool):
        """Return type is a list of ContextEntry dataclass instances."""
        from butlers.context_bus import ContextEntry, get_active_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting")

        result = await get_active_context(pool)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert isinstance(result[0], ContextEntry)

    async def test_context_entry_fields_populated(self, pool):
        """ContextEntry has all expected fields populated."""
        from butlers.context_bus import get_active_context, set_context

        await set_context(
            pool,
            butler_name="general",
            signal_type="meeting",
            value="standup",
            confidence=0.9,
        )

        result = await get_active_context(pool)
        entry = next(e for e in result if e.signal_type == "meeting")

        assert entry.signal_type == "meeting"
        assert entry.value == "standup"
        assert entry.set_by_butler == "general"
        assert abs(entry.confidence - 0.9) < 1e-6
        assert entry.set_at is not None
        assert entry.expires_at is not None

    async def test_multiple_butlers_same_signal_type(self, pool):
        """Multiple butlers can have active signals of the same type."""
        from butlers.context_bus import get_active_context, set_context

        await set_context(pool, butler_name="travel", signal_type="traveling", value="Paris")
        await set_context(pool, butler_name="general", signal_type="traveling", value="London")

        result = await get_active_context(pool)
        traveling = [e for e in result if e.signal_type == "traveling"]
        assert len(traveling) == 2


# ---------------------------------------------------------------------------
# is_user_in_context() integration tests
# ---------------------------------------------------------------------------


class TestIsUserInContext:
    async def test_returns_true_when_active_signal_exists(self, pool):
        """Returns True when there is an active signal meeting the confidence threshold."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="travel", signal_type="traveling", confidence=0.9)

        assert await is_user_in_context(pool, "traveling") is True

    async def test_returns_false_when_no_signal_exists(self, pool):
        """Returns False when there is no active signal of the given type."""
        from butlers.context_bus import is_user_in_context

        assert await is_user_in_context(pool, "traveling") is False

    async def test_returns_false_when_signal_expired(self, pool):
        """Returns False when the signal has expired."""
        from butlers.context_bus import is_user_in_context

        past = datetime.now(tz=UTC) - timedelta(hours=1)
        await pool.execute(
            """
            INSERT INTO shared.user_context
                (signal_type, set_by_butler, set_at, expires_at, confidence)
            VALUES ('traveling', 'travel', NOW(), $1, 0.9)
            """,
            past,
        )

        assert await is_user_in_context(pool, "traveling") is False

    async def test_returns_false_when_signal_superseded(self, pool):
        """Returns False when the signal has been cleared (superseded)."""
        from butlers.context_bus import clear_context, is_user_in_context, set_context

        await set_context(pool, butler_name="travel", signal_type="traveling")
        await clear_context(pool, butler_name="travel", signal_type="traveling")

        assert await is_user_in_context(pool, "traveling") is False

    async def test_low_confidence_signal_filtered_by_default(self, pool):
        """Signal with confidence below default 0.5 threshold returns False."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.3)

        # Default min_confidence = 0.5
        assert await is_user_in_context(pool, "meeting") is False

    async def test_custom_min_confidence_lower_threshold(self, pool):
        """Signal with confidence 0.3 returns True when min_confidence=0.2."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.3)

        assert await is_user_in_context(pool, "meeting", min_confidence=0.2) is True

    async def test_custom_min_confidence_higher_threshold(self, pool):
        """Signal with confidence 0.7 returns False when min_confidence=0.9."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.7)

        assert await is_user_in_context(pool, "meeting", min_confidence=0.9) is False

    async def test_returns_bool_type(self, pool):
        """Return type is strictly bool."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting")
        result = await is_user_in_context(pool, "meeting")
        assert isinstance(result, bool)

    async def test_min_confidence_exactly_at_signal_confidence(self, pool):
        """Signal is included when confidence exactly equals min_confidence."""
        from butlers.context_bus import is_user_in_context, set_context

        await set_context(pool, butler_name="general", signal_type="meeting", confidence=0.5)

        assert await is_user_in_context(pool, "meeting", min_confidence=0.5) is True

    async def test_unknown_signal_type_returns_false(self, pool):
        """Querying a signal type with no rows returns False without error."""
        from butlers.context_bus import is_user_in_context

        result = await is_user_in_context(pool, "nonexistent_signal")
        assert result is False
