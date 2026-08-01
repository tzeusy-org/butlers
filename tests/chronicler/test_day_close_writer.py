"""Tests for butlers.chronicler.day_close_writer.

Covers:
- _compute_day_window() returns the correct (day_date, start_at, end_at)
  for a UTC run timestamp.
- _extract_provenance_refs() extracts source_ref values from
  chronicler_list_episodes / chronicler_list_events tool calls.
- _extract_date_label() extracts the structured date_label a
  chronicler_day_close_bundle tool call echoed back.
- write_day_close_cache() writes the expected row to tier2_cache via
  upsert_tier2_cache() (mock the storage function).
- write_day_close_cache() is a no-op for non-success results or empty output.
- write_day_close_cache() records a covered-local-day witness on a successful
  dispatch (independent of output emptiness) but not on a failed one
  (bu-ep4ks.1 / clarify-chronicles-narrative-truth).
- write_day_close_cache() contains an inadmissible-shape or date-mismatched
  candidate rather than rendering it, and never replaces an existing
  admissible row with one.
- Idempotency: calling write_day_close_cache() twice with the same result
  triggers two upsert calls (the storage layer owns idempotency via ON CONFLICT).
- build_day_close_completion_hooks() returns a dict keyed by DAY_CLOSE_TASK_NAME.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    _compute_day_window,
    _extract_date_label,
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


def test_extract_date_label_from_bundle_result() -> None:
    """date_label is read from the bundle result's echoed "date" field."""
    tool_calls = [
        {
            "tool": "chronicler_day_close_bundle",
            "result": {"date": "2026-04-24", "citations": []},
        }
    ]
    assert _extract_date_label(tool_calls) == "2026-04-24"


def test_extract_date_label_none_without_bundle_call() -> None:
    tool_calls = [
        {"tool": "chronicler_list_episodes", "result": {"data": []}},
    ]
    assert _extract_date_label(tool_calls) is None


def test_extract_date_label_handles_json_string_result() -> None:
    import json

    tool_calls = [
        {
            "tool": "chronicler_day_close_bundle",
            "result": json.dumps({"date": "2026-04-24"}),
        }
    ]
    assert _extract_date_label(tool_calls) == "2026-04-24"


# ---------------------------------------------------------------------------
# write_day_close_cache
# ---------------------------------------------------------------------------


class _AcquireCM:
    """Minimal async context manager mimicking asyncpg's ``pool.acquire()``."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _TransactionCM:
    """Minimal async context manager mimicking ``conn.transaction()``."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture()
def fake_pool():
    """A pool double whose acquired connection supports cache transactions."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(side_effect=lambda: _AcquireCM(conn))
    conn.transaction = MagicMock(side_effect=_TransactionCM)
    conn.fetchrow = AsyncMock(return_value=None)
    pool._conn = conn  # exposed for tests asserting on the acquired connection
    return pool


@pytest.fixture()
def mock_upsert():
    with patch(
        "butlers.chronicler.day_close_writer.upsert_tier2_cache",
        new_callable=AsyncMock,
    ) as m:
        yield m


def _bundle_call(date_label: str) -> dict:
    """A ``chronicler_day_close_bundle`` tool-call entry echoing ``date_label``."""
    return {"tool": "chronicler_day_close_bundle", "result": {"date": date_label, "citations": []}}


def _canonical_bundle_call(
    date_label: str,
    *,
    outcome: str = "success",
    result_date: str | None = None,
    citations: list[str] | None = None,
) -> dict:
    """An executed runtime capture for ``chronicler_day_close_bundle``."""
    return {
        "name": "chronicler_day_close_bundle",
        "input": {"date_label": date_label, "timezone": "UTC"},
        "outcome": outcome,
        "result": {"date": result_date or date_label, "citations": citations or []},
    }


def _make_result(
    *,
    success: bool = True,
    output: str | None = "Day summary prose.",
    date_label: str | None = "2026-04-24",
) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.tool_calls = [_bundle_call(date_label)] if date_label else []
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


async def test_write_day_close_cache_admits_one_successful_canonical_bundle_capture(
    fake_pool, mock_upsert
) -> None:
    """A real executed bundle capture admits its matching closed-day prose."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = MagicMock()
    result.success = True
    result.output = "A concise retrospective of the closed day."
    result.tool_calls = [_canonical_bundle_call("2026-04-24")]

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] is None
    assert mock_upsert.call_args.kwargs["date_label"] == "2026-04-24"


