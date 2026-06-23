"""Tests for butlers.chronicler.day_close_writer.

Covers:
- _compute_day_window() returns the correct (day_date, start_at, end_at)
  for a UTC run timestamp.
- _extract_provenance_refs() extracts source_ref values from
  chronicler_list_episodes / chronicler_list_events tool calls.
- write_day_close_cache() writes the expected row to tier2_cache via
  upsert_tier2_cache() (mock the storage function).
- write_day_close_cache() is a no-op for non-success results or empty output.
- Idempotency: calling write_day_close_cache() twice with the same result
  triggers two upsert calls (the storage layer owns idempotency via ON CONFLICT).
- build_day_close_completion_hooks() returns a dict keyed by DAY_CLOSE_TASK_NAME.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    _compute_day_window,
    _extract_provenance_refs,
    build_day_close_completion_hooks,
    write_day_close_cache,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _compute_day_window
# ---------------------------------------------------------------------------


def test_compute_day_window_basic() -> None:
    """Run timestamp at 01:05 UTC, tz=UTC → yesterday's window."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    day_date, start_at, end_at = _compute_day_window(run_at, "UTC")

    from datetime import date

    assert day_date == date(2026, 4, 24)
    assert start_at == datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)
    assert end_at == datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)
    assert start_at.tzinfo is UTC
    assert end_at.tzinfo is UTC


def test_compute_day_window_midnight() -> None:
    """Run at exactly midnight UTC, tz=UTC: yesterday = date - 1."""
    run_at = datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)
    day_date, start_at, end_at = _compute_day_window(run_at, "UTC")

    from datetime import date

    assert day_date == date(2026, 4, 24)
    assert start_at == datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)
    assert end_at == datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)


def test_compute_day_window_sgt_fire_closes_previous_local_day() -> None:
    """Regression for #2681: cron fires 01:05 SGT (= 17:05 UTC the *previous* day).

    The day-close cron ``5 1 * * *`` is evaluated in the owner's general timezone
    (Asia/Singapore), so it fires at 01:05 SGT — which is 17:05 UTC on the
    previous UTC calendar day.  Computing "yesterday" off the UTC date would yield
    the day *two* local days before delivery (the reported D+2 bug).  The window
    must be the SGT calendar day immediately before the fire's LOCAL date, so a
    day-close for date D is produced/delivered on D+1 SGT.
    """
    from datetime import date

    tz = ZoneInfo("Asia/Singapore")
    # Fire at 2026-06-22 01:05 SGT == 2026-06-21 17:05 UTC.
    run_at = datetime(2026, 6, 21, 17, 5, 0, tzinfo=UTC)
    day_date, start_at, end_at = _compute_day_window(run_at, tz)

    # Closed day is D = 2026-06-21 (delivered on D+1 = 2026-06-22 SGT), NOT
    # 2026-06-20 (which would be delivered on D+2 = 2026-06-22-as-D... i.e. late).
    assert day_date == date(2026, 6, 21)
    assert start_at == datetime(2026, 6, 21, 0, 0, 0, tzinfo=tz).astimezone(UTC)
    assert end_at == datetime(2026, 6, 22, 0, 0, 0, tzinfo=tz).astimezone(UTC)


def test_compute_day_window_delivery_is_one_local_day_after_closed_day() -> None:
    """Contract: a day-close for D (SGT) is delivered on D + 1 SGT.

    Drives the cron's local fire time (01:05 SGT) across a range of dates and
    asserts the closed ``day_date`` is always exactly one SGT day before the
    fire's local date — never two.
    """
    from datetime import date, timedelta

    tz = ZoneInfo("Asia/Singapore")
    for delivery_local_date in (date(2026, 6, 22), date(2026, 1, 1), date(2026, 3, 1)):
        fire_local = datetime(
            delivery_local_date.year,
            delivery_local_date.month,
            delivery_local_date.day,
            1,
            5,
            tzinfo=tz,
        )
        run_at = fire_local.astimezone(UTC)
        day_date, _start_at, _end_at = _compute_day_window(run_at, tz)
        assert day_date == delivery_local_date - timedelta(days=1)


def test_compute_day_window_string_timezone_accepted() -> None:
    """tz may be passed as an IANA string as well as a ZoneInfo."""
    from datetime import date

    run_at = datetime(2026, 6, 21, 17, 5, 0, tzinfo=UTC)
    day_date, _start, _end = _compute_day_window(run_at, "Asia/Singapore")
    assert day_date == date(2026, 6, 21)


def test_compute_day_window_unknown_timezone_fails_open_to_utc() -> None:
    """An unparseable timezone fails open to UTC rather than wedging dispatch."""
    from datetime import date

    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    day_date, start_at, _end = _compute_day_window(run_at, "Not/AZone")
    assert day_date == date(2026, 4, 24)
    assert start_at == datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _extract_provenance_refs
