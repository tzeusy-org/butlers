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
        db_pool=db_pool or _FakePool(),
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


async def test_it_refuses_to_start_without_a_database_pool() -> None:
    """No pool means no evidence journal: it must not serve a webhook that can only answer 503."""
    from butlers.connectors.evidence_journal import EvidenceNotCommitted

    connector = OwnTracksConnector(
        config=OwnTracksConnectorConfig(switchboard_mcp_url="http://test-switchboard/mcp"),
        webhook_token="test-token",
        db_pool=None,
    )
    with pytest.raises(EvidenceNotCommitted) as refused:
        await connector.start()
    assert refused.value.reason == "no_pool"
    assert connector._running is False


async def test_the_runner_exits_when_the_pool_cannot_be_created(monkeypatch) -> None:
    import asyncpg

    async def unreachable(**_kwargs: Any) -> Any:
        raise OSError("synthetic: database down")

    monkeypatch.setattr(asyncpg, "create_pool", unreachable)
    monkeypatch.setattr(owntracks, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(
        owntracks.OwnTracksConnectorConfig,
        "from_env",
        classmethod(lambda cls: cls(switchboard_mcp_url="http://test-switchboard/mcp")),
    )
    with pytest.raises(SystemExit) as exited:
        await owntracks.run_owntracks_connector()
    assert exited.value.code == 1


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


async def test_the_location_row_is_written_before_the_submit(monkeypatch) -> None:
    order: list[str] = []

    async def fake_persist(pool: Any, **kwargs: Any) -> bool:
        order.append("persist")
        return True

    async def call_tool(tool: str, payload: dict | None = None) -> dict:
        if tool == "ingest":
            order.append(tool)
        return {"status": "accepted"}

    monkeypatch.setattr(owntracks, "persist_location_point", fake_persist)
    connector = _connector(AsyncMock(side_effect=call_tool), _FakePool())
    try:
        assert (await _post(connector, _POINT)).status_code == 200
        assert order == ["persist", "ingest"]
    finally:
        await _stop(connector)


async def test_a_failed_location_write_acknowledges_nothing(monkeypatch) -> None:
    async def broken_persist(pool: Any, **kwargs: Any) -> bool:
        raise OSError("connectors database unreachable")

    monkeypatch.setattr(owntracks, "persist_location_point", broken_persist)
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, _FakePool())
    saved = AsyncMock()
    connector._save_checkpoint = saved  # type: ignore[method-assign]
    try:
        response = await _post(connector, _POINT)

        assert response.status_code == 503  # the phone keeps the point
        assert _ingests(call_tool) == []  # nothing submitted
        saved.assert_not_awaited()  # checkpoint unchanged
    finally:
        await _stop(connector)


async def test_a_failed_submit_keeps_the_location_row(monkeypatch) -> None:
    persisted: list[int] = []

    async def fake_persist(pool: Any, **kwargs: Any) -> bool:
        persisted.append(kwargs["tst"])
        return True

    monkeypatch.setattr(owntracks, "persist_location_point", fake_persist)
    connector = _connector(AsyncMock(side_effect=_always_unreachable), _FakePool())
    try:
        assert (await _post(connector, _POINT)).status_code == 503
        assert persisted == [_POINT["tst"]]  # evidence kept although the submit failed
    finally:
        await _stop(connector)


def _asyncpg_exc(name: str) -> Exception:
    import asyncpg.exceptions as pgx

    cls = getattr(pgx, name)
    if name in {"UntranslatableCharacterError", "DataError"}:
        # As the server raises it: SQLSTATE and message fields set.
        return cls.new({"M": "synthetic", "S": "ERROR", "C": cls.sqlstate})
    return cls("synthetic")


class _FailingPool(_FakePool):
    """A pool whose evidence INSERT (``fetchval``) raises ``error``."""

    def __init__(self, error: Exception | None) -> None:
        super().__init__()
        self.error = error
        self.args_seen: list[tuple[Any, ...]] = []

    async def fetchval(self, *args: Any) -> Any:
        self.args_seen.append(args)
        if self.error is not None:
            raise self.error
        return 1


