"""Tests for the Home Assistant wellness classifier and dual-channel emission.

Covers openspec change `home-assistant-wellness-promotion` tasks §2:
- WellnessClassifier rule-table matching (ADR-1)
- env-config gating + rules-extra + denylist (ADR-2)
- build_wellness_envelope payload shape (ADR-4)
- dual-channel emission ordering + single checkpoint advance (ADR-3)
- ambient exclusion + non-numeric skip

[bu-w7qf2.2]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.home_assistant import HAConnectorConfig
from butlers.connectors.home_assistant_envelope import build_wellness_envelope
from butlers.connectors.home_assistant_wellness import (
    DEFAULT_WELLNESS_RULES,
    WellnessClassifier,
    WellnessRule,
)

_ENDPOINT = "home_assistant:homeassistant.local:8123"
_TIME_FIRED = "2026-06-12T14:30:00+00:00"


# ---------------------------------------------------------------------------
# Classifier — default rule table coverage
# ---------------------------------------------------------------------------


def _classifier(**kwargs: Any) -> WellnessClassifier:
    return WellnessClassifier(**kwargs)


@pytest.mark.parametrize(
    ("entity_id", "device_class", "unit", "state", "expected"),
    [
        # blood pressure: mmHg + token
        (
            "sensor.withings_systolic_blood_pressure",
            None,
            "mmHg",
            "120",
            "blood_pressure_systolic",
        ),
        (
            "sensor.withings_diastolic_blood_pressure",
            None,
            "mmHg",
            "80",
            "blood_pressure_diastolic",
        ),
        # weight (device_class weight)
        ("sensor.body_scale_weight", "weight", "kg", "72.5", "weight"),
        ("sensor.body_scale_weight", "weight", "lb", "160", "weight"),
        # heart rate
        ("sensor.oura_heart_rate", None, "bpm", "62", "heart_rate"),
        # blood sugar
        ("sensor.dexcom_glucose", None, "mg/dL", "98", "blood_sugar"),
        ("sensor.dexcom_glucose", None, "mmol/L", "5.4", "blood_sugar"),
        # steps
        ("sensor.phone_steps", None, "steps", "5123", "steps"),
    ],
)
def test_default_rules_match(
    entity_id: str,
    device_class: str | None,
    unit: str | None,
    state: str,
    expected: str,
) -> None:
    clf = _classifier()
    metric = clf.classify(
        entity_id=entity_id,
        device_class=device_class,
        unit_of_measurement=unit,
        attributes={},
        state=state,
    )
    assert metric == expected


def test_mmhg_without_token_no_match() -> None:
    # mmHg alone (no systolic/diastolic token) does not match a BP rule.
    clf = _classifier()
    assert (
        clf.classify(
            entity_id="sensor.barometer",
            device_class=None,
            unit_of_measurement="mmHg",
            attributes={},
            state="760",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Ambient exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity_id", "device_class", "unit", "state"),
    [
        ("sensor.living_room_temperature", "temperature", "°C", "22.0"),
        ("sensor.bedroom_humidity", "humidity", "%", "45"),
        ("sensor.cpu_load", None, "%", "30"),
    ],
)
def test_ambient_not_promoted(
    entity_id: str, device_class: str | None, unit: str | None, state: str
) -> None:
    clf = _classifier()
    assert (
        clf.classify(
            entity_id=entity_id,
            device_class=device_class,
            unit_of_measurement=unit,
            attributes={},
            state=state,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Non-numeric state skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["unknown", "unavailable", "", "abc", None])
def test_non_numeric_state_returns_none(state: str | None) -> None:
    clf = _classifier()
    assert (
        clf.classify(
            entity_id="sensor.withings_systolic_blood_pressure",
            device_class=None,
            unit_of_measurement="mmHg",
            attributes={},
            state=state,
        )
        is None
    )


# ---------------------------------------------------------------------------
# Denylist
# ---------------------------------------------------------------------------


def test_denylisted_entity_never_promoted() -> None:
    clf = _classifier(denylist=frozenset({"sensor.withings_systolic_blood_pressure"}))
    assert (
        clf.classify(
            entity_id="sensor.withings_systolic_blood_pressure",
            device_class=None,
            unit_of_measurement="mmHg",
            attributes={},
            state="120",
        )
        is None
    )


def test_denylisted_outcome_is_distinct_from_skip() -> None:
    clf = _classifier(denylist=frozenset({"sensor.withings_systolic_blood_pressure"}))
    result = clf.classify_detailed(
        entity_id="sensor.withings_systolic_blood_pressure",
        device_class=None,
        unit_of_measurement="mmHg",
        attributes={},
        state="120",
    )
    assert result.metric is None
    assert result.outcome == "denylisted"


# ---------------------------------------------------------------------------
# rules-extra extension
# ---------------------------------------------------------------------------


def test_rules_extra_extends_table() -> None:
    extra = (WellnessRule(entity_token="spo2", unit="%", metric="spo2"),)
    clf = _classifier(extra_rules=extra)
    assert (
        clf.classify(
            entity_id="sensor.oximeter_spo2",
            device_class=None,
            unit_of_measurement="%",
            attributes={},
            state="97",
        )
        == "spo2"
    )


def test_extra_rules_do_not_mutate_defaults() -> None:
    extra = (WellnessRule(entity_token="spo2", unit="%", metric="spo2"),)
    _classifier(extra_rules=extra)
    # The module-level default table must be unaffected.
    assert all(r.metric != "spo2" for r in DEFAULT_WELLNESS_RULES)


# ---------------------------------------------------------------------------
# classify_detailed outcomes
# ---------------------------------------------------------------------------


def test_classify_detailed_promoted() -> None:
    clf = _classifier()
    res = clf.classify_detailed(
        entity_id="sensor.oura_heart_rate",
        device_class=None,
        unit_of_measurement="bpm",
        attributes={},
        state="62",
    )
    assert res.metric == "heart_rate"
    assert res.outcome == "promoted"
    assert res.value == 62.0


def test_classify_detailed_skipped_non_numeric() -> None:
    clf = _classifier()
    res = clf.classify_detailed(
        entity_id="sensor.oura_heart_rate",
        device_class=None,
        unit_of_measurement="bpm",
        attributes={},
        state="unavailable",
    )
    assert res.metric is None
    assert res.outcome == "skipped_non_numeric"


def test_classify_detailed_no_match() -> None:
    clf = _classifier()
    res = clf.classify_detailed(
        entity_id="sensor.living_room_temperature",
        device_class="temperature",
        unit_of_measurement="°C",
        attributes={},
        state="22.0",
    )
    assert res.metric is None
    assert res.outcome == "no_match"


# ---------------------------------------------------------------------------
# Envelope builder (ADR-4)
# ---------------------------------------------------------------------------


def test_build_wellness_envelope_shape() -> None:
    env = build_wellness_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        metric="blood_pressure_systolic",
        value=120.0,
        unit="mmHg",
        device_class=None,
        friendly_name="Withings Systolic",
        new_state={"state": "120"},
    )

    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["channel"] == "wellness"
    assert env["source"]["provider"] == "home_assistant"
    assert env["source"]["endpoint_identity"] == _ENDPOINT
    # Same external_event_id as the home_assistant emission.
    assert env["event"]["external_event_id"].startswith("ha:")
    assert "systolic_blood_pressure" in env["event"]["external_event_id"]

    wm = env["payload"]["raw"]["wellness_measurement"]
    assert wm["metric"] == "blood_pressure_systolic"
    assert wm["value"] == 120.0
    assert wm["unit"] == "mmHg"
    assert wm["valid_at"] == "2026-06-12T14:30:00+00:00"
    assert wm["source_entity_id"] == "sensor.withings_systolic_blood_pressure"
    assert wm["device_class"] is None
    # full HA context preserved alongside the normalized block
    assert "ha_event" in env["payload"]["raw"]
    assert "120" in env["payload"]["normalized_text"]
    assert "mmHg" in env["payload"]["normalized_text"]


def test_wellness_envelope_shares_external_event_id_with_ha_envelope() -> None:
    from butlers.connectors.home_assistant_envelope import build_state_changed_envelope

    ha = build_state_changed_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        new_state={"state": "120"},
        unit_of_measurement="mmHg",
    )
    well = build_wellness_envelope(
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        metric="blood_pressure_systolic",
        value=120.0,
        unit="mmHg",
        device_class=None,
        new_state={"state": "120"},
    )
    assert ha["event"]["external_event_id"] == well["event"]["external_event_id"]


# ---------------------------------------------------------------------------
# Config from_env (ADR-2)
# ---------------------------------------------------------------------------


def test_config_defaults_promotion_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    for k in (
        "HA_WELLNESS_PROMOTION_ENABLED",
        "HA_WELLNESS_RULES_EXTRA",
        "HA_WELLNESS_ENTITY_DENYLIST",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = HAConnectorConfig.from_env()
    assert cfg.wellness_promotion_enabled is True
    assert cfg.wellness_rules_extra == ()
    assert cfg.wellness_entity_denylist == frozenset()


def test_config_promotion_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    monkeypatch.setenv("HA_WELLNESS_PROMOTION_ENABLED", "false")
    cfg = HAConnectorConfig.from_env()
    assert cfg.wellness_promotion_enabled is False


def test_config_rules_extra_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    monkeypatch.setenv(
        "HA_WELLNESS_RULES_EXTRA",
        '[{"entity_token": "spo2", "unit": "%", "metric": "spo2"}]',
    )
    cfg = HAConnectorConfig.from_env()
    assert len(cfg.wellness_rules_extra) == 1
    rule = cfg.wellness_rules_extra[0]
    assert rule.metric == "spo2"
    assert rule.unit == "%"
    assert rule.entity_token == "spo2"


def test_config_rules_extra_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    monkeypatch.setenv("HA_WELLNESS_RULES_EXTRA", "{not json")
    with pytest.raises(ValueError, match="HA_WELLNESS_RULES_EXTRA"):
        HAConnectorConfig.from_env()


def test_config_rules_extra_missing_metric_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    monkeypatch.setenv("HA_WELLNESS_RULES_EXTRA", '[{"unit": "%"}]')
    with pytest.raises(ValueError, match="metric"):
        HAConnectorConfig.from_env()


def test_config_denylist_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://sb")
    monkeypatch.setenv("HA_WELLNESS_ENTITY_DENYLIST", "sensor.a, sensor.b")
    cfg = HAConnectorConfig.from_env()
    assert cfg.wellness_entity_denylist == frozenset({"sensor.a", "sensor.b"})


# ---------------------------------------------------------------------------
# Dual-channel emission (ADR-3) — exercised at the dispatch helper seam
# ---------------------------------------------------------------------------


@pytest.fixture
def emit_helper():
    """Import the dispatch-level dual-emission helper under test."""
    from butlers.connectors.home_assistant import emit_with_wellness_promotion

    return emit_with_wellness_promotion


async def test_dual_emission_ordering(emit_helper) -> None:
    calls: list[str] = []

    async def call_tool(tool: str, env: dict[str, Any]) -> None:
        calls.append(env["source"]["channel"])

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=call_tool)
    clf = WellnessClassifier()
    metrics = MagicMock()

    ha_env = {
        "source": {"channel": "home_assistant", "provider": "home_assistant"},
        "event": {"external_event_id": "ha:sensor.x:1"},
    }

    submitted_all = await emit_helper(
        mcp_client=client,
        ha_envelope=ha_env,
        classifier=clf,
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={"event_type": "state_changed"},
        device_class=None,
        unit_of_measurement="mmHg",
        attributes={},
        new_state={"state": "120"},
        friendly_name=None,
        metrics=metrics,
        promotion_enabled=True,
    )

    assert submitted_all is True
    assert calls == ["home_assistant", "wellness"]


async def test_no_promotion_when_disabled(emit_helper) -> None:
    calls: list[str] = []

    async def call_tool(tool: str, env: dict[str, Any]) -> None:
        calls.append(env["source"]["channel"])

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=call_tool)

    submitted_all = await emit_helper(
        mcp_client=client,
        ha_envelope={"source": {"channel": "home_assistant"}, "event": {"external_event_id": "x"}},
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={},
        device_class=None,
        unit_of_measurement="mmHg",
        attributes={},
        new_state={"state": "120"},
        friendly_name=None,
        metrics=MagicMock(),
        promotion_enabled=False,
    )

    assert submitted_all is True
    assert calls == ["home_assistant"]


async def test_no_promotion_when_not_health_shaped(emit_helper) -> None:
    calls: list[str] = []

    async def call_tool(tool: str, env: dict[str, Any]) -> None:
        calls.append(env["source"]["channel"])

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=call_tool)

    await emit_helper(
        mcp_client=client,
        ha_envelope={"source": {"channel": "home_assistant"}, "event": {"external_event_id": "x"}},
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.living_room_temperature",
        time_fired=_TIME_FIRED,
        ha_event={},
        device_class="temperature",
        unit_of_measurement="°C",
        attributes={},
        new_state={"state": "22.0"},
        friendly_name=None,
        metrics=MagicMock(),
        promotion_enabled=True,
    )

    assert calls == ["home_assistant"]


async def test_secondary_failure_returns_false(emit_helper) -> None:
    """Transient wellness submission failure must signal the caller not to
    advance the checkpoint (returns False)."""
    calls: list[str] = []

    async def call_tool(tool: str, env: dict[str, Any]) -> None:
        ch = env["source"]["channel"]
        calls.append(ch)
        if ch == "wellness":
            raise RuntimeError("transient")

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=call_tool)

    submitted_all = await emit_helper(
        mcp_client=client,
        ha_envelope={
            "source": {"channel": "home_assistant", "provider": "home_assistant"},
            "event": {"external_event_id": "ha:sensor.x:1"},
        },
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={},
        device_class=None,
        unit_of_measurement="mmHg",
        attributes={},
        new_state={"state": "120"},
        friendly_name=None,
        metrics=MagicMock(),
        promotion_enabled=True,
    )

    assert submitted_all is False
    assert calls == ["home_assistant", "wellness"]


async def test_primary_failure_returns_false_no_wellness(emit_helper) -> None:
    calls: list[str] = []

    async def call_tool(tool: str, env: dict[str, Any]) -> None:
        ch = env["source"]["channel"]
        calls.append(ch)
        if ch == "home_assistant":
            raise RuntimeError("transient")

    client = MagicMock()
    client.call_tool = AsyncMock(side_effect=call_tool)

    submitted_all = await emit_helper(
        mcp_client=client,
        ha_envelope={
            "source": {"channel": "home_assistant", "provider": "home_assistant"},
            "event": {"external_event_id": "ha:sensor.x:1"},
        },
        classifier=WellnessClassifier(),
        endpoint_identity=_ENDPOINT,
        entity_id="sensor.withings_systolic_blood_pressure",
        time_fired=_TIME_FIRED,
        ha_event={},
        device_class=None,
        unit_of_measurement="mmHg",
        attributes={},
        new_state={"state": "120"},
        friendly_name=None,
        metrics=MagicMock(),
        promotion_enabled=True,
    )

    # Primary failed: do not attempt wellness, signal no checkpoint advance.
    assert submitted_all is False
    assert calls == ["home_assistant"]
