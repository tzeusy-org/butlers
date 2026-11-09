"""A live HA event whose ingest fails is parked, never dropped (bu-dhloi1.224).

The WebSocket and REST-fallback streams are push-only: a live event the
connector fails to submit is never delivered again, and the checkpoint only
deduplicates. The dispatcher used to log the failure and return, so the event
was gone with no record. It now parks each envelope that failed, scrubbed and
with its idempotency key, as a replayable ``connectors.filtered_events`` error
row. The row's error detail is the exception class only, never its text.

Drives the real dispatcher from ``_make_event_dispatcher``, mocking only the
Switchboard MCP client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors import home_assistant
from butlers.connectors.home_assistant import (
    HAConnector,
    HAConnectorConfig,
    _make_event_dispatcher,
)
from butlers.connectors.home_assistant_filter import HAFilterPersistence
from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig
from butlers.connectors.home_assistant_wellness import WellnessClassifier

pytestmark = pytest.mark.unit

_BASE_URL = "http://homeassistant.test:8123"
_TIME_FIRED = "2026-03-26T10:00:00.000000+00:00"
_SECRET_TEXT = "upstream said: token=abc123"


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the live retry's pauses instead of sleeping through them."""
    pauses: list[float] = []

    async def fake_sleep(delay: float) -> None:
        pauses.append(delay)

    monkeypatch.setattr(home_assistant.asyncio, "sleep", fake_sleep)
    return pauses


def _wire(
    fail_channels: set[str],
    *,
    fail_times: int | None = None,
    exc_type: type[Exception] = ConnectionError,
) -> tuple[Any, HAConnector, HAFilterPersistence]:
    """Wire the live dispatcher; ingest of *fail_channels* raises *exc_type*.

    With *fail_times*, each failing channel fails that many times, then lands.
    """
    connector = HAConnector(
        config=HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._set_endpoint_identity(_BASE_URL)
    failures: dict[str, int] = {}

    async def call_tool(tool: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool == "ingest" and payload is not None:
            channel = payload["source"]["channel"]
            if channel in fail_channels:
                failures[channel] = failures.get(channel, 0) + 1
                if fail_times is None or failures[channel] <= fail_times:
                    raise exc_type(_SECRET_TEXT)
        return {"status": "accepted"}

    connector._mcp_client = MagicMock()
    connector._mcp_client.call_tool = AsyncMock(side_effect=call_tool)
    connector._starting = False
    persistence = HAFilterPersistence(
        endpoint_identity=connector._endpoint_identity, db_pool=None, submit_fn=AsyncMock()
    )
    dispatch = _make_event_dispatcher(
        connector=connector,
        config=connector._config,
        db_pool=None,
        pipeline=HAFilterPipeline(
            config=HAFilterPipelineConfig(domain_allowlist=connector._config.domain_allowlist),
            evaluator=None,
            metrics=connector._ha_metrics,
        ),
        wellness_classifier=WellnessClassifier(),
        endpoint_identity=connector._endpoint_identity,
        resume_ts=None,
        ha_filter_persistence=persistence,
    )
    return dispatch, connector, persistence


def _state_event(entity_id: str, old: str, new: str, attributes: dict[str, Any]) -> dict:
    return {
        "event_type": "state_changed",
        "time_fired": _TIME_FIRED,
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old, "attributes": attributes},
            "new_state": {"state": new, "attributes": attributes},
        },
    }


_DOOR = _state_event(
    "binary_sensor.front_door", "off", "on", {"device_class": "door", "friendly_name": "Door"}
)
_WEIGHT = _state_event(
    "sensor.body_weight",
    "70.0",
    "72.0",
    {"device_class": "weight", "unit_of_measurement": "kg", "friendly_name": "Body Weight"},
)


def _attempted(connector: HAConnector) -> dict[str, dict[str, Any]]:
    client: Any = connector._mcp_client
    calls = client.call_tool.call_args_list
    return {c.args[1]["source"]["channel"]: c.args[1] for c in calls if c.args[0] == "ingest"}


def _parked(persistence: HAFilterPersistence) -> dict[str, tuple]:
    return {row[9]["source"]["channel"]: row for row in persistence._buffer._rows}


def _assert_parked_exactly(row: tuple, envelope: dict[str, Any]) -> None:
    assert row[8] == "error"  # status
    assert row[9]["control"]["idempotency_key"] == envelope["control"]["idempotency_key"]
    assert row[9]["event"] == envelope["event"]
    assert "schema_version" not in row[9]
    assert row[10] == "ConnectionError"  # error_detail: the class, never the text
    assert _SECRET_TEXT not in repr(row)


