"""OwnTracks acknowledges a point only once it is ingested or parked (bu-dhloi1.224).

The webhook used to answer ``200 []`` before ingest ran, so the phone dropped
the point from its queue even when the Switchboard submit then failed: the
point was lost with no record. The webhook now waits for processing. A failure
with attempts left answers 503 so the phone keeps the point and sends it again;
once the point's attempts are spent it is parked as a replayable error row
(the exact envelope, idempotency key included) and acknowledged.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from butlers.connectors import owntracks
from butlers.connectors.owntracks import OwnTracksConnector, OwnTracksConnectorConfig
from tests.connectors.filtered_event_fakes import FilteredEventsPool as _FakePool

pytestmark = pytest.mark.unit

_AUTH = {"Authorization": "Bearer test-token"}
_POINT = {"_type": "location", "tst": 1711360400, "tid": "ph", "lat": 1.0, "lon": 2.0}


def _connector(call_tool: AsyncMock, db_pool: Any = None) -> OwnTracksConnector:
    connector = OwnTracksConnector(
        config=OwnTracksConnectorConfig(switchboard_mcp_url="http://test-switchboard/mcp"),
        webhook_token="test-token",
        db_pool=db_pool,
    )
    connector._mcp_client = AsyncMock()
    connector._mcp_client.call_tool = call_tool
    connector._main_loop = asyncio.get_running_loop()
    return connector


async def _post(connector: OwnTracksConnector, body: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=connector._build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://owntracks") as client:
        return await client.post("/owntracks/webhook", json=body, headers=_AUTH)


async def _stop(connector: OwnTracksConnector) -> None:
    for task in connector._flush_drain_tasks.values():
        task.cancel()
    for device in connector._devices.values():
        await device.heartbeat.stop()


async def _always_unreachable(tool: str, payload: dict | None = None) -> dict:
    if tool == "ingest":
        raise ConnectionError("switchboard unreachable")
    return {"status": "accepted"}


def _ingests(call_tool: AsyncMock) -> list[dict]:
    return [call.args[1] for call in call_tool.await_args_list if call.args[0] == "ingest"]


async def test_the_point_is_acknowledged_only_after_it_is_ingested() -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool)
    try:
        response = await _post(connector, _POINT)

        assert response.status_code == 200 and response.json() == []
        assert len(_ingests(call_tool)) == 1  # ingested before the answer
    finally:
        await _stop(connector)


async def test_a_failed_ingest_keeps_the_point_on_the_phone_until_it_is_parked() -> None:
    mock = AsyncMock(side_effect=_always_unreachable)
    pool = _FakePool()
    connector = _connector(mock, pool)
    try:
        # Attempts left: the phone is told to keep the point and send it again.
        for _ in range(connector._ingest_retry.max_attempts - 1):
            response = await _post(connector, _POINT)
            assert response.status_code == 503
        assert pool.written == []

        # Last attempt: parked durably as a replayable error row, then acknowledged.
        response = await _post(connector, _POINT)

        assert response.status_code == 200 and response.json() == []
        attempts = _ingests(mock)
        assert len(attempts) == connector._ingest_retry.max_attempts
        # Every resend carries the same key, so ingest deduplicates any that landed.
        assert len({e["control"]["idempotency_key"] for e in attempts}) == 1
        envelope = attempts[-1]
        (row,) = pool.written  # committed before the 200, not left in the buffer
        assert row[8] == "error"  # status
        stored = row[9]  # full_payload: the exact envelope, for replay
        assert "schema_version" not in stored
        assert stored["control"]["idempotency_key"] == envelope["control"]["idempotency_key"]
        assert stored["event"] == envelope["event"]
        assert row[10] == "ConnectionError"  # error_detail: the class, never the text
        assert len(connector._ingest_retry) == 0
    finally:
        await _stop(connector)


async def test_a_park_that_is_not_written_answers_503_and_parks_on_the_resend() -> None:
    mock = AsyncMock(side_effect=_always_unreachable)
    pool = _FakePool()
    connector = _connector(mock, pool)
    try:
        for _ in range(connector._ingest_retry.max_attempts - 1):
            assert (await _post(connector, _POINT)).status_code == 503

        # Attempts spent, but the park cannot be written: the phone keeps the point.
        pool.fail_writes = True
        assert (await _post(connector, _POINT)).status_code == 503
        assert pool.written == []

        # Once the database is back, a later resend parks it. A failed park
        # restarts the count, which only adds attempts.
        pool.fail_writes = False
        statuses = [
            (await _post(connector, _POINT)).status_code
            for _ in range(connector._ingest_retry.max_attempts)
        ]

        assert statuses[-1] == 200
        assert set(statuses[:-1]) <= {503}
        assert len(pool.written) == 1
    finally:
        await _stop(connector)


async def test_with_no_database_a_spent_point_is_never_acknowledged() -> None:
    connector = _connector(AsyncMock(side_effect=_always_unreachable), db_pool=None)
    try:
        for _ in range(connector._ingest_retry.max_attempts + 1):
            assert (await _post(connector, _POINT)).status_code == 503
    finally:
        await _stop(connector)


async def test_a_slow_replay_drain_does_not_hold_the_answer(monkeypatch) -> None:
    monkeypatch.setattr(owntracks, "_WEBHOOK_PROCESS_TIMEOUT_S", 0.05)
    drain_started = asyncio.Event()

    async def slow_drain(**_: Any) -> None:
        drain_started.set()
        await asyncio.Event().wait()  # never finishes on its own

    monkeypatch.setattr(owntracks, "drain_replay_pending", slow_drain)
    connector = _connector(AsyncMock(return_value={"status": "accepted"}), _FakePool())
    try:
        response = await _post(connector, _POINT)

        assert response.status_code == 200
        await asyncio.wait_for(drain_started.wait(), 1)  # it runs, in the background
    finally:
        await _stop(connector)


async def test_no_main_loop_answers_503_rather_than_dropping_the_point() -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool)
    connector._main_loop = None
    try:
        response = await _post(connector, _POINT)

        assert response.status_code == 503
        assert _ingests(call_tool) == []
    finally:
        await _stop(connector)


async def test_a_slow_ingest_answers_503_without_cancelling_the_ingest(monkeypatch) -> None:
    monkeypatch.setattr(owntracks, "_WEBHOOK_PROCESS_TIMEOUT_S", 0.05)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def call_tool(tool: str, payload: dict | None = None) -> dict:
        if tool == "ingest":
            await release.wait()
            finished.set()
        return {"status": "accepted"}

    connector = _connector(AsyncMock(side_effect=call_tool))
    try:
        response = await _post(connector, _POINT)
        assert response.status_code == 503  # the phone resends; ingest dedupes by key

        release.set()
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        await _stop(connector)