@pytest.mark.parametrize(
    "tool_calls",
    [
        [_canonical_bundle_call("2026-04-24", outcome="error")],
        [_canonical_bundle_call("2026-04-24"), _canonical_bundle_call("2026-04-24")],
        [_canonical_bundle_call("2026-04-24", result_date="2026-04-23")],
    ],
    ids=["failed", "ambiguous", "result-date-mismatch"],
)
async def test_write_day_close_cache_contains_invalid_canonical_bundle_capture(
    fake_pool, mock_upsert, tool_calls: list[dict]
) -> None:
    """Only one successful capture with matching input/result can admit prose."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = MagicMock()
    result.success = True
    result.output = "A concise retrospective of the closed day."
    result.tool_calls = tool_calls

    outcome = await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    assert outcome is not None
    assert outcome.invalid_reason == "date_mismatch"
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "date_mismatch"


async def test_write_day_close_cache_keeps_citations_from_canonical_bundle_capture(
    fake_pool, mock_upsert
) -> None:
    """Canonical bundle provenance remains available to the staleness path."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = MagicMock()
    result.success = True
    result.output = "A concise retrospective of the closed day."
    result.tool_calls = [
        _canonical_bundle_call("2026-04-24", citations=["core.sessions:day-close-1"])
    ]

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    assert mock_upsert.call_args.kwargs["provenance_refs"] == ["core.sessions:day-close-1"]


async def test_write_day_close_cache_extracts_provenance(fake_pool, mock_upsert) -> None:
    """Provenance refs are extracted from tool_calls and stored."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()
    result.tool_calls = [
        {
            "tool": "chronicler_list_episodes",
            "result": {"data": [{"source_ref": "core.sessions:abc"}]},
        },
        _bundle_call("2026-04-24"),
    ]

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["provenance_refs"] == ["core.sessions:abc"]
    assert kwargs["invalid_reason"] is None


async def test_write_day_close_cache_noop_when_not_success(fake_pool, mock_upsert) -> None:
    """No upsert when result.success is False, and no coverage witness is
    recorded either — a failed dispatch cannot prove its evidence reads
    completed."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(success=False)

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_not_awaited()
    fake_pool._conn.execute.assert_not_awaited()


async def test_write_day_close_cache_records_coverage_witness(fake_pool, mock_upsert) -> None:
    """A successful dispatch records a covered-local-day witness for the closed day."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result()

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
        tz="UTC",
    )

    assert fake_pool._conn.execute.await_count == 2
    args = fake_pool._conn.execute.await_args_list[0].args
    assert args[1] == date(2026, 4, 24)
    assert args[2] == "UTC"
    lock_sql, lock_key = fake_pool._conn.execute.await_args_list[1].args
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_key == "day_close:2026-04-24"


async def test_write_day_close_cache_contains_inadmissible_shape_candidate(
    fake_pool, mock_upsert
) -> None:
    """A tool-trace-shaped candidate is contained, never rendered."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(output='```json\n{"tool": "chronicler_list_episodes"}\n```')

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "inadmissible_prose"


@pytest.mark.parametrize(
    "output",
    [
        'Tool result: {"date": "2026-04-24", "citations": []}',
        "{'tool': 'chronicler_day_close_bundle', 'result': {'date': '2026-04-24'}}",
        "('tool', {'result': 'raw tool payload'})",
        "set()",
    ],
    ids=["tool-result-header", "python-literal-object", "python-literal-tuple", "empty-set"],
)
async def test_write_day_close_cache_contains_protocol_or_serialized_object_candidate(
    fake_pool, mock_upsert, output: str
) -> None:
    """Machine-shaped output is retained only as contained audit data."""
    result = _make_result(output=output)

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC),
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "inadmissible_prose"


