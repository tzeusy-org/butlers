"""Tests for butlers.jobs.briefing — aggregation job and helpers.

Covers validate_contribution, key helpers, collect_briefing_contributions,
and _get_butler_typed_specialist_butlers. No real database required.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig, ButlerType
from butlers.jobs.briefing import (
    SPECIALIST_BUTLERS,
    _get_butler_typed_specialist_butlers,
    collect_briefing_contributions,
    combined_key,
    contribution_key,
    today_sgt,
    validate_contribution,
)

pytestmark = pytest.mark.unit

_DATE_2026_03_25 = date(2026, 3, 25)
_DATE_STR_2026_03_25 = "2026-03-25"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, fetch_rows: list[dict[str, Any]] | None = None) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchval = AsyncMock(return_value=1)
    pool.execute = AsyncMock()
    return pool


def _make_contribution(
    *,
    butler: str,
    date: str = _DATE_STR_2026_03_25,
    has_updates: bool = True,
    highlights: list[dict[str, str]] | None = None,
    summary: str = "All good.",
) -> dict[str, Any]:
    return {
        "butler": butler,
        "date": date,
        "has_updates": has_updates,
        "highlights": highlights or [],
        "summary": summary,
    }


def _make_view_row(butler: str, contribution: dict[str, Any]) -> dict[str, Any]:
    return {
        "butler": butler,
        "key": f"briefing/daily/{contribution['date']}",
        "value": json.dumps(contribution),
    }


def _make_mock_config(name: str, agent_type: ButlerType) -> MagicMock:
    cfg = MagicMock(spec=ButlerConfig)
    cfg.name = name
    cfg.type = agent_type
    return cfg


# ---------------------------------------------------------------------------
# validate_contribution
# ---------------------------------------------------------------------------


def test_validate_contribution_valid():
    """Valid contributions (with and without updates) parse without error."""
    full = _make_contribution(
        butler="health",
        highlights=[{"category": "medication", "text": "Missed dose", "priority": "high"}],
        summary="Missed 1 dose.",
    )
    result = validate_contribution(full)
    assert result is not None
    assert result["butler"] == "health" and len(result["highlights"]) == 1

    no_updates = _make_contribution(butler="finance", has_updates=False, highlights=[], summary="")
    result2 = validate_contribution(no_updates)
    assert result2["has_updates"] is False and result2["highlights"] == []


@pytest.mark.parametrize(
    "raw, match",
    [
        # Not a dict
        (None, "dict"),
        # Missing required field
        (
            {"date": _DATE_STR_2026_03_25, "has_updates": True, "highlights": [], "summary": ""},
            "'butler'",
        ),
        # Wrong type
        (
            {
                "butler": 123,
                "date": _DATE_STR_2026_03_25,
                "has_updates": True,
                "highlights": [],
                "summary": "",
            },
            "str",
        ),
        (
            {
                "butler": "health",
                "date": _DATE_STR_2026_03_25,
                "has_updates": "yes",
                "highlights": [],
                "summary": "",
            },
            "bool",
        ),
    ],
)
def test_validate_contribution_invalid(raw, match):
    """validate_contribution raises ValueError for non-dict, missing fields, and wrong types."""
    with pytest.raises(ValueError, match=match):
        validate_contribution(raw)


def test_validate_contribution_malformed_highlight():
    """Highlights with missing required fields raise ValueError."""
    raw = _make_contribution(
        butler="health",
        highlights=[{"category": "medication"}],  # missing text and priority
    )
    with pytest.raises(ValueError, match="'text'"):
        validate_contribution(raw)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def test_key_helpers():
    """contribution_key and combined_key produce correct prefixes; today_sgt returns a date."""
    assert contribution_key("2026-03-25") == "briefing/daily/2026-03-25"
    assert combined_key("2026-03-25") == "briefing/combined/2026-03-25"
    result = today_sgt()
    assert isinstance(result, date)
    assert len(result.isoformat().split("-")) == 3


# ---------------------------------------------------------------------------
# collect_briefing_contributions
# ---------------------------------------------------------------------------


async def test_collect_briefing_contributions_counts():
    """All specialists present: contributions_count == len(SPECIALIST_BUTLERS).
    Partial: missing_butlers is populated."""
    date_str = _DATE_STR_2026_03_25

    # All present
    rows = [
        _make_view_row(b, _make_contribution(butler=b, date=date_str)) for b in SPECIALIST_BUTLERS
    ]
    pool = _make_pool(fetch_rows=rows)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
    ):
        result = await collect_briefing_contributions(pool, None)
    assert result["contributions_count"] == len(SPECIALIST_BUTLERS)
    assert result["missing_count"] == 0

    # Partial
    present = ["health", "finance", "relationship"]
    missing = sorted(set(SPECIALIST_BUTLERS) - set(present))
    rows2 = [_make_view_row(b, _make_contribution(butler=b, date=date_str)) for b in present]
    pool2 = _make_pool(fetch_rows=rows2)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
    ):
        result2 = await collect_briefing_contributions(pool2, None)
    assert result2["contributions_count"] == len(present)
    assert sorted(result2["missing_butlers"]) == missing


async def test_collect_briefing_contributions_malformed_skipped():
    """Invalid JSON and butler mismatch rows are skipped; valid row is counted."""
    date_str = _DATE_STR_2026_03_25
    rows = [
        {"butler": "health", "key": f"briefing/daily/{date_str}", "value": "not-json{{"},
        {
            "butler": "home",
            "key": f"briefing/daily/{date_str}",
            "value": json.dumps(_make_contribution(butler="travel", date=date_str)),
        },
        _make_view_row("education", _make_contribution(butler="education", date=date_str)),
    ]
    pool = _make_pool(fetch_rows=rows)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
    ):
        result = await collect_briefing_contributions(pool, None)
    assert result["contributions_count"] == 1


async def test_collect_briefing_contributions_db_error_propagates():
    """DB errors propagate out of collect_briefing_contributions."""
    pool = _make_pool()
    pool.fetch = AsyncMock(side_effect=Exception("database error"))
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
    ):
        with pytest.raises(Exception, match="database error"):
            await collect_briefing_contributions(pool, None)


# ---------------------------------------------------------------------------
# _get_butler_typed_specialist_butlers
# ---------------------------------------------------------------------------


def test_get_butler_typed_specialist_butlers():
    """Excludes staffers; falls back to SPECIALIST_BUTLERS on error or empty roster."""
    mock_configs = [
        _make_mock_config("health", ButlerType.BUTLER),
        _make_mock_config("finance", ButlerType.BUTLER),
        _make_mock_config("travel", ButlerType.STAFFER),
        _make_mock_config("messenger", ButlerType.STAFFER),
    ]
    with patch("butlers.jobs.briefing.list_butlers", return_value=mock_configs):
        result = _get_butler_typed_specialist_butlers()
    assert "health" in result and "finance" in result
    assert "travel" not in result and "messenger" not in result

    with patch("butlers.jobs.briefing.list_butlers", side_effect=RuntimeError("roster not found")):
        assert _get_butler_typed_specialist_butlers() == frozenset(SPECIALIST_BUTLERS)

    with patch("butlers.jobs.briefing.list_butlers", return_value=[]):
        assert _get_butler_typed_specialist_butlers() == frozenset(SPECIALIST_BUTLERS)
