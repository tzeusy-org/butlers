"""Telegram polling offset: Telegram forgets an update once a later offset is sent.

The connector must only send ``offset = last + 1`` for updates it actually
handled. A batch cut short by ``ConnectionError`` (Switchboard down) must be
fetched again, and the heartbeat checkpoint must never run ahead of handling.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors import telegram_bot
from butlers.connectors.filtered_event_buffer import ParkNotDurableError
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.ingestion_policy import PolicyDecision
from tests.connectors.filtered_event_fakes import FilteredEventsPool

_ENDPOINT = "telegram:bot:123456789"


def _update(update_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": 987},
            "chat": {"id": 100},
            "text": f"hello {update_id}",
        },
    }


class _FakeTelegram:
    """MockTransport getUpdates: serves updates with update_id >= offset, like Telegram."""

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.offsets: list[int | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.params.get("offset")
        offset = int(raw) if raw is not None else None
        self.offsets.append(offset)
        batch = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return httpx.Response(200, json={"ok": True, "result": batch}, request=request)


def _connector(telegram: Callable[[httpx.Request], httpx.Response]) -> TelegramBotConnector:
    config = TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_bot",
        endpoint_identity=_ENDPOINT,
        telegram_token="test-token",
    )
    connector = TelegramBotConnector(config, cursor_pool=MagicMock())
    connector._http_client = httpx.AsyncClient(transport=httpx.MockTransport(telegram))
    connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
        return_value=PolicyDecision(action="pass_through")
    )
    connector._save_checkpoint = AsyncMock()  # type: ignore[method-assign]
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]
    return connector


def _failing_on(update_id: int, exc: Exception, calls: list[str]) -> Any:
    """Ingest double that raises ``exc`` for one update id, every time."""

    async def submit(envelope: dict[str, Any]) -> None:
        text = envelope["payload"]["normalized_text"]
        calls.append(text)
        if text == f"hello {update_id}":
            raise exc

    return submit


async def test_connection_error_mid_batch_refetches_the_failed_update() -> None:
    telegram = _FakeTelegram([_update(10), _update(11), _update(12)])
    connector = _connector(telegram)
    delivered: list[str] = []
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        11, ConnectionError("switchboard down"), delivered
    )

    with pytest.raises(ConnectionError):
        await connector._poll_once()

    # Update 10 was handled; 11 failed; 12 was never reached.
    assert delivered == ["hello 10", "hello 11"]

    delivered.clear()
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        999, RuntimeError("unused"), delivered
    )
    await connector._poll_once()

    assert telegram.offsets == [None, 11]
    assert delivered == ["hello 11", "hello 12"]


async def test_connection_error_on_first_update_confirms_nothing() -> None:
    telegram = _FakeTelegram([_update(10), _update(11)])
    connector = _connector(telegram)
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        10, ConnectionError("switchboard down"), []
    )

    with pytest.raises(ConnectionError):
        await connector._poll_once()
    with pytest.raises(ConnectionError):
        await connector._poll_once()

    assert telegram.offsets == [None, None]


async def test_heartbeat_checkpoint_never_runs_ahead_of_handled_updates() -> None:
    telegram = _FakeTelegram([_update(10), _update(11), _update(12)])
    connector = _connector(telegram)
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        11, ConnectionError("switchboard down"), []
    )

    with pytest.raises(ConnectionError):
        await connector._poll_once()

    cursor, _ = connector._get_checkpoint()
    assert cursor is not None
    assert json.loads(cursor) == {"last_update_id": 10}


async def test_poison_update_is_recorded_and_skipped_not_retried_forever() -> None:
    telegram = _FakeTelegram([_update(10), _update(11), _update(12)])
    connector = _connector(telegram)
    connector._db_pool = pool = FilteredEventsPool()  # type: ignore[assignment]
    delivered: list[str] = []
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        11, RuntimeError("bad update"), delivered
    )

    await connector._poll_once()
    await connector._poll_once()

    assert delivered == ["hello 10", "hello 11", "hello 12"]
    assert telegram.offsets == [None, 13]
    rows = pool.written
    assert [row[8] for row in rows] == ["error"]


async def test_clean_batch_advances_offset_past_the_whole_batch() -> None:
    telegram = _FakeTelegram([_update(10), _update(11)])
    connector = _connector(telegram)
    connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]

    await connector._poll_once()
    await connector._poll_once()

    assert telegram.offsets == [None, 12]


async def test_unparkable_poison_update_holds_the_offset_until_it_is_parked() -> None:
    """The offset passes a failed update only once its error row is committed (.274)."""
    telegram = _FakeTelegram([_update(10), _update(11), _update(12)])
    connector = _connector(telegram)
    connector._db_pool = pool = FilteredEventsPool()  # type: ignore[assignment]
    pool.fail_writes = True
    delivered: list[str] = []
    connector._submit_to_ingest = _failing_on(  # type: ignore[method-assign]
        11, RuntimeError("bad update"), delivered
    )

    with pytest.raises(ParkNotDurableError):
        await connector._poll_once()
    assert connector._last_update_id == 10
    assert pool.written == []

    pool.fail_writes = False
    await connector._poll_once()
    assert connector._last_update_id == 12
    assert [row[8] for row in pool.written] == ["error"]


@pytest.mark.parametrize("failures", [1, 1025], ids=["first-error", "extended-outage"])
async def test_polling_recovers_and_keeps_credentials_out_of_retry_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failures: int
) -> None:
    attempts = 0
    sleeps = []

    def get_updates(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.path == "/bottest-token/getUpdates"
        assert request.url.params["offset"] == "43"
        if attempts == failures + 1:
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(503)

    connector = _connector(get_updates)
    connector._last_update_id = 42
    monkeypatch.setattr(connector, "_load_checkpoint", AsyncMock())
    monkeypatch.setattr(connector, "_start_health_server", MagicMock())
    monkeypatch.setattr(connector, "_start_heartbeat", MagicMock())
    monkeypatch.setattr(connector, "_start_owner_delivery_sweep", MagicMock())
    monkeypatch.setattr(telegram_bot, "wait_for_switchboard_ready", AsyncMock())
    monkeypatch.setattr(telegram_bot.random, "random", lambda: 0.75)

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == failures + 2:
            connector._running = False

    monkeypatch.setattr(telegram_bot.asyncio, "sleep", record_sleep)
    async with connector._http_client:
        await connector.start_polling()

    startup_delays = [2, 4, 8, 16, 32, 60]
    expected_delays = startup_delays[:failures] + [60] * max(0, failures - len(startup_delays))
    assert sleeps == pytest.approx([delay * 1.05 for delay in expected_delays] + [1, 2.1])
    assert attempts == failures + 2
    assert connector._last_update_id == 42
    assert connector._consecutive_failures == 1

    assert "test-token" not in caplog.text
    retry_logs = [
        record
        for record in caplog.records
        if record.getMessage() == "Error polling Telegram updates"
    ]
    assert len(retry_logs) == failures + 1
    assert all(
        record.error_type == "http_error" and record.exc_info is None for record in retry_logs
    )