# ---------------------------------------------------------------------------


def test_extract_provenance_refs_empty() -> None:
    assert _extract_provenance_refs([]) == []


def test_extract_provenance_refs_non_list_tool_calls() -> None:
    """Non-list items in tool_calls are silently skipped."""
    assert _extract_provenance_refs([None, "string", 42]) == []  # type: ignore[list-item]


def test_extract_provenance_refs_ignores_other_tools() -> None:
    """Tool calls for tools other than the list tools are ignored."""
    tool_calls = [
        {
            "tool": "notify",
            "result": {"data": [{"source_ref": "should_not_appear"}]},
        },
        {
            "tool": "chronicler_get_episode",
            "result": {"source_ref": "also_not_extracted"},
        },
    ]
    assert _extract_provenance_refs(tool_calls) == []


def test_extract_provenance_refs_from_episodes_tool() -> None:
    """source_ref values are extracted from chronicler_list_episodes result."""
    tool_calls = [
        {
            "tool": "chronicler_list_episodes",
            "result": {
                "data": [
                    {"source_ref": "core.sessions:abc123"},
                    {"source_ref": "google_calendar.completed:evt456"},
                    {"title": "no ref here"},
                ]
            },
        }
    ]
    refs = _extract_provenance_refs(tool_calls)
    assert refs == ["core.sessions:abc123", "google_calendar.completed:evt456"]


def test_extract_provenance_refs_from_events_tool() -> None:
    """source_ref values are extracted from chronicler_list_events result."""
    tool_calls = [
        {
            "tool": "chronicler_list_events",
            "result": {
                "data": [
                    {"source_ref": "owntracks.points:pt789"},
                ]
            },
        }
    ]
    refs = _extract_provenance_refs(tool_calls)
    assert refs == ["owntracks.points:pt789"]


def test_extract_provenance_refs_from_day_close_bundle_tool() -> None:
    """Bundle citations are internal provenance and do not need prose citations."""
    spotify_ref = (
        "connectors.spotify_listening_sessions:spotify:spotify:tzeusii:session:1778551516835"
    )
    steam_ref = "connectors.steam_play_history:76561198037633688:570:2026-05-12"
    tool_calls = [
        {
            "tool": "chronicler_day_close_bundle",
            "result": {
                "date": "2026-05-12",
                "citations": [spotify_ref, steam_ref],
            },
        }
    ]

    refs = _extract_provenance_refs(tool_calls)
    assert refs == [spotify_ref, steam_ref]


def test_extract_provenance_refs_deduplication() -> None:
    """Duplicate source_refs across calls are deduplicated."""
    tool_calls = [
        {
            "tool": "chronicler_list_episodes",
            "result": {"data": [{"source_ref": "core.sessions:abc"}]},
        },
        {
            "tool": "chronicler_list_events",
            "result": {"data": [{"source_ref": "core.sessions:abc"}]},
        },
    ]
    refs = _extract_provenance_refs(tool_calls)
    assert refs == ["core.sessions:abc"]


def test_extract_provenance_refs_json_string_result() -> None:
    """Result that is a JSON string is decoded before extraction."""
    import json

    result_dict = {"data": [{"source_ref": "spotify.session_summary:s1"}]}
    tool_calls = [
        {
            "tool": "chronicler_list_episodes",
            "result": json.dumps(result_dict),
        }
    ]
    refs = _extract_provenance_refs(tool_calls)
    assert refs == ["spotify.session_summary:s1"]


# ---------------------------------------------------------------------------
# write_day_close_cache
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_pool():
    return MagicMock()


@pytest.fixture()
def mock_upsert():
    with patch(
        "butlers.chronicler.day_close_writer.upsert_tier2_cache",
        new_callable=AsyncMock,
    ) as m:
        yield m


def _make_result(*, success: bool = True, output: str | None = "Day summary prose.") -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.tool_calls = []
    return r


async def test_write_day_close_cache_writes_row(fake_pool, mock_upsert) -> None:
    """Successful dispatch with output writes a tier2_cache row."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["cache_key"] == "day_close:2026-04-24"
    assert kwargs["prose"] == "Day summary prose."
    assert kwargs["start_at"] == datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)
    assert kwargs["end_at"] == datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)
    assert kwargs["provenance_refs"] == []


async def test_write_day_close_cache_extracts_provenance(fake_pool, mock_upsert) -> None:
    """Provenance refs are extracted from tool_calls and stored."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()
    result.tool_calls = [
        {
            "tool": "chronicler_list_episodes",
            "result": {"data": [{"source_ref": "core.sessions:abc"}]},
        }
    ]

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["provenance_refs"] == ["core.sessions:abc"]


