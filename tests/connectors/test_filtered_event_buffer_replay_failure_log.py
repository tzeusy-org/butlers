"""Replay-drain failure logs name the failure by identifiers only (bu-mze8g2).

The exception raised by a replay submit (or a payload parse) can echo envelope
content, e.g. a pydantic ``input_value`` returned through the Switchboard tool
error.  The warning must carry the failure class, raise site and row id, never
the exception text, in any attribute of any record.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors import filtered_event_buffer as feb

pytestmark = pytest.mark.unit

MARKER = "synthetic-echoed-body-marker"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in (*value.keys(), *value.values()) for s in _strings(item)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, BaseException):
        return [str(value), *_strings(value.args)]
    return [repr(value)] if value is not None else []


def _assert_nowhere(records: list[logging.LogRecord], needle: str) -> None:
    assert records, "expected the drain to log the failure"
    for record in records:
        for name, value in vars(record).items():
            for text in _strings(value):
                assert needle not in text, f"marker found in record.{name}"
        assert needle not in record.getMessage()
        assert needle not in logging.Formatter().format(record)


def _drain_fixture(full_payload: object):
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 41,
                "received_at": "2026-03-11T10:00:00Z",
                "external_message_id": "msg-1",
                "full_payload": full_payload,
            }
        ]
    )
    conn.transaction = MagicMock(return_value=AsyncMock())
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = ctx
    return pool, conn


def _capturing_logger() -> tuple[logging.Logger, _Capture]:
    log = logging.getLogger("test.replay_failure_log")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    capture = _Capture()
    log.handlers = [capture]
    return log, capture


async def test_replay_submit_failure_logs_no_exception_text() -> None:
    payload = {"event": {"external_event_id": "msg-1"}, "payload": {"normalized_text": "hi"}}
    pool, conn = _drain_fixture(payload)
    log, capture = _capturing_logger()

    async def submit(envelope: dict) -> None:
        raise ValueError(f"Invalid ingest.v1 envelope: input_value='{MARKER}'")

    await feb.drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit, log)

    _assert_nowhere(capture.records, MARKER)
    warning = next(r for r in capture.records if r.levelno == logging.WARNING)
    message = warning.getMessage()
    assert "ValueError" in message
    assert "id=41" in message
    assert conn.execute.await_args.args[1] == "replay_failed"


async def test_replay_parse_failure_logs_no_exception_text(monkeypatch) -> None:
    def failing_loads(_text: str) -> dict:
        raise ValueError(f"cannot parse input_value='{MARKER}'")

    monkeypatch.setattr(feb, "json", SimpleNamespace(loads=failing_loads))
    pool, conn = _drain_fixture("{not-json")
    log, capture = _capturing_logger()
    submit = AsyncMock()

    await feb.drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit, log)

    _assert_nowhere(capture.records, MARKER)
    warning = next(r for r in capture.records if r.levelno == logging.WARNING)
    message = warning.getMessage()
    assert "ValueError" in message
    assert "id=41" in message
    submit.assert_not_awaited()
    assert conn.execute.await_args.args[1] == "replay_failed"


@pytest.mark.parametrize("failing_stage", ["submit", "parse"])
async def test_status_update_failure_after_replay_failure_logs_no_chained_text(
    monkeypatch, failing_stage: str
) -> None:
    """A replay_failed UPDATE that raises chains the replay exception as __context__."""
    if failing_stage == "parse":

        def failing_loads(_text: str) -> dict:
            raise ValueError(f"cannot parse input_value='{MARKER}'")

        monkeypatch.setattr(feb, "json", SimpleNamespace(loads=failing_loads))
        pool, conn = _drain_fixture("{not-json")
    else:
        pool, conn = _drain_fixture(
            {"event": {"external_event_id": "msg-1"}, "payload": {"normalized_text": "hi"}}
        )
    conn.execute = AsyncMock(side_effect=ConnectionError("connection dropped"))
    log, capture = _capturing_logger()

    async def submit(envelope: dict) -> None:
        raise ValueError(f"Invalid ingest.v1 envelope: input_value='{MARKER}'")

    await feb.drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit, log)

    _assert_nowhere(capture.records, MARKER)
    for record in capture.records:
        assert record.exc_info is None
    outer = capture.records[-1]
    assert "ConnectionError" in outer.getMessage()
