"""HA connector — global ingestion-policy pre-check self-persist (bu-416vk).

Verified live (bu-49fqa investigation): binary_sensor motion/door/garage state
changes always bypass HA's local Layer-2 significance filter and the Layer-1
domain allowlist, reach Switchboard ingest(), and get global-policy
skip-routed into payload-less public.ingestion_events — NOT into
connectors.filtered_events. Every other multi-scope connector (gmail,
google_calendar, telegram_bot, telegram_user_client, discord_user,
whatsapp_user_client) pre-checks the global ingestion policy locally and
self-persists to FilteredEventBuffer with a "global_rule:skip:*" filter_reason
BEFORE calling ingest(); home_assistant.py was the one connector missing it.

These tests drive the *real* dispatcher built by ``_make_event_dispatcher``
(the same code ``_main`` uses), mocking only the Switchboard MCP client and
the global ``IngestionPolicyEvaluator``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.home_assistant import (
    HAConnector,
    HAConnectorConfig,
    _make_event_dispatcher,
)
from butlers.connectors.home_assistant_filter import HAFilterPersistence
from butlers.connectors.home_assistant_pipeline import HAFilterPipeline, HAFilterPipelineConfig
from butlers.connectors.home_assistant_wellness import WellnessClassifier
from butlers.ingestion_policy import IngestionEnvelope, PolicyDecision

pytestmark = pytest.mark.unit

_BASE_URL = "http://homeassistant.test:8123"
_TIME_FIRED = "2026-03-26T10:00:00.000000+00:00"


class _FakeGlobalPolicy:
    """Minimal stand-in for IngestionPolicyEvaluator.evaluate()."""

    def __init__(self, decision: PolicyDecision | None) -> None:
        self._decision = decision
        self.calls: list[IngestionEnvelope] = []

    def evaluate(self, envelope: IngestionEnvelope) -> PolicyDecision:
        self.calls.append(envelope)
        if self._decision is None:
            raise RuntimeError("boom")
        return self._decision


def _build_connector() -> HAConnector:
    config = HAConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    connector = HAConnector(config=config)
    connector._set_endpoint_identity(_BASE_URL)
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
    connector._mcp_client = mock_mcp
    connector._starting = False
    return connector


def _wire(
    connector: HAConnector,
    global_ingestion_policy: Any | None,
    *,
    dispatcher_db_pool: Any | None = None,
) -> tuple[Any, HAFilterPersistence]:
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
        db_pool=dispatcher_db_pool,
        pipeline=pipeline,
        wellness_classifier=WellnessClassifier(),
        endpoint_identity=connector._endpoint_identity,
        resume_ts=None,
        ha_filter_persistence=persistence,
        global_ingestion_policy=global_ingestion_policy,
    )
    return dispatch, persistence


def _make_binary_sensor_event(
    entity_id: str = "binary_sensor.front_door",
    old_state: str = "off",
    new_state: str = "on",
    device_class: str = "door",
) -> dict[str, Any]:
    """A door/motion binary_sensor state_changed event.

    Binary states always bypass the Layer-2 significance filter (per
    home_assistant_pipeline.py) and binary_sensor is in the default domain
    allowlist, so this event always reaches the (new) global-policy check.
    """
    return {
        "event_type": "state_changed",
        "time_fired": _TIME_FIRED,
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old_state, "attributes": {}},
            "new_state": {
                "state": new_state,
                "attributes": {"device_class": device_class, "friendly_name": "Front Door"},
            },
        },
    }


def _make_person_event(
    entity_id: str = "person.tzeusy",
    old_state: str = "not_home",
    new_state: str = "home",
) -> dict[str, Any]:
    return {
        "event_type": "state_changed",
        "time_fired": _TIME_FIRED,
        "data": {
            "entity_id": entity_id,
            "old_state": {"state": old_state, "attributes": {}},
            "new_state": {"state": new_state, "attributes": {"friendly_name": "Tze"}},
        },
    }


def _make_weight_event(
    *,
    entity_id: str = "sensor.body_weight",
    old_state: str = "72.0",
    new_state: str = "72.0",
    time_fired: str = _TIME_FIRED,
) -> dict[str, Any]:
    return {
        "event_type": "state_changed",
        "time_fired": time_fired,
        "data": {
            "entity_id": entity_id,
            "old_state": {
                "state": old_state,
                "attributes": {"device_class": "weight", "unit_of_measurement": "kg"},
            },
            "new_state": {
                "state": new_state,
                "attributes": {
                    "device_class": "weight",
                    "unit_of_measurement": "kg",
                    "friendly_name": "Body Weight",
                },
            },
        },
    }


def _ingest_calls(connector: HAConnector) -> list[Any]:
    return [
        c
        for c in connector._mcp_client.call_tool.call_args_list
        if c.args and c.args[0] == "ingest"
    ]


@pytest.mark.asyncio
async def test_global_skip_persists_binary_sensor_and_does_not_ingest() -> None:
    """A global-policy 'skip' decision self-persists a binary_sensor event to
    filtered_events with a global_rule:skip:* reason and never calls ingest()."""
    connector = _build_connector()
    policy = _FakeGlobalPolicy(PolicyDecision(action="skip", matched_rule_type="source_channel"))
    dispatch, persistence = _wire(connector, policy)

    await dispatch("state_changed", _make_binary_sensor_event())

    assert not _ingest_calls(connector), "skip-routed event must never reach ingest()"
    assert policy.calls, "global policy must be evaluated for non-person domains"

    assert len(persistence) == 1
    row = persistence._buffer._rows[0]
    filter_reason = row[7]
    source_channel = row[4]
    status = row[8]
    assert filter_reason == "global_rule:skip:source_channel"
    assert source_channel == "home_assistant"
    assert status == "filtered"


@pytest.mark.asyncio
async def test_global_skip_promotes_weight_only_and_advances_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary HA skip must not suppress deterministic wellness ingress."""
    from butlers.connectors import home_assistant_checkpoint

    connector = _build_connector()
    policy = _FakeGlobalPolicy(PolicyDecision(action="skip", matched_rule_type="source_channel"))
    db_pool = object()
    saved_checkpoints: list[tuple[Any, ...]] = []

    async def _save_checkpoint(*args: Any) -> None:
        saved_checkpoints.append(args)

    monkeypatch.setattr(home_assistant_checkpoint, "save_ha_checkpoint", _save_checkpoint)
    dispatch, persistence = _wire(
        connector,
        policy,
        dispatcher_db_pool=db_pool,
    )

    await dispatch("state_changed", _make_weight_event())

    ingest_calls = _ingest_calls(connector)
    assert len(ingest_calls) == 1
    assert ingest_calls[0].args[1]["source"]["channel"] == "wellness"
    assert policy.calls == [
        IngestionEnvelope(source_channel="home_assistant", raw_key="sensor.body_weight")
    ]
    assert len(persistence) == 1
    assert persistence._buffer._rows[0][7] == "global_rule:skip:source_channel"
    assert len(saved_checkpoints) == 1
    assert saved_checkpoints[0][0] is db_pool
    assert saved_checkpoints[0][3] == "sensor.body_weight"