async def test_write_day_close_cache_noop_when_not_success(fake_pool, mock_upsert) -> None:
    """No upsert when result.success is False."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(success=False)

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_noop_when_output_empty(fake_pool, mock_upsert) -> None:
    """No upsert when output is empty / whitespace-only."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    for empty_output in (None, "", "   \n"):
        mock_upsert.reset_mock()
        result = _make_result(output=empty_output)
        await write_day_close_cache(
            fake_pool,
            task_name=DAY_CLOSE_TASK_NAME,
            result=result,
            run_at=run_at,
        )
        mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_noop_when_result_is_none(fake_pool, mock_upsert) -> None:
    """No upsert when result is None (dispatch raised before returning)."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=None,
        run_at=run_at,
    )

    mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_noop_wrong_task_name(fake_pool, mock_upsert) -> None:
    """No upsert for task names other than DAY_CLOSE_TASK_NAME."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    await write_day_close_cache(
        fake_pool,
        task_name="chronicler_project_sessions",
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_accepts_dict_result(fake_pool, mock_upsert) -> None:
    """Plain dict results (from deterministic job path) are also handled."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result_dict = {"success": True, "output": "Dict-based prose.", "tool_calls": []}

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result_dict,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["prose"] == "Dict-based prose."


async def test_write_day_close_cache_idempotent_second_call(fake_pool, mock_upsert) -> None:
    """Calling write_day_close_cache twice issues two upsert calls.

    The storage layer owns idempotency via ON CONFLICT; the writer just calls
    upsert regardless.  This test asserts the writer does not add extra
    deduplication that would silently discard second writes.
    """
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    await write_day_close_cache(
        fake_pool, task_name=DAY_CLOSE_TASK_NAME, result=result, run_at=run_at
    )
    await write_day_close_cache(
        fake_pool, task_name=DAY_CLOSE_TASK_NAME, result=result, run_at=run_at
    )

    assert mock_upsert.await_count == 2


async def test_write_day_close_cache_hook_swallows_upsert_error(
    fake_pool, mock_upsert, caplog
) -> None:
    """If upsert_tier2_cache raises, the exception is logged and swallowed."""
    import logging

    mock_upsert.side_effect = RuntimeError("DB connection lost")
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    with caplog.at_level(logging.ERROR, logger="butlers.chronicler.day_close_writer"):
        # Must not raise
        await write_day_close_cache(
            fake_pool,
            task_name=DAY_CLOSE_TASK_NAME,
            result=result,
            run_at=run_at,
        )

    assert any("failed to write tier2_cache" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# build_day_close_completion_hooks
# ---------------------------------------------------------------------------


def test_build_day_close_completion_hooks_returns_correct_key(fake_pool) -> None:
    """build_day_close_completion_hooks returns a dict with DAY_CLOSE_TASK_NAME."""
    hooks = build_day_close_completion_hooks(fake_pool)
    assert isinstance(hooks, dict)
    assert DAY_CLOSE_TASK_NAME in hooks
    assert callable(hooks[DAY_CLOSE_TASK_NAME])


async def test_build_day_close_completion_hooks_hook_delegates(fake_pool, mock_upsert) -> None:
    """The built hook delegates to write_day_close_cache (default tz = UTC)."""
    hooks = build_day_close_completion_hooks(fake_pool)
    hook = hooks[DAY_CLOSE_TASK_NAME]

    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    await hook(task_name=DAY_CLOSE_TASK_NAME, result=result, run_at=run_at)

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["cache_key"] == "day_close:2026-04-24"


async def test_build_day_close_completion_hooks_uses_owner_timezone(fake_pool, mock_upsert) -> None:
    """Regression for #2681: the hook closes the day in the owner's timezone.

    When the daemon binds the owner's general timezone (Asia/Singapore), a cron
    that fires at 01:05 SGT (17:05 UTC the previous day) must cache the closed
    day under the SGT date one local day before delivery — not two.
    """
    hooks = build_day_close_completion_hooks(fake_pool, timezone="Asia/Singapore")
    hook = hooks[DAY_CLOSE_TASK_NAME]

    # 2026-06-22 01:05 SGT == 2026-06-21 17:05 UTC (delivery is 2026-06-22 SGT).
    run_at = datetime(2026, 6, 21, 17, 5, 0, tzinfo=UTC)
    result = _make_result()

    await hook(task_name=DAY_CLOSE_TASK_NAME, result=result, run_at=run_at)

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["cache_key"] == "day_close:2026-06-21"
    assert mock_upsert.call_args.kwargs["start_at"] == datetime(
        2026, 6, 21, 0, 0, 0, tzinfo=ZoneInfo("Asia/Singapore")
    ).astimezone(UTC)
