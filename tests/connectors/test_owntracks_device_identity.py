"""Per-device identity/heartbeat/checkpoint isolation tests for the OwnTracks connector.

Regression coverage for bu-86zll: the connector previously kept ONE mutable
``self._endpoint_identity`` (plus one shared heartbeat/metrics/ingestion-policy/
filtered-event-buffer) that got overwritten every time a *different* physical
device's `tid` resolved. With several household devices posting through the
same connector process, this meant:

- whichever device most recently resolved "owned" the shared heartbeat, so
  sibling devices could go silent for weeks with zero registry row (bu-e16to)
- because webhook events are dispatched fire-and-forget onto the main loop
  (see ``_dispatch_event_to_main_loop``), two devices' events could interleave
  such that a *slower* event finished processing under a *different* device's
  identity than the one it actually belonged to -- corrupting that device's
  checkpoint cursor (``cursor_store`` writes straight into
  ``switchboard.connector_registry.checkpoint_cursor``, so this was not benign)

The fix gives each resolved device its own independent state bundle
(``_OwnTracksDeviceState``): metrics, ingestion policy, filtered-event buffer,
heartbeat lifecycle, and checkpoint bookkeeping, resolved once per event and
threaded through as a local variable rather than read back off shared
connector state.

[bu-86zll]
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.owntracks import OwnTracksConnector, OwnTracksConnectorConfig

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal asyncpg-Pool test double for cursor_store.save_cursor/load_cursor
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(
        self,
        store: dict[tuple[str, str], str],
        registered_identities: list[str],
    ) -> None:
        self._store = store
        self._registered_identities = registered_identities

    async def execute(
        self,
        _sql: str,
        connector_type: str,
        endpoint_identity: str,
        cursor_value: str,
        _now: object,
        _operational_role: str,
        _parent_endpoint_identity: str | None,
    ) -> str:
        # ``save_cursor`` passes the row's operational role as $5 and the parent
        # runtime instance as $6 (sw_031, bu-ogs8x); the fake keys on
        # ``(connector_type, endpoint_identity)`` like the real upsert, so both
        # are accepted and ignored here.
        self._store[(connector_type, endpoint_identity)] = cursor_value
        return "INSERT 0 1"

    async def fetchrow(
        self, _sql: str, connector_type: str, endpoint_identity: str
    ) -> dict[str, str] | None:
        value = self._store.get((connector_type, endpoint_identity))
        return None if value is None else {"checkpoint_cursor": value}

    async def fetch(self, sql: str, connector_type: str, placeholder_identity: str):
        assert "deleted_at IS NULL" in sql
        assert "archived_at IS NULL" in sql
        assert "state IS DISTINCT FROM 'paused'" in sql
        assert connector_type == "owntracks"
        return [
            {"endpoint_identity": identity}
            for identity in self._registered_identities
            if identity != placeholder_identity
        ]


class _FakeAcquireContext:
    def __init__(
        self,
        store: dict[tuple[str, str], str],
        registered_identities: list[str],
    ) -> None:
        self._store = store
        self._registered_identities = registered_identities

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._store, self._registered_identities)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeCursorPool:
    """Sufficient double for cursor_store's ``pool.acquire()`` usage.

    Real ``switchboard.connector_registry`` rows are keyed by
    ``(connector_type, endpoint_identity)`` with an upsert -- this fake mirrors
    that exactly, in memory, so checkpoint-isolation assertions reflect the
    real production keying.
    """

    def __init__(self, *, registered_identities: list[str] | None = None) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.registered_identities = registered_identities or []

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self.store, self.registered_identities)


def _make_connector(
    *,
    cursor_pool: _FakeCursorPool | None = None,
    call_tool: AsyncMock | None = None,
    tracker_id_override: str | None = None,
) -> OwnTracksConnector:
    """Build an OwnTracksConnector with a mocked MCP client (no real network I/O).

    ``db_pool=None`` keeps IngestionPolicyEvaluator fail-open (no DB fetch
    attempted) so tests don't need to stub `.fetch()`. ``cursor_pool`` is a
    ``_FakeCursorPool`` when the test needs real checkpoint persistence.
    """
    config = OwnTracksConnectorConfig(
        switchboard_mcp_url="http://test-switchboard/mcp",
        tracker_id_override=tracker_id_override,
    )
    connector = OwnTracksConnector(
        config=config,
        webhook_token="test-token",
        db_pool=None,
        cursor_pool=cursor_pool,
    )
    connector._mcp_client = AsyncMock()
    connector._mcp_client.call_tool = call_tool or AsyncMock(return_value={"status": "accepted"})
    return connector


async def _stop_all_heartbeats(connector: OwnTracksConnector) -> None:
    for device in connector._devices.values():
        await device.heartbeat.stop()


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


async def test_get_or_create_device_creates_distinct_devices_for_distinct_identities() -> None:
    connector = _make_connector()
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        device_b = await connector._get_or_create_device("owntracks:b")

        assert device_a is not device_b
        assert device_a.endpoint_identity == "owntracks:a"
        assert device_b.endpoint_identity == "owntracks:b"
        assert connector._devices.keys() == {"owntracks:a", "owntracks:b"}
    finally:
        await _stop_all_heartbeats(connector)


async def test_get_or_create_device_reuses_existing_device_for_same_identity() -> None:
    connector = _make_connector()
    try:
        first = await connector._get_or_create_device("owntracks:a")
        second = await connector._get_or_create_device("owntracks:a")

        assert first is second
        assert len(connector._devices) == 1
    finally:
        await _stop_all_heartbeats(connector)


@pytest.mark.parametrize("tid", [None, "", "abc", "phone", "a!", "12-"])
async def test_invalid_tids_do_not_create_per_device_state(tid: object) -> None:
    """Malformed or overlong tracker IDs must not mint durable device resources."""
    connector = _make_connector()
    try:
        await connector._process_webhook_event(
            {
                "_type": "location",
                "tst": 100,
                "tid": tid,
                "lat": 1.0,
                "lon": 2.0,
            }
        )

        assert connector._devices == {}
        connector._mcp_client.call_tool.assert_not_awaited()
    finally:
        await _stop_all_heartbeats(connector)


async def test_unsupported_payload_does_not_create_state_for_a_valid_tid() -> None:
    """Protocol-level ignores do not need a per-device heartbeat or metrics bundle."""
    connector = _make_connector()
    try:
        await connector._process_webhook_event(
            {
                "_type": "lwt",
                "tid": "a1",
            }
        )

        assert connector._devices == {}
        connector._mcp_client.call_tool.assert_not_awaited()
    finally:
        await _stop_all_heartbeats(connector)


async def test_tracker_id_override_uses_its_fixed_device_for_invalid_payload_tid() -> None:
    """A configured single-device deployment remains pinned to its fixed identity."""
    connector = _make_connector(tracker_id_override="configured")
    try:
        await connector._process_webhook_event(
            {
                "_type": "location",
                "tst": 100,
                "tid": "malformed",
                "lat": 1.0,
                "lon": 2.0,
            }
        )

        assert connector._devices.keys() == {"owntracks:configured"}
        ingest_call = connector._mcp_client.call_tool.await_args
        assert ingest_call.args[1]["source"]["endpoint_identity"] == "owntracks:configured"
    finally:
        await _stop_all_heartbeats(connector)


async def test_devices_have_independent_metrics_and_policy_scope() -> None:
    connector = _make_connector()
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        device_b = await connector._get_or_create_device("owntracks:b")

        assert device_a.metrics is not device_b.metrics
        assert device_a.filtered_event_buffer is not device_b.filtered_event_buffer
        assert device_a.ingestion_policy.scope == "connector:owntracks:owntracks:a"
        assert device_b.ingestion_policy.scope == "connector:owntracks:owntracks:b"
    finally:
        await _stop_all_heartbeats(connector)


# ---------------------------------------------------------------------------
# Heartbeat isolation
# ---------------------------------------------------------------------------


async def test_restart_restores_heartbeat_for_registered_devices() -> None:
    """A process restart must not leave quiet webhook devices falsely offline."""
    cursor_pool = _FakeCursorPool(
        registered_identities=["owntracks:unknown", "owntracks:a", "owntracks:b"]
    )
    connector = _make_connector(cursor_pool=cursor_pool)

    try:
        await connector._restore_registered_devices()

        assert connector._devices.keys() == {"owntracks:a", "owntracks:b"}
        assert all(device.heartbeat._task is not None for device in connector._devices.values())
        assert connector._mcp_client.call_tool.await_count == 2
    finally:
        await _stop_all_heartbeats(connector)


async def test_restart_skips_invalid_registered_device_identities() -> None:
    """Persisted rows must not bypass the device-reported TID boundary."""
    cursor_pool = _FakeCursorPool(
        registered_identities=[
            "owntracks:a",
            "owntracks:b2",
            "owntracks:abc",
            "owntracks:phone",
            "owntracks:a!",
            "owntracks:a:b",
            "owntracks:",
            "telegram:a",
        ]
    )
    connector = _make_connector(cursor_pool=cursor_pool)

    try:
        await connector._restore_registered_devices()

        assert connector._devices.keys() == {"owntracks:a", "owntracks:b2"}
        assert connector._mcp_client.call_tool.await_count == 2
    finally:
        await _stop_all_heartbeats(connector)


async def test_new_device_heartbeat_does_not_stop_existing_devices_heartbeat() -> None:
    """Resolving a second device must not touch the first device's heartbeat task.

    Under the old shared-identity design, every identity switch tore down and
    replaced the one shared heartbeat task. With per-device state, sibling
    devices' heartbeats are fully independent.
    """
    connector = _make_connector()
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        task_a_before = device_a.heartbeat._task
        assert task_a_before is not None
        assert not task_a_before.done()

        await connector._get_or_create_device("owntracks:b")

        assert device_a.heartbeat._task is task_a_before
        assert not task_a_before.done()
    finally:
        await _stop_all_heartbeats(connector)


async def test_devices_have_distinct_heartbeat_instance_ids() -> None:
    connector = _make_connector()
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        device_b = await connector._get_or_create_device("owntracks:b")

        assert device_a.heartbeat.instance_id != device_b.heartbeat.instance_id
    finally:
        await _stop_all_heartbeats(connector)


async def test_shutdown_stops_every_devices_heartbeat() -> None:
    connector = _make_connector()
    device_a = await connector._get_or_create_device("owntracks:a")
    device_b = await connector._get_or_create_device("owntracks:b")

    await connector._shutdown()

    assert device_a.heartbeat._task is None
    assert device_b.heartbeat._task is None


async def test_shutdown_does_not_register_unresolved_placeholder() -> None:
    connector = _make_connector()
    placeholder = connector._create_device_state(connector._endpoint_identity)
    connector._devices[connector._endpoint_identity] = placeholder

    await connector._shutdown()

    connector._mcp_client.call_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# Checkpoint isolation
# ---------------------------------------------------------------------------


async def test_save_checkpoint_is_isolated_per_device() -> None:
    cursor_pool = _FakeCursorPool()
    connector = _make_connector(cursor_pool=cursor_pool)
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        device_b = await connector._get_or_create_device("owntracks:b")

        await connector._save_checkpoint(device_a, 100)
        await connector._save_checkpoint(device_b, 200)

        assert device_a.last_checkpoint_tst == 100
        assert device_b.last_checkpoint_tst == 200
        assert cursor_pool.store[("owntracks", "owntracks:a")] == "100"
        assert cursor_pool.store[("owntracks", "owntracks:b")] == "200"
    finally:
        await _stop_all_heartbeats(connector)


async def test_load_checkpoint_reads_back_only_the_matching_device() -> None:
    cursor_pool = _FakeCursorPool()
    cursor_pool.store[("owntracks", "owntracks:a")] = "555"
    connector = _make_connector(cursor_pool=cursor_pool)
    try:
        device_a = await connector._get_or_create_device("owntracks:a")
        device_b = await connector._get_or_create_device("owntracks:b")

        assert device_a.last_checkpoint_tst == 555
        assert device_b.last_checkpoint_tst is None
    finally:
        await _stop_all_heartbeats(connector)


@pytest.mark.parametrize("event_tst", [555, 554])
async def test_checkpoint_replays_are_logged_but_submitted(
    caplog: pytest.LogCaptureFixture,
    event_tst: int,
) -> None:
    """A scalar checkpoint is diagnostic only; Switchboard owns duplicate effects."""
    cursor_pool = _FakeCursorPool()
    cursor_pool.store[("owntracks", "owntracks:ph")] = "555"
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _make_connector(cursor_pool=cursor_pool, call_tool=call_tool)
    try:
        with caplog.at_level(logging.DEBUG, logger="butlers.connectors.owntracks"):
            await connector._process_webhook_event(
                {
                    "_type": "location",
                    "tst": event_tst,
                    "tid": "ph",
                    "lat": 1.0,
                    "lon": 2.0,
                }
            )

        ingest_calls = [call for call in call_tool.await_args_list if call.args[0] == "ingest"]
        assert len(ingest_calls) == 1
        assert ingest_calls[0].args[1]["event"]["external_event_id"] == f"{event_tst}:location"

        replay_records = [
            record
            for record in caplog.records
            if record.getMessage() == "OwnTracksConnector: event may be a replay; submitting it"
        ]
        assert len(replay_records) == 1
        record = replay_records[0]
        assert record.endpoint_identity == "owntracks:ph"
        assert record.event_tst == event_tst
        assert record.checkpoint_tst == 555
    finally:
        await _stop_all_heartbeats(connector)


# ---------------------------------------------------------------------------
# End-to-end: concurrent webhook events from different devices
# ---------------------------------------------------------------------------


async def test_concurrent_events_from_different_devices_do_not_cross_contaminate_checkpoints() -> (
    None
):
    """Regression test for the exact race the old shared-identity design had.

    Webhook events are dispatched fire-and-forget onto the main loop, so a
    slow-to-process device-A event and a fast device-B event can interleave.
    Under the old design, reading ``self._endpoint_identity`` *after* the
    ingest await (to save the checkpoint) could observe device B's identity
    if B's processing completed while A was still suspended -- corrupting A's
    checkpoint. This test forces exactly that interleaving and asserts each
    device's checkpoint lands under its own identity regardless.
    """
    cursor_pool = _FakeCursorPool()
    a_may_proceed = asyncio.Event()

    async def fake_call_tool(tool_name: str, payload: dict | None = None) -> dict:
        if tool_name == "ingest" and payload is not None:
            if payload.get("sender", {}).get("identity") == "owntracks:a":
                await a_may_proceed.wait()
        return {"status": "accepted"}

    connector = _make_connector(
        cursor_pool=cursor_pool, call_tool=AsyncMock(side_effect=fake_call_tool)
    )
    try:
        payload_a = {"_type": "location", "tst": 100, "tid": "a", "lat": 1.0, "lon": 2.0}
        payload_b = {"_type": "location", "tst": 200, "tid": "b", "lat": 3.0, "lon": 4.0}

        task_a = asyncio.create_task(connector._process_webhook_event(payload_a))
        # Let task_a run up to (and block inside) its "ingest" call.
        await asyncio.sleep(0.01)

        task_b = asyncio.create_task(connector._process_webhook_event(payload_b))
        await task_b  # device B resolves, ingests, and checkpoints while A is still parked

        a_may_proceed.set()
        await task_a

        assert connector._devices["owntracks:a"].last_checkpoint_tst == 100
        assert connector._devices["owntracks:b"].last_checkpoint_tst == 200
        assert cursor_pool.store[("owntracks", "owntracks:a")] == "100"
        assert cursor_pool.store[("owntracks", "owntracks:b")] == "200"
    finally:
        await _stop_all_heartbeats(connector)
