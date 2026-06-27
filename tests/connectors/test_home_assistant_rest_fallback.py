"""REST polling fallback wiring tests for the Home Assistant connector.

Proves the bug fix for bu-2gfh9: the REST polling fallback
(``HARestPoller`` / ``HAFallbackController``) is wired into the connector's
transport supervisor so HA ingestion survives a WebSocket outage.

The tests drive the *real* wiring built by ``_build_transport_supervisor`` and
the *real* event dispatcher built by ``_make_event_dispatcher`` (the same code
``_main`` uses), mocking only the WebSocket-failure signal and the REST
``GET /api/states`` endpoint:

- after 3 failed WS reconnect attempts the controller activates REST polling,
- a REST poll flows through the filter pipeline and submits to the Switchboard
  (ingest) with the checkpoint tagged ``transport="rest_fallback"``,
- a successful WS reconnect deactivates REST polling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import butlers.connectors.home_assistant_checkpoint as ha_checkpoint
import butlers.connectors.home_assistant_rest as ha_rest
from butlers.connectors.home_assistant import (
    HAConnector,
    HAConnectorConfig,
    _build_transport_supervisor,
    _make_event_dispatcher,
)
from butlers.connectors.home_assistant_filter import HAFilterPersistence
from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig
from butlers.connectors.home_assistant_wellness import WellnessClassifier

_BASE_URL = "http://homeassistant.test:8123"
_TOKEN = "test-token"


# ---------------------------------------------------------------------------
# Fake aiohttp session for HARestPoller.poll_once (GET /api/states)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> list[dict[str, Any]]:
        return self._payload


class _FakeSession:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def get(self, *args: object, **kwargs: object) -> _FakeResp:
        return _FakeResp(self._payload)


def _build_connector() -> HAConnector:
    config = HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    connector = HAConnector(config=config)
    connector._set_endpoint_identity(_BASE_URL)
    # Replace the real MCP client with a mock that records ingest submissions.
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
    connector._mcp_client = mock_mcp
    connector._starting = False
    return connector


def _wire(connector: HAConnector) -> tuple[Any, Any, Any]:
    """Build the real dispatcher + transport supervisor for ``connector``."""
    pipeline = HAFilterPipeline(
        config=HAFilterPipelineConfig(domain_allowlist=connector._config.domain_allowlist),
        evaluator=None,
        metrics=connector._ha_metrics,
    )
    persistence = HAFilterPersistence(
        endpoint_identity=connector._endpoint_identity,
        db_pool=None,
        submit_fn=AsyncMock(),
    )
    dispatch = _make_event_dispatcher(
        connector=connector,
        config=connector._config,
        db_pool=object(),  # non-None: exercises the checkpoint-save branch
        pipeline=pipeline,
        wellness_classifier=WellnessClassifier(),
        endpoint_identity=connector._endpoint_identity,
        resume_ts=None,
        ha_filter_persistence=persistence,
    )
    return _build_transport_supervisor(
        connector=connector,
        config=connector._config,
        ha_base_url=_BASE_URL,
        ha_access_token=_TOKEN,
        dispatch=dispatch,
    )


@pytest.mark.asyncio
async def test_rest_fallback_activates_and_ingests_after_three_failed_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 failed WS reconnects -> REST polling activates and ingests via REST."""
    saved_transports: list[str] = []

    async def _fake_save(_pool, _eid, _ts, _entity, transport):  # type: ignore[no-untyped-def]
        saved_transports.append(transport)

    # Patched BEFORE the dispatcher is built: the factory imports
    # save_ha_checkpoint at construction time.
    monkeypatch.setattr(ha_checkpoint, "save_ha_checkpoint", _fake_save)

    connector = _build_connector()
    ws_client, rest_poller, controller = _wire(connector)

    # Initially: WS path, no fallback.
    assert controller.fallback_active is False
    assert rest_poller.is_running is False

    # First two failed reconnect attempts do not trip the fallback.
    ws_client._on_reconnect_failed()
    ws_client._on_reconnect_failed()
    assert controller.fallback_active is False
    assert rest_poller.is_running is False

    # Third failed reconnect crosses the threshold -> REST polling activates.
    ws_client._on_reconnect_failed()
    assert controller.consecutive_failures == 3
    assert controller.fallback_active is True
    assert rest_poller.is_running is True
    assert connector._rest_fallback_active is True

    # The connector now reports degraded with the REST transport in the message.
    state, message = connector._get_health_state()
    assert state == "degraded"
    assert "rest_fallback" in (message or "")

    # A REST poll surfaces a state change that flows through the real pipeline
    # and is submitted to the Switchboard (ingest) via the REST path.
    states = [
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "Living Room"},
            "last_changed": "2026-06-27T10:00:00+00:00",
            "last_updated": "2026-06-27T10:00:00+00:00",
        }
    ]
    monkeypatch.setattr(ha_rest.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(states))

    diffs = await rest_poller.poll_once()
    assert len(diffs) == 1  # new entity treated as a change

    ingest_calls = [
        c
        for c in connector._mcp_client.call_tool.call_args_list
        if c.args and c.args[0] == "ingest"
    ]
    assert ingest_calls, "expected an ingest submission via the REST fallback path"

    # The checkpoint advanced with the REST transport literal.
    assert saved_transports == ["rest_fallback"]

    rest_poller.stop()


@pytest.mark.asyncio
async def test_rest_poll_checkpoint_pins_rest_fallback_during_recovery_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A REST-polled event checkpoints as ``rest_fallback`` even if WS recovers
    mid-dispatch (``_rest_fallback_active`` flips to False before the
    checkpoint-save await)."""
    saved_transports: list[str] = []

    async def _fake_save(_pool, _eid, _ts, _entity, transport):  # type: ignore[no-untyped-def]
        saved_transports.append(transport)

    monkeypatch.setattr(ha_checkpoint, "save_ha_checkpoint", _fake_save)

    connector = _build_connector()
    _ws_client, rest_poller, _controller = _wire(connector)

    states = [
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "Living Room"},
            "last_changed": "2026-06-27T10:00:00+00:00",
            "last_updated": "2026-06-27T10:00:00+00:00",
        }
    ]
    monkeypatch.setattr(ha_rest.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(states))

    # Simulate the recovery race: the WS client has just reconnected, so the
    # connector's dynamic transport now reads "websocket". The REST poller's
    # explicit transport pin must still win for this polled event.
    connector._rest_fallback_active = False

    diffs = await rest_poller.poll_once()
    assert len(diffs) == 1

    assert saved_transports == ["rest_fallback"]


@pytest.mark.asyncio
async def test_rest_fallback_deactivates_on_ws_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful WS reconnect stops REST polling and restores the WS path."""

    async def _fake_save(*_a, **_k):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(ha_checkpoint, "save_ha_checkpoint", _fake_save)

    connector = _build_connector()
    ws_client, rest_poller, controller = _wire(connector)

    # Trip the fallback.
    for _ in range(3):
        ws_client._on_reconnect_failed()
    assert controller.fallback_active is True
    assert rest_poller.is_running is True

    # WS reconnects -> fallback deactivates, poller stops, counter resets.
    ws_client._on_connected()
    assert controller.fallback_active is False
    assert controller.consecutive_failures == 0
    assert rest_poller.is_running is False
    assert connector._rest_fallback_active is False
    assert connector._ws_connected is True

    # Back on the WebSocket transport.
    state, message = connector._get_health_state()
    assert state == "healthy"
    assert "transport=websocket" in (message or "")