async def test_a_failed_state_change_is_parked_for_replay() -> None:
    dispatch, connector, persistence = _wire({"home_assistant"})

    await dispatch("state_changed", _DOOR)

    parked = _parked(persistence)
    assert parked.keys() == {"home_assistant"}
    _assert_parked_exactly(parked["home_assistant"], _attempted(connector)["home_assistant"])


async def test_a_failed_primary_still_sends_wellness_and_parks_only_the_primary() -> None:
    dispatch, connector, persistence = _wire({"home_assistant"})

    await dispatch("state_changed", _WEIGHT)

    attempted = _attempted(connector)
    assert attempted.keys() == {"home_assistant", "wellness"}
    parked = _parked(persistence)
    assert parked.keys() == {"home_assistant"}
    _assert_parked_exactly(parked["home_assistant"], attempted["home_assistant"])


async def test_a_failed_wellness_emission_is_parked_after_the_primary_lands() -> None:
    dispatch, connector, persistence = _wire({"wellness"})

    await dispatch("state_changed", _WEIGHT)

    parked = _parked(persistence)
    assert parked.keys() == {"wellness"}
    _assert_parked_exactly(parked["wellness"], _attempted(connector)["wellness"])


async def test_a_failed_automation_trigger_is_parked_for_replay() -> None:
    dispatch, connector, persistence = _wire({"home_assistant"})
    event = {
        "event_type": "automation_triggered",
        "time_fired": _TIME_FIRED,
        "data": {"entity_id": "automation.lights_on", "name": "Lights on"},
    }

    await dispatch("automation_triggered", event)

    parked = _parked(persistence)
    assert parked.keys() == {"home_assistant"}
    _assert_parked_exactly(parked["home_assistant"], _attempted(connector)["home_assistant"])


async def test_a_delivered_event_parks_nothing() -> None:
    dispatch, _, persistence = _wire(set())

    await dispatch("state_changed", _WEIGHT)

    assert _parked(persistence) == {}


def _ingest_calls(connector: HAConnector) -> list[dict[str, Any]]:
    client: Any = connector._mcp_client
    return [c.args[1] for c in client.call_tool.call_args_list if c.args[0] == "ingest"]


async def test_a_transport_blip_is_retried_once_after_a_short_pause_and_lands(
    _no_retry_pause: list[float],
) -> None:
    dispatch, connector, persistence = _wire({"home_assistant"}, fail_times=1)

    await dispatch("state_changed", _DOOR)

    calls = _ingest_calls(connector)
    assert len(calls) == 2
    assert calls[0] == calls[1]  # the same envelope, same idempotency key
    assert _no_retry_pause == [home_assistant._LIVE_INGEST_RETRY_DELAY_S]
    assert home_assistant._LIVE_INGEST_RETRY_DELAY_S <= 0.5  # never holds the stream
    assert _parked(persistence) == {}


async def test_a_persistent_transport_failure_stops_after_two_attempts_and_parks(
    _no_retry_pause: list[float],
) -> None:
    dispatch, connector, persistence = _wire({"home_assistant"})

    await dispatch("state_changed", _DOOR)

    assert len(_ingest_calls(connector)) == home_assistant._LIVE_INGEST_ATTEMPTS == 2
    assert len(_no_retry_pause) == 1
    assert _parked(persistence).keys() == {"home_assistant"}


async def test_a_non_transport_failure_is_parked_without_a_retry(
    _no_retry_pause: list[float],
) -> None:
    dispatch, connector, persistence = _wire({"home_assistant"}, exc_type=RuntimeError)

    await dispatch("state_changed", _DOOR)

    assert len(_ingest_calls(connector)) == 1
    assert _no_retry_pause == []
    row = _parked(persistence)["home_assistant"]
    assert row[10] == "RuntimeError"


async def test_a_failed_automation_trigger_retries_a_transport_blip() -> None:
    dispatch, connector, persistence = _wire({"home_assistant"}, fail_times=1)
    event = {
        "event_type": "automation_triggered",
        "time_fired": _TIME_FIRED,
        "data": {"entity_id": "automation.lights_on", "name": "Lights on"},
    }

    await dispatch("automation_triggered", event)

    assert len(_ingest_calls(connector)) == 2
    assert _parked(persistence) == {}
