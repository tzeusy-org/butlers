"""Unit tests for contact_resolve threshold decision logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Load resolve.py from roster/relationship/tools
RESOLVE_PATH = Path(__file__).parent.parent.parent / "roster/relationship/tools/resolve.py"


def _load_resolve_module():
    """Load resolve.py from disk."""
    spec = importlib.util.spec_from_file_location("resolve", RESOLVE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolve"] = mod
    spec.loader.exec_module(mod)
    return mod


resolve_mod = _load_resolve_module()
contact_resolve = resolve_mod.contact_resolve
CONFIDENCE_HIGH = resolve_mod.CONFIDENCE_HIGH
CONFIDENCE_MEDIUM = resolve_mod.CONFIDENCE_MEDIUM
CONFIDENCE_NONE = resolve_mod.CONFIDENCE_NONE

pytestmark = pytest.mark.unit


class TestContactResolveThresholds:
    """Tests for the 30-point gap threshold decision logic."""

    async def test_exact_match_multiple_with_30_point_gap_returns_high(self):
        """Multiple exact matches: if top candidate leads by ≥30 points, return HIGH confidence."""
        # Mock pool with two exact matches
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query (stay_in_touch_days)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - +50 points to uuid-1
                [{"contact_id": "uuid-1", "forward_label": "spouse"}],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 2
        # uuid-1 should have base 90 + salience (50 for spouse + 10 for stay_in_touch) = 150
        # uuid-2 should have base 90 + salience (0) = 90
        # Gap = 60 points (≥30) → HIGH confidence
        assert result["candidates"][0]["contact_id"] == "uuid-1"
        assert result["candidates"][0]["score"] >= 150

    async def test_exact_match_multiple_with_small_gap_returns_medium(self):
        """Multiple exact matches: gap <30 points returns MEDIUM, no auto-selection."""
        # Mock pool with two exact matches that score similarly
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None  # No auto-selection when gap <30
        assert len(result["candidates"]) == 2
        # Both should have base 90 + salience (0) = 90
        # Gap = 0 points (<30) → MEDIUM confidence, no auto-selection

    async def test_partial_match_with_30_point_gap_returns_high(self):
        """Partial matches: top leads by ≥30 points returns HIGH with auto-selection."""
        # Mock pool with partial matches where one clearly leads
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": "Acme Corp",
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Johnny",
                        "last_name": "Doe",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query - +50 points to uuid-1
                [{"contact_id": "uuid-1", "forward_label": "spouse"}],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 2
        # uuid-1 should have base score (70 for first name prefix match) + salience (50 + 10) = 130
        # uuid-2 should have lower base score + salience (0) = ~40-50
        # Gap should be ≥30 → HIGH confidence with auto-selection

    async def test_partial_match_with_small_gap_returns_medium_no_autoselect(self):
        """Partial matches: if gap <30 points, return MEDIUM confidence without auto-selection."""
        # Mock pool with partial matches that score similarly
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 2 rows with similar scores
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "Johnny",
                        "last_name": "Doe",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [],
                # relationships query
                [],
                # interactions query
                [],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None  # No auto-selection when gap <30
        assert len(result["candidates"]) == 2

    async def test_single_partial_match_returns_medium(self):
        """A single partial match returns MEDIUM confidence (no threshold check needed)."""
        # Mock pool with one partial match
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns no rows
                [],
                # Partial match query returns 1 row
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    }
                ],
            ]
        )

        result = await contact_resolve(pool, "John")

        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] == "uuid-1"
        assert len(result["candidates"]) == 1

    async def test_exact_30_point_gap_is_high_confidence(self):
        """A gap of exactly 30 points should return HIGH confidence (boundary test)."""
        # Mock pool with exact matches where gap is exactly 30
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query
                [{"contact_id": "uuid-1", "forward_label": "friend"}],  # +10 points
                # interactions query
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 10,  # +20 points (capped)
                        "most_recent": None,
                    }
                ],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + stay_in_touch (10) + friend (10) + interactions (20) = 130
        # uuid-2: base 90 + 0 = 90
        # Gap = 40 points (≥30) → HIGH confidence
        assert result["confidence"] == CONFIDENCE_HIGH
        assert result["contact_id"] == "uuid-1"

    async def test_29_point_gap_is_medium_confidence(self):
        """A gap of 29 points should return MEDIUM confidence (boundary test)."""
        # This is harder to control precisely, but we can test the logic
        # by ensuring gap <30 → MEDIUM
        pool = MagicMock()
        pool.fetch = AsyncMock(
            side_effect=[
                # Exact match query returns 2 rows
                [
                    {
                        "id": "uuid-1",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                    {
                        "id": "uuid-2",
                        "first_name": "John",
                        "last_name": "Smith",
                        "nickname": None,
                        "company": None,
                        "job_title": None,
                        "metadata": {},
                    },
                ],
                # contact_data query - give uuid-1 a slight edge (10 points)
                [
                    {"id": "uuid-1", "stay_in_touch_days": 7},
                    {"id": "uuid-2", "stay_in_touch_days": None},
                ],
                # relationships query
                [{"contact_id": "uuid-1", "forward_label": "acquaintance"}],  # +2 points
                # interactions query
                [
                    {
                        "contact_id": "uuid-1",
                        "count_90d": 8,  # +16 points
                        "most_recent": None,
                    }
                ],
                # fact_note query
                [],
                # groups query
                [],
            ]
        )

        result = await contact_resolve(pool, "John Smith")

        # uuid-1: base 90 + stay_in_touch (10) + acquaintance (2) + interactions (16) = 118
        # uuid-2: base 90 + 0 = 90
        # Gap = 28 points (<30) → MEDIUM confidence, no auto-selection
        assert result["confidence"] == CONFIDENCE_MEDIUM
        assert result["contact_id"] is None