async def test_write_day_close_cache_contains_date_mismatch_candidate(
    fake_pool, mock_upsert
) -> None:
    """Prose whose echoed date_label does not match the closed day is contained."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(date_label="2026-04-23")  # closed day is 2026-04-24

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["invalid_reason"] == "date_mismatch"
    assert kwargs["date_label"] == "2026-04-23"


async def test_write_day_close_cache_missing_date_label_is_date_mismatch(
    fake_pool, mock_upsert
) -> None:
    """No chronicler_day_close_bundle call at all means the binding is unproven."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(date_label=None)

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "date_mismatch"


async def test_write_day_close_cache_invalid_candidate_does_not_clobber_valid_row(
    fake_pool, mock_upsert
) -> None:
    """An invalid candidate SHALL NOT replace an existing admissible cache row."""
    fake_pool._conn.fetchrow = AsyncMock(
        return_value={
            "prose": "A valid earlier retrospective.",
            "date_label": "2026-04-24",
            "invalid_reason": None,
        }
    )
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(date_label="2026-04-23")  # mismatched -> invalid

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_does_not_preserve_legacy_malformed_row_as_valid(
    fake_pool, mock_upsert
) -> None:
    """An unmarked legacy trace cannot block containment of a new invalid candidate."""
    fake_pool._conn.fetchrow = AsyncMock(
        return_value={
            "prose": "tool: chronicler_list_episodes returned []",
            "date_label": "2026-04-24",
            "invalid_reason": None,
        }
    )
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(date_label="2026-04-23")

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "date_mismatch"


async def test_write_day_close_cache_never_logs_rejected_raw_prose(
    fake_pool, mock_upsert, caplog: pytest.LogCaptureFixture
) -> None:
    """Containment logs only a safe reason, never candidate content."""
    raw_prose = "UNSAFE-RAW-CANDIDATE-DO-NOT-LOG"
    fake_pool._conn.fetchrow = AsyncMock(
        return_value={
            "prose": "A valid earlier retrospective.",
            "date_label": "2026-04-24",
            "invalid_reason": None,
        }
    )
    result = MagicMock()
    result.success = True
    result.output = raw_prose
    result.tool_calls = [_canonical_bundle_call("2026-04-24", outcome="error")]
    caplog.set_level(logging.WARNING, logger="butlers.chronicler.day_close_writer")

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC),
    )

    assert raw_prose not in caplog.text
    mock_upsert.assert_not_awaited()


async def test_write_day_close_cache_invalid_candidate_replaces_prior_invalid_row(
    fake_pool, mock_upsert
) -> None:
    """An invalid candidate MAY replace a prior invalid row (still contained, still audited)."""
    fake_pool._conn.fetchrow = AsyncMock(return_value={"invalid_reason": "date_mismatch"})
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    result = _make_result(date_label="2026-04-23")

    await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["invalid_reason"] == "date_mismatch"


async def test_write_day_close_cache_noop_when_output_empty(fake_pool, mock_upsert) -> None:
    """No upsert when output is empty / whitespace-only, but the coverage
    witness IS still recorded: a covered quiet day has no episode, so gating
    the witness on output emptiness would make a genuinely quiet closed day
    indistinguishable from one never chronicled."""
    run_at = datetime(2026, 4, 25, 1, 5, 0, tzinfo=UTC)
    for empty_output in (None, "", "   \n"):
        mock_upsert.reset_mock()
        fake_pool._conn.execute.reset_mock()
        result = _make_result(output=empty_output)
        await write_day_close_cache(
            fake_pool,
            task_name=DAY_CLOSE_TASK_NAME,
            result=result,
            run_at=run_at,
        )
        mock_upsert.assert_not_awaited()
        fake_pool._conn.execute.assert_awaited_once()


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
    result_dict = {
        "success": True,
        "output": "Dict-based prose.",
        "tool_calls": [_bundle_call("2026-04-24")],
    }

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
    result = _make_result(date_label="2026-06-21")

    await hook(task_name=DAY_CLOSE_TASK_NAME, result=result, run_at=run_at)

    mock_upsert.assert_awaited_once()
    assert mock_upsert.call_args.kwargs["cache_key"] == "day_close:2026-06-21"
    assert mock_upsert.call_args.kwargs["start_at"] == datetime(
        2026, 6, 21, 0, 0, 0, tzinfo=ZoneInfo("Asia/Singapore")
    ).astimezone(UTC)