@pytest.mark.asyncio
async def test_global_skip_non_health_event_remains_fully_suppressed() -> None:
    connector = _build_connector()
    policy = _FakeGlobalPolicy(PolicyDecision(action="skip", matched_rule_type="source_channel"))
    dispatch, persistence = _wire(connector, policy)

    await dispatch("state_changed", _make_binary_sensor_event())

    assert _ingest_calls(connector) == []
    assert len(policy.calls) == 1
    assert len(persistence) == 1


@pytest.mark.asyncio
async def test_global_skip_does_not_affect_person_domain() -> None:
    """person.* events must always reach ingest() regardless of global policy
    (bu-bm2pm territory: presence/history depends on this path)."""
    connector = _build_connector()
    policy = _FakeGlobalPolicy(PolicyDecision(action="skip", matched_rule_type="source_channel"))
    dispatch, persistence = _wire(connector, policy)

    await dispatch("state_changed", _make_person_event())

    assert _ingest_calls(connector), "person domain must always be ingested"
    assert not policy.calls, "global policy must not be evaluated for person domain"
    assert len(persistence) == 0


@pytest.mark.asyncio
async def test_global_pass_through_ingests_normally_without_double_persist() -> None:
    """A pass_through (no-match) global decision must not filter or double-persist."""
    connector = _build_connector()
    policy = _FakeGlobalPolicy(PolicyDecision(action="pass_through"))
    dispatch, persistence = _wire(connector, policy)

    await dispatch("state_changed", _make_binary_sensor_event())

    assert _ingest_calls(connector), "pass_through events must still be ingested"
    assert len(persistence) == 0, "pass_through must not be recorded as filtered"


@pytest.mark.asyncio
async def test_no_global_policy_configured_ingests_normally() -> None:
    """When no global evaluator is wired (e.g. legacy call site), behavior is
    unchanged: non-person events proceed straight to ingest()."""
    connector = _build_connector()
    dispatch, persistence = _wire(connector, None)

    await dispatch("state_changed", _make_binary_sensor_event())

    assert _ingest_calls(connector)
    assert len(persistence) == 0


@pytest.mark.asyncio
async def test_global_policy_evaluation_error_fails_open_to_ingest() -> None:
    """A raised exception during global-policy evaluation must not crash the
    dispatcher; the event proceeds to ingest() (fail-open, matches existing
    per-layer exception handling conventions elsewhere in this dispatcher)."""
    connector = _build_connector()
    policy = _FakeGlobalPolicy(None)  # raises on evaluate()
    dispatch, persistence = _wire(connector, policy)

    await dispatch("state_changed", _make_binary_sensor_event())

    assert _ingest_calls(connector)
    assert len(persistence) == 0
