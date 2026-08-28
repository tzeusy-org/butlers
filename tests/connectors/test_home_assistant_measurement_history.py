"""Durable Home Assistant measurement-history recovery tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import home_assistant_rest as ha_rest
from butlers.connectors.home_assistant_rest import EntityStateSnapshot
from butlers.connectors.home_assistant_wellness import WellnessClassifier

pytestmark = pytest.mark.unit

_ENTITY_ID = "sensor.body_weight"
_ENDPOINT_IDENTITY = "home_assistant:homeassistant.test:8123"
_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _recovery_class() -> type[Any]:
    recovery_class = getattr(ha_rest, "HAMeasurementHistoryRecovery", None)
    assert recovery_class is not None, "measurement-history recovery is not implemented"
    return recovery_class


def _weight_snapshot() -> EntityStateSnapshot:
    return EntityStateSnapshot(
        entity_id=_ENTITY_ID,
        state="72.0",
        attributes={
            "device_class": "weight",
            "unit_of_measurement": "kg",
            "friendly_name": "Body Weight",
        },
        last_changed="2026-08-27T07:00:00+00:00",
        last_updated="2026-08-27T07:00:00+00:00",
    )


def _history_state(timestamp: str, value: str = "72.0") -> dict[str, Any]:
    return {
        "entity_id": _ENTITY_ID,
        "state": value,
        "last_changed": timestamp,
        "last_updated": timestamp,
    }


class _RecordingMetrics:
    def __init__(self) -> None:
        self.polls: list[str] = []
        self.emitted: list[int] = []
        self.cursor_ages: list[float] = []

    def record_poll(self, status: str) -> None:
        self.polls.append(status)

    def record_emitted(self, count: int) -> None:
        self.emitted.append(count)

    def set_cursor_age(self, seconds: float) -> None:
        self.cursor_ages.append(seconds)


def _build_recovery(
    on_measurement: AsyncMock,
    *,
    metrics: _RecordingMetrics | None = None,
) -> Any:
    return _recovery_class()(
        base_url="http://homeassistant.test:8123",
        access_token="test-token",
        endpoint_identity=_ENDPOINT_IDENTITY,
        db_pool=object(),
        on_measurement=on_measurement,
        metrics=metrics,
        now=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_history_recovery_catches_up_measurements_after_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[dict[str, Any]] = []

    async def _emit(event: dict[str, Any]) -> bool:
        emitted.append(event)
        return True

    recovery = _build_recovery(AsyncMock(side_effect=_emit))
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(
            return_value=[
                _history_state("2026-08-26T07:00:00+00:00", "72.4"),
                _history_state("2026-08-27T07:00:00+00:00", "72.0"),
            ]
        ),
    )
    saved: list[str] = []

    async def _load_cursor(*_args: Any, **_kwargs: Any) -> str:
        return '{"last_measurement_at":"2026-08-25T07:00:00+00:00"}'

    async def _save_cursor(*args: Any, **_kwargs: Any) -> None:
        saved.append(args[3])

    monkeypatch.setattr(ha_rest, "load_cursor", _load_cursor)
    monkeypatch.setattr(ha_rest, "save_cursor", _save_cursor)

    result = await recovery.recover_once()

    assert result.success is True
    assert result.emitted == 2
    assert [event["time_fired"] for event in emitted] == [
        "2026-08-26T07:00:00+00:00",
        "2026-08-27T07:00:00+00:00",
    ]
    assert len(saved) == 2
    assert "2026-08-27T07:00:00+00:00" in saved[-1]


@pytest.mark.asyncio
async def test_same_value_on_later_day_is_a_distinct_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on_measurement = AsyncMock(return_value=True)
    recovery = _build_recovery(on_measurement)
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(
            return_value=[
                _history_state("2026-08-26T07:00:00+00:00"),
                _history_state("2026-08-27T07:00:00+00:00"),
            ]
        ),
    )
    monkeypatch.setattr(ha_rest, "load_cursor", AsyncMock(return_value=None))
    monkeypatch.setattr(ha_rest, "save_cursor", AsyncMock())

    result = await recovery.recover_once()

    assert result.emitted == 2
    first_event, second_event = [call.args[0] for call in on_measurement.await_args_list]
    assert first_event["data"]["new_state"]["state"] == "72.0"
    assert second_event["data"]["new_state"]["state"] == "72.0"
    assert first_event["time_fired"] != second_event["time_fired"]


@pytest.mark.asyncio
async def test_duplicate_entity_timestamp_replay_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on_measurement = AsyncMock(return_value=True)
    recovery = _build_recovery(on_measurement)
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(return_value=[_history_state("2026-08-27T07:00:00+00:00")]),
    )
    cursor_value: str | None = None

    async def _load_cursor(*_args: Any, **_kwargs: Any) -> str | None:
        return cursor_value

    async def _save_cursor(*args: Any, **_kwargs: Any) -> None:
        nonlocal cursor_value
        cursor_value = args[3]

    monkeypatch.setattr(ha_rest, "load_cursor", _load_cursor)
    monkeypatch.setattr(ha_rest, "save_cursor", _save_cursor)

    first = await recovery.recover_once()
    second = await recovery.recover_once()

    assert first.emitted == 1
    assert second.emitted == 0
    on_measurement.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_timestamp_rows_in_one_response_emit_and_checkpoint_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on_measurement = AsyncMock(return_value=True)
    recovery = _build_recovery(on_measurement)
    duplicate_timestamp = "2026-08-27T07:00:00+00:00"
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(
            return_value=[
                _history_state(duplicate_timestamp),
                _history_state(duplicate_timestamp),
            ]
        ),
    )
    monkeypatch.setattr(ha_rest, "load_cursor", AsyncMock(return_value=None))
    save_cursor = AsyncMock()
    monkeypatch.setattr(ha_rest, "save_cursor", save_cursor)

    result = await recovery.recover_once()

    assert result.success is True
    assert result.emitted == 1
    on_measurement.assert_awaited_once()
    save_cursor.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_history_fetch_does_not_advance_entity_cursor(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recovery = _build_recovery(AsyncMock(return_value=True))
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(side_effect=TimeoutError("history timeout")),
    )
    monkeypatch.setattr(
        ha_rest,
        "load_cursor",
        AsyncMock(return_value='{"last_measurement_at":"2026-08-25T07:00:00+00:00"}'),
    )
    save_cursor = AsyncMock()
    monkeypatch.setattr(ha_rest, "save_cursor", save_cursor)

    with caplog.at_level("WARNING"):
        result = await recovery.recover_once()

    assert result.success is False
    assert result.emitted == 0
    save_cursor.assert_not_awaited()
    assert "measurement history fetch failed" in caplog.text


@pytest.mark.asyncio
async def test_measurement_callback_exception_is_retryable_without_cursor_advance(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    measurement_at = "2026-08-27T07:00:00+00:00"
    recovery = _build_recovery(AsyncMock(side_effect=RuntimeError("callback exploded")))
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(return_value=[_history_state(measurement_at)]),
    )
    monkeypatch.setattr(ha_rest, "load_cursor", AsyncMock(return_value=None))
    save_cursor = AsyncMock()
    monkeypatch.setattr(ha_rest, "save_cursor", save_cursor)

    with caplog.at_level("WARNING"):
        result = await recovery.recover_once()

    assert result == ha_rest.HAMeasurementHistoryResult(success=False, emitted=0)
    save_cursor.assert_not_awaited()
    assert "measurement history submission callback failed" in caplog.text
    assert _ENTITY_ID in caplog.text
    assert measurement_at in caplog.text
    callback_record = next(
        record
        for record in caplog.records
        if "measurement history submission callback failed" in record.getMessage()
    )
    assert callback_record.getMessage() == (
        "HA measurement history submission callback failed "
        f"entity_id={_ENTITY_ID} measurement_at={measurement_at}"
    )
    assert callback_record.exc_info is not None


@pytest.mark.asyncio
async def test_history_event_uses_existing_wellness_envelope_with_measurement_timestamp() -> None:
    from butlers.connectors import home_assistant as ha_connector

    emit_history = getattr(ha_connector, "_emit_history_wellness_measurement", None)
    assert emit_history is not None, "history-to-wellness dispatcher seam is not implemented"
    mcp_client = AsyncMock()
    measurement_at = "2026-08-27T07:00:00+00:00"
    event = {
        "event_type": "state_changed",
        "time_fired": measurement_at,
        "data": {
            "entity_id": _ENTITY_ID,
            "old_state": None,
            "new_state": {
                "state": "72.0",
                "attributes": _weight_snapshot().attributes,
            },
        },
    }

    submitted = await emit_history(
        mcp_client=mcp_client,
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT_IDENTITY,
        event=event,
        metrics=None,
    )

    assert submitted is True
    mcp_client.call_tool.assert_awaited_once()
    tool_name, envelope = mcp_client.call_tool.await_args.args
    assert tool_name == "ingest"
    assert envelope["source"]["channel"] == "wellness"
    assert envelope["source"]["provider"] == "home_assistant"
    assert envelope["payload"]["raw"]["wellness_measurement"]["valid_at"] == measurement_at


@pytest.mark.asyncio
async def test_quiet_history_poll_reports_success_and_existing_cursor_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _RecordingMetrics()
    on_measurement = AsyncMock(return_value=True)
    recovery = _build_recovery(on_measurement, metrics=metrics)
    monkeypatch.setattr(
        recovery,
        "_fetch_weight_entities",
        AsyncMock(return_value=[_weight_snapshot()]),
    )
    monkeypatch.setattr(
        recovery,
        "_fetch_entity_history",
        AsyncMock(return_value=[_history_state("2026-08-27T07:00:00+00:00")]),
    )
    monkeypatch.setattr(
        ha_rest,
        "load_cursor",
        AsyncMock(return_value='{"last_measurement_at":"2026-08-27T07:00:00+00:00"}'),
    )
    monkeypatch.setattr(ha_rest, "save_cursor", AsyncMock())

    result = await recovery.recover_once()

    assert result.success is True
    assert result.emitted == 0
    assert metrics.polls == ["success"]
    assert metrics.emitted == [0]
    assert metrics.cursor_ages == [29 * 60 * 60]
    on_measurement.assert_not_awaited()
