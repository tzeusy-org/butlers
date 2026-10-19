"""The day-close writer's parser-only duplicate tolerance (bu-4mfhdq).

The merge now pairs a Codex parser record with its executed capture, but a parser-only
``chronicler_day_close_bundle`` record can still sit beside the executed one whenever the runtime
reports a call the wrapper never executed. The writer must keep ignoring that record as proof,
and must still fail closed on a second outcome-bearing record.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, write_day_close_cache
from tests.chronicler.test_day_close_writer import _canonical_bundle_call

pytestmark = pytest.mark.unit

_TARGET = date(2026, 4, 24)
_RUN_AT = datetime(2026, 4, 25, 1, 5, tzinfo=UTC)


class _Cm:
    async def __aenter__(self) -> AsyncMock:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture()
def fake_pool():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    class _Acquire(_Cm):
        pass

    class _Tx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: object) -> None:
            return None

    acquire = _Acquire()
    acquire.conn = conn
    pool.acquire = MagicMock(return_value=acquire)
    conn.transaction = MagicMock(side_effect=_Tx)
    return pool


@pytest.fixture()
def mock_upsert():
    with patch(
        "butlers.chronicler.day_close_writer.upsert_tier2_cache", new_callable=AsyncMock
    ) as m:
        yield m


def _parser_only_bundle_call() -> dict:
    """A parser record for the canonical tool: no outcome, so it is not execution proof."""
    return {
        "id": "parser-call-1",
        "name": "chronicler_day_close_bundle",
        "input": {"date_label": _TARGET.isoformat(), "timezone": "UTC"},
    }


async def _write(fake_pool, tool_calls: list[dict]):
    result = MagicMock()
    result.success = True
    result.output = "A concise retrospective of the day."
    result.tool_calls = tool_calls
    return await write_day_close_cache(
        fake_pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=_RUN_AT,
        tz="UTC",
        target_date=_TARGET,
    )


async def test_an_executed_capture_beside_a_parser_only_duplicate_is_accepted(
    fake_pool, mock_upsert
) -> None:
    executed = _canonical_bundle_call(_TARGET.isoformat())

    outcome = await _write(fake_pool, [executed, _parser_only_bundle_call()])

    assert outcome is not None
    assert outcome.invalid_reason is None
    assert mock_upsert.await_count == 1
    assert mock_upsert.call_args.kwargs["date_label"] == _TARGET.isoformat()


async def test_the_parser_only_duplicate_may_come_first(fake_pool, mock_upsert) -> None:
    executed = _canonical_bundle_call(_TARGET.isoformat())

    outcome = await _write(fake_pool, [_parser_only_bundle_call(), executed])

    assert outcome is not None
    assert outcome.invalid_reason is None
    assert mock_upsert.await_count == 1


async def test_two_outcome_bearing_captures_fail_closed(fake_pool, mock_upsert) -> None:
    first = _canonical_bundle_call(_TARGET.isoformat())
    second = _canonical_bundle_call(_TARGET.isoformat())

    outcome = await _write(fake_pool, [first, second])

    assert outcome is not None
    assert outcome.invalid_reason == "date_mismatch"


async def test_a_parser_only_record_alone_is_not_proof(fake_pool, mock_upsert) -> None:
    outcome = await _write(fake_pool, [_parser_only_bundle_call()])

    assert outcome is not None
    assert outcome.invalid_reason == "date_mismatch"
