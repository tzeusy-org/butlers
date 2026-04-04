"""Unit tests for butlers.jobs.briefing — aggregation job and helpers.

Covers:
- validate_contribution: valid/invalid inputs
- today_sgt / contribution_key / combined_key helpers
- collect_briefing_contributions: all specialists, partial, none, malformed

All tests use mocked asyncpg pools — no database required.
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

# Fixed date used for mocking today_sgt()
_DATE_2026_03_25 = date(2026, 3, 25)
_DATE_STR_2026_03_25 = "2026-03-25"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, fetch_rows: list[dict[str, Any]] | None = None) -> MagicMock:
    """Return a minimal mock asyncpg pool suitable for briefing job tests."""
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
    """Simulate a row from general.v_briefing_contributions."""
    return {
        "butler": butler,
        "key": f"briefing/daily/{contribution['date']}",
        "value": json.dumps(contribution),
    }


# ---------------------------------------------------------------------------
# validate_contribution tests
# ---------------------------------------------------------------------------


class TestValidateContribution:
    def test_valid_full_contribution(self):
        raw = _make_contribution(
            butler="health",
            highlights=[{"category": "medication", "text": "Missed dose", "priority": "high"}],
            summary="Missed 1 dose.",
        )
        result = validate_contribution(raw)
        assert result is not None
        assert result["butler"] == "health"
        assert result["has_updates"] is True
        assert len(result["highlights"]) == 1
        assert result["highlights"][0]["category"] == "medication"

    def test_valid_no_updates_contribution(self):
        raw = _make_contribution(butler="finance", has_updates=False, highlights=[], summary="")
        result = validate_contribution(raw)
        assert result is not None
        assert result["has_updates"] is False
        assert result["highlights"] == []
        assert result["summary"] == ""

    def test_missing_butler_field(self):
        raw = {"date": _DATE_STR_2026_03_25, "has_updates": True, "highlights": [], "summary": ""}
        with pytest.raises(ValueError, match="'butler'"):
            validate_contribution(raw)

    def test_missing_date_field(self):
        raw = {"butler": "health", "has_updates": True, "highlights": [], "summary": ""}
        with pytest.raises(ValueError, match="'date'"):
            validate_contribution(raw)

    def test_missing_has_updates_field(self):
        raw = {
            "butler": "health",
            "date": _DATE_STR_2026_03_25,
            "highlights": [],
            "summary": "",
        }
        with pytest.raises(ValueError, match="'has_updates'"):
            validate_contribution(raw)

    def test_not_a_dict(self):
        with pytest.raises(ValueError, match="dict"):
            validate_contribution("not a dict")
        with pytest.raises(ValueError):
            validate_contribution(None)
        with pytest.raises(ValueError):
            validate_contribution(42)
        with pytest.raises(ValueError):
            validate_contribution(["list"])

    def test_butler_not_string(self):
        raw = {
            "butler": 123,
            "date": _DATE_STR_2026_03_25,
            "has_updates": True,
            "highlights": [],
            "summary": "",
        }
        with pytest.raises(ValueError, match="str"):
            validate_contribution(raw)

    def test_has_updates_not_bool(self):
        raw = {
            "butler": "health",
            "date": _DATE_STR_2026_03_25,
            "has_updates": "yes",
            "highlights": [],
            "summary": "",
        }
        with pytest.raises(ValueError, match="bool"):
            validate_contribution(raw)

    def test_highlights_with_malformed_entry_raises(self):
        """Highlights with missing required fields raise ValueError."""
        raw = _make_contribution(
            butler="health",
            highlights=[
                {"category": "medication", "text": "Good", "priority": "low"},
                {"category": "missing_priority"},  # malformed — raises
            ],
        )
        with pytest.raises(ValueError, match="'text'"):
            validate_contribution(raw)

    def test_missing_highlights_raises(self):
        raw = {
            "butler": "health",
            "date": _DATE_STR_2026_03_25,
            "has_updates": False,
            "summary": "",
        }
        with pytest.raises(ValueError, match="'highlights'"):
            validate_contribution(raw)

    def test_missing_summary_raises(self):
        raw = {
            "butler": "health",
            "date": _DATE_STR_2026_03_25,
            "has_updates": False,
            "highlights": [],
        }
        with pytest.raises(ValueError, match="'summary'"):
            validate_contribution(raw)

    def test_empty_highlights_valid(self):
        raw = _make_contribution(butler="health", highlights=[], has_updates=False)
        result = validate_contribution(raw)
        assert result["highlights"] == []

    def test_multiple_highlights_valid(self):
        raw = _make_contribution(
            butler="health",
            highlights=[
                {"category": "a", "text": "first", "priority": "high"},
                {"category": "b", "text": "second", "priority": "low"},
            ],
        )
        result = validate_contribution(raw)
        assert len(result["highlights"]) == 2


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


class TestKeyHelpers:
    def test_contribution_key(self):
        assert contribution_key("2026-03-25") == "briefing/daily/2026-03-25"

    def test_combined_key(self):
        assert combined_key("2026-03-25") == "briefing/combined/2026-03-25"

    def test_today_sgt_returns_date(self):
        result = today_sgt()
        assert isinstance(result, date)
        # Can call isoformat() on it
        iso = result.isoformat()
        parts = iso.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # year


# ---------------------------------------------------------------------------
# collect_briefing_contributions — all specialists present
# ---------------------------------------------------------------------------


class TestCollectBriefingContributionsAllPresent:
    async def test_all_specialists_contribute(self):
        """When all 6 specialists contribute, combined payload contains all 6."""
        date_str = _DATE_STR_2026_03_25
        rows = [
            _make_view_row(b, _make_contribution(butler=b, date=date_str))
            for b in SPECIALIST_BUTLERS
        ]

        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch(
                "butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1
            ) as mock_ss,
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["date"] == date_str
        assert result["contributions_count"] == len(SPECIALIST_BUTLERS)
        assert result["missing_count"] == 0
        assert result["missing_butlers"] == []
        assert result["state_key"] == f"briefing/combined/{date_str}"

        # Verify state_set was called once with the correct key
        mock_ss.assert_awaited_once()
        call_args = mock_ss.call_args
        assert call_args[0][1] == f"briefing/combined/{date_str}"
        payload = call_args[0][2]
        assert payload["date"] == date_str
        assert len(payload["contributions"]) == len(SPECIALIST_BUTLERS)
        assert payload["missing_butlers"] == []
        # Contributions are sorted by butler name
        assert [c["butler"] for c in payload["contributions"]] == sorted(SPECIALIST_BUTLERS)


# ---------------------------------------------------------------------------
# collect_briefing_contributions — partial contributions
# ---------------------------------------------------------------------------


class TestCollectBriefingContributionsPartial:
    async def test_partial_contributions(self):
        """When only 3 of 6 specialists contribute, missing_butlers lists the rest."""
        date_str = _DATE_STR_2026_03_25
        present = ["health", "finance", "relationship"]
        missing = sorted(set(SPECIALIST_BUTLERS) - set(present))

        rows = [_make_view_row(b, _make_contribution(butler=b, date=date_str)) for b in present]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch(
                "butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1
            ) as mock_ss,
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == len(present)
        assert result["missing_count"] == len(missing)
        assert sorted(result["missing_butlers"]) == missing

        payload = mock_ss.call_args[0][2]
        assert len(payload["contributions"]) == len(present)
        assert sorted(payload["missing_butlers"]) == missing


# ---------------------------------------------------------------------------
# collect_briefing_contributions — no contributions
# ---------------------------------------------------------------------------


class TestCollectBriefingContributionsNone:
    async def test_no_contributions(self):
        """When no specialists have contributed, all are listed as missing."""
        date_str = _DATE_STR_2026_03_25
        pool = _make_pool(fetch_rows=[])

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch(
                "butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1
            ) as mock_ss,
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == 0
        assert result["missing_count"] == len(SPECIALIST_BUTLERS)
        assert sorted(result["missing_butlers"]) == sorted(SPECIALIST_BUTLERS)

        payload = mock_ss.call_args[0][2]
        assert payload["contributions"] == []
        assert sorted(payload["missing_butlers"]) == sorted(SPECIALIST_BUTLERS)
        assert payload["date"] == date_str
        assert "generated_at" in payload


# ---------------------------------------------------------------------------
# collect_briefing_contributions — malformed contributions
# ---------------------------------------------------------------------------


class TestCollectBriefingContributionsMalformed:
    async def test_invalid_json_is_skipped(self):
        """A row with non-JSON value is skipped; butler added to missing."""
        date_str = _DATE_STR_2026_03_25
        rows = [
            {"butler": "health", "key": f"briefing/daily/{date_str}", "value": "not-json{{"},
        ]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == 0
        assert "health" in result["missing_butlers"]

    async def test_missing_required_field_is_skipped(self):
        """A row missing 'has_updates' is skipped; butler added to missing."""
        date_str = _DATE_STR_2026_03_25
        bad_contribution = {"butler": "finance", "date": date_str}  # missing has_updates etc.
        rows = [_make_view_row("finance", bad_contribution)]  # type: ignore[arg-type]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == 0
        assert "finance" in result["missing_butlers"]

    async def test_butler_mismatch_is_skipped(self):
        """A contribution whose butler field does not match the view source column is skipped."""
        date_str = _DATE_STR_2026_03_25
        # The view says 'health', but the payload claims 'travel' — mismatch
        contribution = _make_contribution(butler="travel", date=date_str)
        rows = [
            {
                "butler": "health",  # source column says health
                "key": f"briefing/daily/{date_str}",
                "value": json.dumps(contribution),  # payload says travel
            }
        ]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == 0
        assert "health" in result["missing_butlers"]

    async def test_mix_of_valid_and_malformed(self):
        """Valid contributions are included; malformed ones are skipped."""
        date_str = _DATE_STR_2026_03_25
        rows = [
            _make_view_row("health", _make_contribution(butler="health", date=date_str)),
            # malformed: missing has_updates, summary, highlights
            _make_view_row("finance", {"butler": "finance", "date": date_str}),  # type: ignore[arg-type]
            _make_view_row("education", _make_contribution(butler="education", date=date_str)),
        ]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch(
                "butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1
            ) as mock_ss,
        ):
            result = await collect_briefing_contributions(pool, None)

        assert result["contributions_count"] == 2
        assert "finance" in result["missing_butlers"]
        payload = mock_ss.call_args[0][2]
        butler_names = [c["butler"] for c in payload["contributions"]]
        assert "health" in butler_names
        assert "education" in butler_names
        assert "finance" not in butler_names

    async def test_view_query_failure_propagates(self):
        """If querying the view raises, the exception is re-raised."""
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("database error"))

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        ):
            with pytest.raises(Exception, match="database error"):
                await collect_briefing_contributions(pool, None)

    async def test_job_args_are_accepted_and_ignored(self):
        """job_args is accepted but currently unused; should not raise."""
        pool = _make_pool(fetch_rows=[])

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
        ):
            result = await collect_briefing_contributions(pool, {"unused_arg": "value"})

        assert result["contributions_count"] == 0


# ---------------------------------------------------------------------------
# Tests: briefing aggregation filtering (butler-typed only, staffer excluded)
# ---------------------------------------------------------------------------


def _make_mock_config(name: str, agent_type: ButlerType) -> MagicMock:
    """Create a minimal mock ButlerConfig with the given name and type."""
    cfg = MagicMock(spec=ButlerConfig)
    cfg.name = name
    cfg.type = agent_type
    return cfg


class TestBriefingAggregationFiltering:
    """Verify collect_briefing_contributions only aggregates butler-typed agents."""

    def test_get_butler_typed_specialist_butlers_excludes_staffers(self) -> None:
        """_get_butler_typed_specialist_butlers should exclude staffer-typed agents."""
        # Simulate a roster where one specialist is a staffer
        mock_configs = [
            _make_mock_config("health", ButlerType.BUTLER),
            _make_mock_config("finance", ButlerType.BUTLER),
            _make_mock_config("messenger", ButlerType.STAFFER),
            # A hypothetical specialist-as-staffer (future edge case)
            _make_mock_config("travel", ButlerType.STAFFER),
        ]

        with patch("butlers.jobs.briefing.list_butlers", return_value=mock_configs):
            result = _get_butler_typed_specialist_butlers()

        assert "health" in result
        assert "finance" in result
        # travel is in SPECIALIST_BUTLERS but typed as staffer — must be excluded
        assert "travel" not in result
        # messenger is not in SPECIALIST_BUTLERS anyway — must be excluded
        assert "messenger" not in result

    def test_get_butler_typed_specialist_butlers_fallback_on_error(self) -> None:
        """Falls back to SPECIALIST_BUTLERS when list_butlers raises."""
        with patch(
            "butlers.jobs.briefing.list_butlers", side_effect=RuntimeError("roster not found")
        ):
            result = _get_butler_typed_specialist_butlers()

        assert result == frozenset(SPECIALIST_BUTLERS)

    def test_get_butler_typed_specialist_butlers_fallback_on_empty_roster(self) -> None:
        """Falls back to SPECIALIST_BUTLERS when list_butlers returns empty list (no roster dir)."""
        with patch("butlers.jobs.briefing.list_butlers", return_value=[]):
            result = _get_butler_typed_specialist_butlers()

        assert result == frozenset(SPECIALIST_BUTLERS)

    def test_get_butler_typed_specialist_butlers_all_butler_typed(self) -> None:
        """When all roster agents are butler-typed, returns full SPECIALIST_BUTLERS intersection."""
        mock_configs = [_make_mock_config(name, ButlerType.BUTLER) for name in SPECIALIST_BUTLERS]
        mock_configs.append(_make_mock_config("general", ButlerType.BUTLER))

        with patch("butlers.jobs.briefing.list_butlers", return_value=mock_configs):
            result = _get_butler_typed_specialist_butlers()

        assert result == frozenset(SPECIALIST_BUTLERS)

    async def test_collect_contributions_skips_staffer_typed_agent(self) -> None:
        """Contributions from staffer-typed agents in the view are skipped."""
        date_str = _DATE_STR_2026_03_25
        # health is a butler; imagine travel is now typed as a staffer
        mock_configs = [
            _make_mock_config(name, ButlerType.BUTLER)
            for name in SPECIALIST_BUTLERS
            if name != "travel"
        ] + [_make_mock_config("travel", ButlerType.STAFFER)]

        rows = [
            _make_view_row("health", _make_contribution(butler="health", date=date_str)),
            # travel row exists in view but should be skipped since it's a staffer
            _make_view_row("travel", _make_contribution(butler="travel", date=date_str)),
        ]
        pool = _make_pool(fetch_rows=rows)

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.list_butlers", return_value=mock_configs),
            patch(
                "butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1
            ) as mock_ss,
        ):
            result = await collect_briefing_contributions(pool, None)

        # health should be counted; travel (staffer) should be skipped
        assert result["contributions_count"] == 1
        payload = mock_ss.call_args[0][2]
        butler_names = [c["butler"] for c in payload["contributions"]]
        assert "health" in butler_names
        assert "travel" not in butler_names

        # travel must NOT appear in missing_butlers: staffers are excluded from the expected set
        assert "travel" not in result["missing_butlers"]

    async def test_collect_contributions_uses_butler_typed_set_for_missing(self) -> None:
        """missing_butlers list only includes butler-typed agents, not staffers."""
        # All SPECIALIST_BUTLERS are butler-typed; none have contributed today
        mock_configs = [_make_mock_config(name, ButlerType.BUTLER) for name in SPECIALIST_BUTLERS]

        pool = _make_pool(fetch_rows=[])

        with (
            patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
            patch("butlers.jobs.briefing.list_butlers", return_value=mock_configs),
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock, return_value=1),
        ):
            result = await collect_briefing_contributions(pool, None)

        # All specialists missing, none are staffers
        assert result["missing_count"] == len(SPECIALIST_BUTLERS)
        assert sorted(result["missing_butlers"]) == sorted(SPECIALIST_BUTLERS)
