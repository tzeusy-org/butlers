"""Condensed Home Assistant connector tests — ingest.v1 contract and write path.

Consolidates: test_home_assistant_envelope.py, test_home_assistant_filter.py,
test_home_assistant_integration.py, test_ha_checkpoint.py,
test_ha_connector_health_metrics.py, test_ha_filter_pipeline.py,
test_ha_rest_poller.py, test_ha_ws_client.py

Verifies:
- ingest.v1 envelope production for state_changed and automation_triggered
- Normalized text generation (branching logic: sensor, binary_sensor, etc.)
- parse_ingest_envelope contract compliance
- persist_ha_history writes correct columns and is non-fatal on DB error
- dispatch function routes events through filter pipeline and persists person.* to history

[bu-35fm7]
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.home_assistant import (
    _DEFAULT_DOMAIN_ALLOWLIST,
    HAConnector,
    HAConnectorConfig,
    _load_domain_allowlist_from_store,
    persist_ha_history,
)
from butlers.connectors.home_assistant_envelope import (
    build_automation_triggered_envelope,
    build_state_changed_envelope,
    build_state_changed_normalized_text,
)

_ENDPOINT = "home_assistant:homeassistant.local:8123"
_TIME_FIRED = "2026-03-26T10:00:00.000000+00:00"


@pytest.fixture
def state_changed_envelope() -> dict[str, Any]:
    return build_state_changed_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.living_room_temperature",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        friendly_name="Living Room Temperature",
        old_state={"state": "21.5"},
        new_state={"state": "22.0"},
        domain="sensor",
        device_class="temperature",
        unit_of_measurement="°C",
    )


def test_state_changed_schema_version(state_changed_envelope: dict[str, Any]) -> None:
    """state_changed envelope must carry schema_version='ingest.v1'."""
    assert state_changed_envelope["schema_version"] == "ingest.v1"
    assert state_changed_envelope["source"]["channel"] == "home_assistant"
    assert state_changed_envelope["source"]["provider"] == "home_assistant"
    assert state_changed_envelope["source"]["endpoint_identity"] == _ENDPOINT


def test_state_changed_event_fields(state_changed_envelope: dict[str, Any]) -> None:
    """External event ID must be deterministic for same entity+time."""
    eid = state_changed_envelope["event"]["external_event_id"]
    assert "sensor.living_room_temperature" in eid


def test_state_changed_ingestion_tier_full(state_changed_envelope: dict[str, Any]) -> None:
    """HA connector must use 'full' ingestion tier (state data included)."""
    assert state_changed_envelope["control"]["ingestion_tier"] == "full"


def test_state_changed_passes_parse_ingest_envelope(
    state_changed_envelope: dict[str, Any],
) -> None:
    """state_changed envelope must validate against parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    try:
        parse_ingest_envelope(state_changed_envelope)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_automation_triggered_schema_version() -> None:
    """automation_triggered envelope must carry schema_version='ingest.v1'."""
    env = build_automation_triggered_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="automation.morning_routine",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "automation_triggered"},
        friendly_name="Morning Routine",
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["provider"] == "home_assistant"


def test_normalized_text_includes_entity_and_state() -> None:
    """Normalized text must include entity ID and new state value."""
    text = build_state_changed_normalized_text(
        entity_id="sensor.living_room_temperature",
        friendly_name="Living Room Temp",
        old_state="21.0",
        new_state="22.5",
        unit_of_measurement="°C",
    )
    assert "22.5" in text
    assert "°C" in text


def test_normalized_text_binary_sensor_on_off() -> None:
    """binary_sensor state changes include state info in normalized text."""
    text = build_state_changed_normalized_text(
        entity_id="binary_sensor.motion_sensor",
        friendly_name="Motion Sensor",
        old_state="off",
        new_state="on",
        unit_of_measurement=None,
    )
    assert text
    assert isinstance(text, str)
    assert "on" in text.lower() or "Motion Sensor" in text


# ---------------------------------------------------------------------------
# Domain allowlist configuration
# ---------------------------------------------------------------------------