@pytest.mark.parametrize(
    "error_name",
    ["ConnectionDoesNotExistError", "SerializationError", "DeadlockDetectedError",
     "UndefinedTableError", "UndefinedColumnError", "InsufficientPrivilegeError"],
)  # fmt: skip
async def test_transient_schema_and_privilege_errors_answer_503(error_name: str) -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, _FailingPool(_asyncpg_exc(error_name)))
    try:
        assert (await _post(connector, _POINT)).status_code == 503
        assert _ingests(call_tool) == []
    finally:
        await _stop(connector)


async def test_a_connection_error_answers_503() -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, _FailingPool(ConnectionError("down")))
    try:
        assert (await _post(connector, _POINT)).status_code == 503
        assert _ingests(call_tool) == []
    finally:
        await _stop(connector)


@pytest.mark.parametrize("error_name", ["UntranslatableCharacterError", "DataError"])
async def test_a_data_rejected_point_is_acknowledged_not_ingested(error_name: str) -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, _FailingPool(_asyncpg_exc(error_name)))
    recorded: list[tuple[str, str]] = []
    try:
        original = owntracks.ConnectorMetrics.record_error
        owntracks.ConnectorMetrics.record_error = lambda self, t, o: recorded.append((t, o))  # type: ignore[method-assign]
        try:
            response = await _post(connector, _POINT)
        finally:
            owntracks.ConnectorMetrics.record_error = original  # type: ignore[method-assign]
        assert response.status_code == 200 and response.json() == []
        assert _ingests(call_tool) == []
        assert ("point_rejected_data_rejected", "persist_location") in recorded
    finally:
        await _stop(connector)


@pytest.mark.parametrize(
    "patch",
    [
        {"tst": "soon"},
        {"tst": None},
        {"tst": True},
        {"lat": "north"},
        {"lat": 91.0},
        {"acc": "bad"},
    ],
)
async def test_a_malformed_point_is_acknowledged_not_ingested_and_counted(
    patch: dict[str, Any],
) -> None:
    pool = _FailingPool(None)
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, pool)
    recorded: list[str] = []
    original = owntracks.ConnectorMetrics.record_error
    owntracks.ConnectorMetrics.record_error = lambda self, t, o: recorded.append(t)  # type: ignore[method-assign]
    try:
        response = await _post(connector, {**_POINT, **patch})
    finally:
        owntracks.ConnectorMetrics.record_error = original  # type: ignore[method-assign]
        await _stop(connector)
    assert response.status_code == 200 and response.json() == []
    assert _ingests(call_tool) == [] and pool.args_seen == []
    assert recorded and recorded[0].startswith("point_rejected_")


async def test_a_nul_in_free_text_is_stripped_and_the_point_persisted() -> None:
    pool = _FailingPool(None)
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, pool)
    try:
        body = {**_POINT, "SSID": "Cafe\x00Wifi", "inregions": ["Ho\x00me"], "t": "p\x00"}
        assert (await _post(connector, body)).status_code == 200
        (call,) = pool.args_seen
        raw = next(a for a in call if isinstance(a, dict))
        assert raw["SSID"] == "CafeWifi" and raw["inregions"] == ["Home"]
        assert not any(isinstance(a, str) and "\x00" in a for a in call)
        assert len(_ingests(call_tool)) == 1
    finally:
        await _stop(connector)


def test_a_non_finite_coordinate_is_rejected() -> None:
    for bad in (float("nan"), float("inf")):
        with pytest.raises(owntracks.LocationPointRejected):
            owntracks.location_point_fields({**_POINT, "lon": bad})


