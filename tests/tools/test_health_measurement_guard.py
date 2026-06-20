"""Unit tests for the passive-provenance guard in measurement_log.

Verifies that measurement_log rejects calls whose notes field signals
digest/briefing/passive-Telegram provenance (circular self-reinforcement),
and passes through for explicit user and wellness-sourced measurements.

The guard must fire BEFORE any DB access — the pool must never be called
when passive provenance is detected.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _is_passive_provenance — pure function contract
# ---------------------------------------------------------------------------


class TestIsPassiveProvenance:
    """_is_passive_provenance() detects digest/briefing/passive markers."""

    def test_none_notes_returns_false(self) -> None:
        from butlers.tools.health.measurements import _is_passive_provenance

        assert _is_passive_provenance(None) is False

    def test_empty_notes_returns_false(self) -> None:
        from butlers.tools.health.measurements import _is_passive_provenance

        assert _is_passive_provenance("") is False

    @pytest.mark.parametrize(
        "notes",
        [
            "From daily briefing",
            "Extracted from health briefing",
            "Daily health briefing — weight 68 kg",
            "From Telegram digest",
            "Weekly digest notes",
            "health digest summary",
            "passive",
            "passive telegram source",
            "passive ingestion context",
            "From daily summary",
            "Daily summary entry",
            "From weekly summary",
            "health summary report",
            "From health summary",
            "trend report source",
            "From the weekly trend report",
            "BRIEFING",  # case-insensitive
            "DIGEST",
            "PASSIVE",
        ],
    )
    def test_passive_markers_detected(self, notes: str) -> None:
        from butlers.tools.health.measurements import _is_passive_provenance

        assert _is_passive_provenance(notes) is True, f"Expected True for notes={notes!r}"

    @pytest.mark.parametrize(
        "notes",
        [
            "After morning workout",
            "Pre-breakfast measurement",
            "Logged after gym session",
            "Feeling tired today",
            "Morning reading",
            "Doctor's office visit",
            "Self-reported via check-in",
            "Post-run",
            "Manual entry",
            "Scale reading",
        ],
    )
    def test_legitimate_notes_pass_through(self, notes: str) -> None:
        from butlers.tools.health.measurements import _is_passive_provenance

        assert _is_passive_provenance(notes) is False, f"Expected False for notes={notes!r}"


# ---------------------------------------------------------------------------
# measurement_log — guard rejects passive provenance before touching the DB
# ---------------------------------------------------------------------------


class TestMeasurementLogGuard:
    """measurement_log raises ValueError on passive-provenance notes without DB access."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "notes",
        [
            "From daily briefing",
            "Telegram digest",
            "passive",
            "From daily summary",
            "weekly summary",
            "health summary",
            "trend report",
        ],
    )
    async def test_rejects_passive_provenance_notes(self, notes: str) -> None:
        """Raises ValueError for digest/briefing/passive notes — pool must not be touched."""
        from butlers.tools.health.measurements import measurement_log

        pool = AsyncMock()
        with pytest.raises(ValueError, match="passive"):
            await measurement_log(pool, "weight", 68.0, notes=notes)

        # Guard fires before any DB access.
        pool.execute.assert_not_called()
        pool.fetch.assert_not_called()
        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_no_notes(self) -> None:
        """measurement_log passes through when notes is None."""
        from butlers.tools.health.measurements import measurement_log

        fake_id = str(uuid.uuid4())
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        with (
            patch(
                "butlers.tools.health.measurements._get_owner_entity_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value={"id": fake_id}),
            ),
            patch(
                "butlers.tools.health.measurements._get_embedding_engine",
                return_value=MagicMock(),
            ),
        ):
            result = await measurement_log(pool, "weight", 68.0)

        assert result["type"] == "weight"
        assert result["value"] == 68.0

    @pytest.mark.asyncio
    async def test_accepts_explicit_user_notes(self) -> None:
        """measurement_log passes through for explicit user-provided notes."""
        from butlers.tools.health.measurements import measurement_log

        fake_id = str(uuid.uuid4())
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        with (
            patch(
                "butlers.tools.health.measurements._get_owner_entity_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value={"id": fake_id}),
            ),
            patch(
                "butlers.tools.health.measurements._get_embedding_engine",
                return_value=MagicMock(),
            ),
        ):
            result = await measurement_log(pool, "weight", 70.5, notes="Morning weigh-in after gym")

        assert result["type"] == "weight"
        assert result["value"] == 70.5
        assert result["notes"] == "Morning weigh-in after gym"