def test_domain_allowlist_env_extends_safety_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """An env addition must not silently remove ``person`` or other defaults."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("HA_DOMAIN_ALLOWLIST", "media_player")

    config = HAConnectorConfig.from_env()

    assert config.domain_allowlist == _DEFAULT_DOMAIN_ALLOWLIST | {"media_player"}


def test_startup_health_uses_canonical_degraded_state() -> None:
    connector = HAConnector(HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"))

    assert connector._get_health_state() == ("degraded", "transport=starting")


def test_failed_initial_websocket_attempt_reports_disconnected_transport() -> None:
    connector = HAConnector(HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"))

    connector.on_ws_disconnected()

    assert connector._get_health_state() == (
        "degraded",
        "WebSocket disconnected — transport=disconnected, ws_reconnect_attempts=1",
    )


@pytest.mark.asyncio
async def test_registry_domain_allowlist_overrides_env_extras_but_preserves_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashboard config is endpoint-scoped and can never drop safety defaults."""
    from butlers.connectors import cursor_store

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("HA_DOMAIN_ALLOWLIST", "media_player")
    load_settings = AsyncMock(return_value={"domain_allowlist": ["vacuum"]})
    monkeypatch.setattr(cursor_store, "load_connector_settings", load_settings)
    config = HAConnectorConfig.from_env()
    endpoint_identity = "home_assistant:ha.test:8123"
    db_pool = MagicMock()

    await _load_domain_allowlist_from_store(config, db_pool, endpoint_identity)

    assert config.domain_allowlist == _DEFAULT_DOMAIN_ALLOWLIST | {"vacuum"}
    load_settings.assert_awaited_once_with(db_pool, "home_assistant", endpoint_identity)


@pytest.mark.asyncio
async def test_invalid_registry_domain_allowlist_retains_env_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed dashboard value must not turn an operator typo into data loss."""
    from butlers.connectors import cursor_store

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("HA_DOMAIN_ALLOWLIST", "media_player")
    monkeypatch.setattr(
        cursor_store,
        "load_connector_settings",
        AsyncMock(return_value={"domain_allowlist": ["vacuum", 42]}),
    )
    config = HAConnectorConfig.from_env()

    await _load_domain_allowlist_from_store(
        config,
        MagicMock(),
        "home_assistant:ha.test:8123",
    )

    assert config.domain_allowlist == _DEFAULT_DOMAIN_ALLOWLIST | {"media_player"}
    assert any(
        "ignoring invalid domain_allowlist setting" in message for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_non_object_registry_settings_retain_env_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed settings JSONB value must retain environment additions."""
    from butlers.connectors import cursor_store

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("HA_DOMAIN_ALLOWLIST", "media_player")
    monkeypatch.setattr(
        cursor_store,
        "load_connector_settings",
        AsyncMock(return_value=["vacuum"]),
    )
    config = HAConnectorConfig.from_env()

    await _load_domain_allowlist_from_store(
        config,
        MagicMock(),
        "home_assistant:ha.test:8123",
    )

    assert config.domain_allowlist == _DEFAULT_DOMAIN_ALLOWLIST | {"media_player"}
    assert any("ignoring non-object connector settings" in message for message in caplog.messages)


def test_main_resolves_endpoint_identity_before_loading_settings_and_pipeline() -> None:
    """Startup must use the resolved endpoint for settings before Layer 1 is built."""
    import inspect

    from butlers.connectors import home_assistant

    source = inspect.getsource(home_assistant._main)
    identity_idx = source.index("connector._set_endpoint_identity(ha_base_url)")
    settings_idx = source.index("await _load_domain_allowlist_from_store(")
    pipeline_idx = source.index("pipeline = HAFilterPipeline(")
    assert identity_idx < settings_idx < pipeline_idx


# ---------------------------------------------------------------------------
# persist_ha_history tests (tasks 8 — evidence table write path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_ha_history_null_state_and_attrs() -> None:
    """persist_ha_history accepts None state and None attributes."""
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()

    recorded_at = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
    result = await persist_ha_history(
        mock_pool,
        entity_id="person.someone",
        state=None,
        attributes=None,
        recorded_at=recorded_at,
    )

    assert result is True
    call_args = mock_pool.execute.call_args
    assert call_args[0][2] is None  # state
    assert call_args[0][3] is None  # attributes (serialized None → None)


@pytest.mark.asyncio
async def test_persist_ha_history_db_error_is_non_fatal() -> None:
    """persist_ha_history returns False and does not raise on DB error."""
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

    recorded_at = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
    result = await persist_ha_history(
        mock_pool,
        entity_id="person.tzeusy",
        state="not_home",
        attributes=None,
        recorded_at=recorded_at,
    )

    assert result is False  # non-fatal: returns False rather than raising


# ---------------------------------------------------------------------------
# Filter pipeline routing tests — verifies dispatch logic contract
# ---------------------------------------------------------------------------