async def test_a_client_side_encode_error_answers_503() -> None:
    import asyncpg.exceptions as pgx

    call_tool = AsyncMock(return_value={"status": "accepted"})
    # Raised by asyncpg itself while encoding an argument: no server fields.
    for error in (pgx.DataError("invalid input for query argument $8"), ValueError("x"),
                  TypeError("x"), OverflowError("x")):  # fmt: skip
        connector = _connector(call_tool, _FailingPool(error))
        try:
            assert (await _post(connector, _POINT)).status_code == 503
            assert _ingests(call_tool) == []
        finally:
            await _stop(connector)


async def test_a_malformed_point_is_rejected_even_without_a_database() -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, None)
    try:
        for bad in ({"tst": None}, {"tst": "soon"}, {"lat": 95.0}):
            response = await _post(connector, {**_POINT, **bad})
            assert response.status_code == 200
        assert _ingests(call_tool) == []
    finally:
        await _stop(connector)


_TRANSITION = {
    "_type": "transition",
    "tst": 1711360400,
    "tid": "ph",
    "event": "enter",
    "desc": "Home",
    "lat": 1.0,
    "lon": 2.0,
}


async def test_a_transition_is_journaled_before_the_submit(monkeypatch) -> None:
    order: list[str] = []

    async def fake_commit(pool: Any, envelope: dict, **kwargs: Any) -> bool:
        order.append("journal")
        return True

    async def call_tool(tool: str, payload: dict | None = None) -> dict:
        if tool == "ingest":
            order.append("ingest")
        return {"status": "accepted"}

    monkeypatch.setattr(owntracks, "commit_envelope", fake_commit)
    connector = _connector(AsyncMock(side_effect=call_tool), _FakePool())
    try:
        assert (await _post(connector, _TRANSITION)).status_code == 200
        assert order == ["journal", "ingest"]
    finally:
        await _stop(connector)


async def test_a_failed_transition_journal_write_submits_nothing(monkeypatch) -> None:
    from butlers.connectors.evidence_journal import EvidenceNotCommitted

    async def broken_commit(pool: Any, envelope: dict, **kwargs: Any) -> bool:
        raise EvidenceNotCommitted("OSError")

    monkeypatch.setattr(owntracks, "commit_envelope", broken_commit)
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, _FakePool())
    try:
        assert (await _post(connector, _TRANSITION)).status_code == 503
        assert _ingests(call_tool) == []
    finally:
        await _stop(connector)


async def test_a_location_point_does_not_use_the_generic_journal(monkeypatch) -> None:
    async def fail_if_called(pool: Any, envelope: dict, **kwargs: Any) -> bool:
        raise AssertionError("locations live in owntracks_points")

    async def fake_persist(pool: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(owntracks, "commit_envelope", fail_if_called)
    monkeypatch.setattr(owntracks, "persist_location_point", fake_persist)
    connector = _connector(AsyncMock(return_value={"status": "accepted"}), _FakePool())
    try:
        assert (await _post(connector, _POINT)).status_code == 200
    finally:
        await _stop(connector)


async def test_evidence_scrub_failure_keeps_location_unacknowledged(monkeypatch, caplog) -> None:
    """A scrub failure is not a duplicate: 503, no ingest, no checkpoint; a retry writes once."""

    def scrub_unavailable(*_args, **_kwargs):
        raise RuntimeError("test-private-scrub-details")

    call_tool = AsyncMock(return_value={"status": "accepted"})
    pool = _FailingPool(error=None)
    connector = _connector(call_tool, pool)
    saved = AsyncMock()
    connector._save_checkpoint = saved
    try:
        with monkeypatch.context() as patch:
            patch.setattr(owntracks, "scrub_evidence_json", scrub_unavailable)
            response = await _post(connector, _POINT)

        assert response.status_code == 503
        assert (len(pool.args_seen), len(_ingests(call_tool)), saved.await_count) == (0, 0, 0)
        assert "test-private-scrub-details" not in caplog.text

        response = await _post(connector, _POINT)  # the scrub recovered
        assert response.status_code == 200
        assert len(pool.args_seen) == len(_ingests(call_tool)) == 1
    finally:
        await _stop(connector)
