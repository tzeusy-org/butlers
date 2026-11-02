"""The flush-failure log line carries identifiers only [bu-59x1fi].

When ``_flush_chat_buffer`` fails, the exception message and traceback can echo message text,
including a 777000 login code. The log therefore carries the exception class, chat id, batch size
and raise site, never ``str(exc)`` and never ``exc_info``.
"""

from __future__ import annotations

import logging
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.telegram_user_client import (
    ChatBuffer,
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

_SERVICE = "777000"
_CODE = "".join(("48", "29", "13"))  # bare login code, built at runtime
_ECHO = "".join(("echoed-", "body-", "marker"))  # synthetic message text an exception might echo
_LOGGER = "butlers.connectors.telegram_user_client"


@pytest.fixture
def connector() -> TelegramUserClientConnector:
    config = TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram_user_client:telegram:user123",
    )
    return TelegramUserClientConnector(config, cursor_pool=MagicMock())


def _message(sender_id: int, text: str) -> MagicMock:
    msg = MagicMock()
    msg.id, msg.chat_id, msg.sender_id = 7, sender_id, sender_id
    msg.message, msg.media = text, None
    msg.to_dict = lambda: {"id": 7, "message": text}
    return msg


def _allow() -> SimpleNamespace:
    return SimpleNamespace(allowed=True, action="pass_through", reason="", matched_rule_type=None)


async def _failing_flush(
    connector, *, sender_id: int, text: str, error: Exception | None = None, call_tool=None
) -> None:
    chat = str(sender_id)
    connector._ingestion_policy.evaluate = MagicMock(return_value=_allow())
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=_allow())
    connector._discretion_dispatcher = None
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)
    connector._participant_count_cache[chat] = (2, time.monotonic())
    msg = _message(sender_id, text)
    connector._chat_buffers[chat] = ChatBuffer(messages=[msg])
    connector._fetch_conversation_history = AsyncMock(return_value=[msg])
    connector._resolve_reply_tos = AsyncMock(return_value=[msg])
    if call_tool is None:
        connector._submit_to_ingest = AsyncMock(side_effect=error)
    else:  # drive the REAL _submit_to_ingest through a stubbed MCP call
        connector._mcp_client = MagicMock()
        connector._mcp_client.call_tool = call_tool
    connector._flush_and_drain = AsyncMock()
    connector._save_checkpoint = AsyncMock()
    await connector._flush_chat_buffer(chat)


def _strings(value, depth: int = 0) -> list[str]:
    """Every string reachable from a log-record attribute (extras included)."""
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            t for k, v in value.items() for t in _strings(k, depth + 1) + _strings(v, depth + 1)
        ]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [t for v in value for t in _strings(v, depth + 1)]
    return []


def _assert_nowhere(caplog: pytest.LogCaptureFixture, forbidden: str) -> None:
    """The text is in no string attribute of any record, extras and rendered message included."""
    for record in caplog.records:
        haystack = _strings(record.__dict__) + [record.getMessage()]
        assert not any(forbidden in text for text in haystack), record.getMessage()
    assert forbidden not in caplog.text


def _failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "Failed to flush chat buffer" in r.getMessage()]


def _assert_identifiers_only(records: list[logging.LogRecord], chat: str, forbidden: str) -> None:
    assert len(records) == 1
    record = records[0]
    message = record.getMessage()
    assert record.exc_info is None
    assert record.exc_text is None
    assert forbidden not in message
    assert forbidden not in repr(record.args)
    assert f"chat {chat}" in message
    assert "batch_size=1" in message
    assert "error_class=RuntimeError" in message
    assert re.search(r"raised_at=\w+\.py:\d+:\w+", message)


async def test_service_batch_failure_log_withholds_the_login_code(connector, caplog):
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _failing_flush(
            connector,
            sender_id=int(_SERVICE),
            text=_CODE,
            error=RuntimeError(f"rejected text {_CODE}"),
        )

    _assert_identifiers_only(_failure_records(caplog), _SERVICE, _CODE)
    _assert_nowhere(caplog, _CODE)


async def test_non_service_batch_failure_log_carries_no_exception_message(connector, caplog):
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _failing_flush(
            connector,
            sender_id=1001,
            text="table for four at noon",
            error=RuntimeError(f"rejected body {_ECHO}"),
        )

    _assert_identifiers_only(_failure_records(caplog), "1001", _ECHO)
    _assert_nowhere(caplog, _ECHO)


async def test_failure_still_records_the_error_row(connector, caplog):
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _failing_flush(
            connector, sender_id=1001, text="hello", error=RuntimeError("submit failed")
        )

    assert len(connector._filtered_event_buffer._rows) == 1


async def test_real_submit_failure_log_withholds_an_echoed_ingest_rejection(connector, caplog):
    echoed = f"Invalid ingest.v1 envelope: input_value='{_CODE}'"
    call_tool = AsyncMock(return_value={"status": "error", "error": echoed})

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _failing_flush(connector, sender_id=int(_SERVICE), text=_CODE, call_tool=call_tool)

    call_tool.assert_awaited_once()
    submit_logs = [r for r in caplog.records if "Failed to submit" in r.getMessage()]
    assert len(submit_logs) == 1
    assert submit_logs[0].error_class == "RuntimeError"
    assert re.search(r"\w+\.py:\d+:\w+", submit_logs[0].raised_at)
    assert not hasattr(submit_logs[0], "error")
    _assert_nowhere(caplog, _CODE)
    assert len(_failure_records(caplog)) == 1


async def test_real_submit_exception_log_withholds_the_exception_text(connector, caplog):
    call_tool = AsyncMock(side_effect=ValueError(f"input_value='{_ECHO}'"))

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        await _failing_flush(connector, sender_id=1001, text="hello", call_tool=call_tool)

    _assert_nowhere(caplog, _ECHO)
    assert any(r.getMessage().startswith("Failed to submit") for r in caplog.records)
