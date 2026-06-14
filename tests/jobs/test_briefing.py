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
    run_finance_briefing_contribution,
    run_health_briefing_contribution,
    today_sgt,
    validate_contribution,
)

pytestmark = pytest.mark.unit

_DATE_2026_03_25 = date(2026, 3, 25)
_DATE_STR_2026_03_25 = "2026-03-25"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_value: dict[str, Any] | None = None,
) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_value)
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


async def test_run_finance_briefing_contribution_avoids_ambiguous_bound_datetime_subtraction():
    """Anomaly SQL must not rely on `$2 - $1`, which triggers asyncpg operator ambiguity."""

    class _RecordingPool:
        def __init__(self) -> None:
            self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []

        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            self.fetch_calls.append((sql, args))
            return []

    pool = _RecordingPool()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", new_callable=AsyncMock),
    ):
        result = await run_finance_briefing_contribution(pool, None)

    anomaly_sql, anomaly_args = pool.fetch_calls[1]
    assert "EXTRACT(DAY FROM $2 - $1)" not in anomaly_sql
    assert "$2 - $1" not in anomaly_sql
    assert len(anomaly_args) == 3
    assert result["spending_anomalies"] == 0


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


# ---------------------------------------------------------------------------
# run_health_briefing_contribution — weight query via search-path scoped facts
# ---------------------------------------------------------------------------


async def test_run_health_briefing_contribution_weight_from_facts_content():
    """Weight is read from public.facts; content field drives the display string.

    measurement_log stores content as "weight: <value> <unit>" (e.g. "weight: 82.5 kg").
    The briefing strips the "<type>: " prefix so the highlight reads "Latest weight: 82.5 kg"
    and the summary reads "Weight: 82.5 kg." — without the redundant type prefix.
    """
    # Realistic fixture: measurement_log writes "weight: 82.5 kg" as content.
    weight_fact = {
        "content": "weight: 82.5 kg",
        "value": "82.5",
        "valid_at": None,
    }
    # fetch() is called twice (missed doses, taken doses); both return empty lists.
    pool = _make_pool(fetch_rows=[], fetchrow_value=weight_fact)
    mock_write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", mock_write),
    ):
        result = await run_health_briefing_contribution(pool, None)

    assert result["butler"] == "health"
    assert result["has_updates"] is True

    # Verify fetchrow was called and the SQL targets the butler-scoped facts table,
    # not the legacy measurements table.
    call_args = pool.fetchrow.call_args
    sql = call_args[0][0]
    assert "FROM facts" in sql
    assert "public.facts" not in sql
    assert "measurement_weight" in sql
    assert "measurements" not in sql
    assert "valid_at IS NOT NULL" in sql
    assert "NULLS LAST" in sql

    # Verify the highlight text strips "weight: " prefix — no double-prefix.
    envelope = mock_write.call_args[0][1]
    weight_highlight = next(h for h in envelope["highlights"] if h["category"] == "weight")
    assert weight_highlight["text"] == "Latest weight: 82.5 kg"
    # Summary must not contain "weight: weight:"
    assert "weight: weight:" not in envelope["summary"].lower()


async def test_run_health_briefing_contribution_weight_fallback_to_metadata():
    """When content is empty the weight text falls back to metadata->>'value' (no unit).

    public.facts.content is NOT NULL, so the realistic "no useful content" scenario
    is an empty string, not NULL. measurement_log stores only {"value": <val>} in
    metadata — there is no "unit" key — so the fallback produces the raw value string.
    """
    weight_fact = {
        "content": "",  # empty string (NOT NULL column); triggers metadata fallback
        "value": "75.0",  # metadata->>'value'; no unit key in measurement_log metadata
        "valid_at": None,
    }
    pool = _make_pool(fetch_rows=[], fetchrow_value=weight_fact)
    mock_write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", mock_write),
    ):
        result = await run_health_briefing_contribution(pool, None)

    assert result["has_updates"] is True
    envelope = mock_write.call_args[0][1]
    weight_highlight = next(h for h in envelope["highlights"] if h["category"] == "weight")
    assert weight_highlight["text"] == "Latest weight: 75.0"


async def test_run_health_briefing_contribution_no_weight_fact():
    """No weight fact in the past 7 days → has_updates is False and no weight highlight."""
    pool = _make_pool(fetch_rows=[], fetchrow_value=None)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", new_callable=AsyncMock),
    ):
        result = await run_health_briefing_contribution(pool, None)

    assert result["butler"] == "health"
    assert result["has_updates"] is False


async def test_run_health_briefing_missed_doses_read_from_facts_not_relational():
    """The missed-doses and taken-doses adherence queries read the facts table.

    Regression guard for bu-i5l99: the butler writes medications/doses as facts
    (predicate='medication'/'took_dose', scope='health'), so the briefing must read
    facts — not the orphaned health.medications / health.medication_doses tables.
    """
    pool = _make_pool(fetch_rows=[], fetchrow_value=None)
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", new_callable=AsyncMock),
    ):
        await run_health_briefing_contribution(pool, None)

    # First fetch() = missed doses; second fetch() = taken doses.
    missed_sql = pool.fetch.call_args_list[0][0][0]
    taken_sql = pool.fetch.call_args_list[1][0][0]

    # Missed-doses query reads medication + took_dose facts, scope-filtered.
    assert "FROM facts" in missed_sql
    assert "predicate = 'medication'" in missed_sql
    assert "predicate = 'took_dose'" in missed_sql
    assert "scope = 'health'" in missed_sql
    assert "metadata->>'medication_id'" in missed_sql
    # No reads of the orphaned relational tables.
    assert "FROM medications" not in missed_sql
    assert "FROM medication_doses" not in missed_sql

    # Taken-doses query counts took_dose facts, not the relational dose table.
    assert "FROM facts" in taken_sql
    assert "predicate = 'took_dose'" in taken_sql
    assert "FROM medication_doses" not in taken_sql


async def test_run_health_briefing_missed_doses_positive_case_from_facts():
    """A medication fact with no dose fact today surfaces a missed-dose highlight."""
    missed_med_row = {
        "name": "Metformin",
        "frequency": "daily",
        "schedule": None,
    }
    # fetch() order: missed_rows -> taken_rows. No weight fact (fetchrow None).
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[[missed_med_row], [{"cnt": 0}]])
    pool.fetchrow = AsyncMock(return_value=None)
    mock_write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DATE_2026_03_25),
        patch("butlers.jobs.briefing._write_contribution", mock_write),
    ):
        result = await run_health_briefing_contribution(pool, None)

    assert result["has_updates"] is True
    assert result["missed_doses"] == 1
    envelope = mock_write.call_args[0][1]
    med_highlight = next(h for h in envelope["highlights"] if h["category"] == "medication")
    assert "Metformin" in med_highlight["text"]