def _make_state_changed_event(
    entity_id: str = "sensor.living_room_temperature",
    old_state: str = "21.0",
    new_state: str = "22.0",
    time_fired: str = "2026-03-26T10:00:00.000000+00:00",
    device_class: str | None = "temperature",
    friendly_name: str | None = "Living Room Temperature",
) -> dict[str, Any]:
    """Build a minimal HA WebSocket event dict for state_changed."""
    return {
        "event_type": "state_changed",
        "time_fired": time_fired,
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old_state, "attributes": {}},
            "new_state": {
                "state": new_state,
                "attributes": {
                    "device_class": device_class,
                    "friendly_name": friendly_name,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_pipeline_passes_significant_sensor_change() -> None:
    """A large enough temperature delta passes the significance filter."""
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig

    pipeline = HAFilterPipeline(
        config=HAFilterPipelineConfig(),
        evaluator=None,
        metrics=None,
    )
    result = await pipeline.run(
        entity_id="sensor.living_room_temperature",
        domain="sensor",
        device_class="temperature",
        old_state_str="21.0",
        new_state_str="22.0",  # delta=1.0 > threshold=0.5
    )
    assert result.verdict == "pass"


@pytest.mark.asyncio
async def test_pipeline_filters_insignificant_sensor_change() -> None:
    """A delta below threshold is filtered by significance filter."""
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig

    pipeline = HAFilterPipeline(
        config=HAFilterPipelineConfig(),
        evaluator=None,
        metrics=None,
    )
    # First call to populate cache
    await pipeline.run(
        entity_id="sensor.bedroom_temperature",
        domain="sensor",
        device_class="temperature",
        old_state_str="21.0",
        new_state_str="21.1",  # delta=0.1 < threshold=0.5
    )
    # Second call with tiny delta from cached value
    result = await pipeline.run(
        entity_id="sensor.bedroom_temperature",
        domain="sensor",
        device_class="temperature",
        old_state_str="21.1",
        new_state_str="21.2",  # delta=0.1 < threshold=0.5
    )
    assert result.verdict == "filtered"
    assert result.stage == "significance_filter"


@pytest.mark.asyncio
async def test_pipeline_filters_excluded_domain() -> None:
    """Events from media_player domain are excluded by Layer 1."""
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig

    pipeline = HAFilterPipeline(config=HAFilterPipelineConfig(), evaluator=None, metrics=None)
    result = await pipeline.run(
        entity_id="media_player.living_room",
        domain="media_player",
    )
    assert result.verdict == "filtered"
    assert result.stage == "domain_filter"
    assert "media_player" in result.filter_reason


@pytest.mark.asyncio
async def test_pipeline_passes_person_entity() -> None:
    """person.* entities pass the domain filter by default.

    "person" powers the Chronicler presence_episode projector, which reads
    domain == "person" rows from connectors.home_assistant_history (see
    _dispatch). It must be in the default domain allowlist or that table
    (and presence_episodes) stay permanently empty regardless of connectivity
    (bu-whhll.3).
    """
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig

    pipeline = HAFilterPipeline(config=HAFilterPipelineConfig(), evaluator=None, metrics=None)
    result = await pipeline.run(
        entity_id="person.tzeusy",
        domain="person",
        old_state_str="not_home",
        new_state_str="home",  # binary-style state → always passes Layer 2
    )
    assert result.verdict == "pass"


@pytest.mark.asyncio
async def test_pipeline_filters_person_entity_when_excluded_from_allowlist() -> None:
    """person.* is filtered by domain when explicitly excluded from the allowlist."""
    from butlers.connectors.home_assistant import _DEFAULT_DOMAIN_ALLOWLIST
    from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig

    allowlist = frozenset(_DEFAULT_DOMAIN_ALLOWLIST - {"person"})
    pipeline = HAFilterPipeline(
        config=HAFilterPipelineConfig(domain_allowlist=allowlist),
        evaluator=None,
        metrics=None,
    )
    result = await pipeline.run(
        entity_id="person.tzeusy",
        domain="person",
        old_state_str="not_home",
        new_state_str="home",
    )
    assert result.verdict == "filtered"
    assert result.stage == "domain_filter"


def test_main_waits_for_switchboard_ready_before_ws_ingress() -> None:
    """_main() must probe Switchboard readiness before starting WS ingress.

    Every other connector (owntracks, gmail, telegram_bot, google_health,
    google_calendar, steam, spotify, live_listener) guards its ingress loop
    with ``wait_for_switchboard_ready`` so the initial burst of upstream
    events isn't delivered into a ConnectionError while the Switchboard
    butler (butlers-up) is still starting up. home_assistant was missing this
    call (bu-whhll.3); this pins the fix so it can't silently regress.
    """
    import inspect

    from butlers.connectors import home_assistant

    assert home_assistant.wait_for_switchboard_ready is not None

    source = inspect.getsource(home_assistant._main)
    wait_idx = source.index("wait_for_switchboard_ready(")
    ws_task_idx = source.index("asyncio.create_task(ws_client.run())")
    assert wait_idx < ws_task_idx, (
        "wait_for_switchboard_ready(...) must be awaited before the WS client "
        "task is created, or HA events race Switchboard startup again"
    )
