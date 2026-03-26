"""Tests for the three-layer HA filter pipeline (task 5.6).

Covers openspec/changes/connector-home-assistant/tasks.md §5 (tasks 5.1–5.6):

5.1 — Layer 1: domain allowlist filter
5.2 — Layer 2: significance filter with per-device-class thresholds
5.3 — Significance filter bypass for binary entities and unavailable/unknown transitions
5.4 — Layer 3: DiscretionEvaluator integration
5.5 — Filter pipeline metrics (Prometheus counters)
5.6 — Tests for each layer

No real LLM or DB I/O is performed; the DiscretionEvaluator is injected
with a lightweight stub dispatcher that returns a fixed verdict.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.discretion import DiscretionEvaluator
from butlers.connectors.home_assistant_pipeline import (
    DEFAULT_SIGNIFICANCE_THRESHOLDS,
    HAFilterPipeline,
    HAFilterPipelineConfig,
    SignificanceStateCache,
    filter_layer1_domain,
    filter_layer2_significance,
    filter_layer3_discretion,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWLIST = frozenset(
    {
        "light",
        "switch",
        "sensor",
        "climate",
        "lock",
        "cover",
        "binary_sensor",
        "automation",
        "script",
    }
)

_ENDPOINT_IDENTITY = "home_assistant:ha.local:8123"


def _make_stub_dispatcher(verdict: str = "FORWARD", reason: str = "test") -> MagicMock:
    """Return a mock DiscretionLLMCaller that always returns *verdict*."""
    dispatcher = MagicMock()
    response = f"{verdict}: {reason}" if verdict == "FORWARD" else verdict
    dispatcher.call = AsyncMock(return_value=response)
    return dispatcher


def _make_evaluator(verdict: str = "FORWARD") -> DiscretionEvaluator:
    """Return a DiscretionEvaluator with a fixed stub dispatcher."""
    return DiscretionEvaluator(
        source_name=_ENDPOINT_IDENTITY,
        dispatcher=_make_stub_dispatcher(verdict),
        weight_bypass=1.0,  # HA always uses weight=1.0, so LLM is never called
    )


def _make_pipeline(
    *,
    allowlist: frozenset[str] = _ALLOWLIST,
    thresholds: dict[str, float] | None = None,
    evaluator: DiscretionEvaluator | None = None,
    metrics: Any = None,
) -> HAFilterPipeline:
    """Build a HAFilterPipeline with custom settings."""
    config = HAFilterPipelineConfig(
        domain_allowlist=allowlist,
        significance_thresholds=(
            thresholds if thresholds is not None else dict(DEFAULT_SIGNIFICANCE_THRESHOLDS)
        ),
    )
    return HAFilterPipeline(config=config, evaluator=evaluator, metrics=metrics)


# ---------------------------------------------------------------------------
# SignificanceStateCache
# ---------------------------------------------------------------------------


class TestSignificanceStateCache:
    """Unit tests for the per-entity numeric state cache."""

    def test_empty_on_init(self) -> None:
        cache = SignificanceStateCache()
        assert len(cache) == 0
        assert cache.get("sensor.temp") is None

    def test_set_and_get(self) -> None:
        cache = SignificanceStateCache()
        cache.set("sensor.temp", 21.9)
        assert cache.get("sensor.temp") == 21.9

    def test_multiple_entities(self) -> None:
        cache = SignificanceStateCache()
        cache.set("sensor.temp", 21.9)
        cache.set("sensor.humidity", 55.0)
        assert cache.get("sensor.temp") == 21.9
        assert cache.get("sensor.humidity") == 55.0
        assert len(cache) == 2

    def test_overwrite_existing(self) -> None:
        cache = SignificanceStateCache()
        cache.set("sensor.temp", 21.9)
        cache.set("sensor.temp", 22.0)
        assert cache.get("sensor.temp") == 22.0


# ---------------------------------------------------------------------------
# Layer 1 — Domain allowlist filter (task 5.1)
# ---------------------------------------------------------------------------


class TestLayer1DomainFilter:
    """Layer 1: domain allowlist correctly passes and blocks domains."""

    def test_allowed_domain_returns_none(self) -> None:
        result = filter_layer1_domain("sensor.temp", "sensor", _ALLOWLIST)
        assert result is None

    def test_excluded_domain_returns_filtered(self) -> None:
        result = filter_layer1_domain("media_player.tv", "media_player", _ALLOWLIST)
        assert result is not None
        assert result.verdict == "filtered"
        assert result.stage == "domain_filter"
        assert result.filter_reason == "domain_excluded:media_player"

    def test_all_default_allowlist_domains_pass(self) -> None:
        for domain in _ALLOWLIST:
            entity_id = f"{domain}.test_entity"
            result = filter_layer1_domain(entity_id, domain, _ALLOWLIST)
            assert result is None, f"Domain {domain!r} should be in allowlist"

    def test_various_excluded_domains(self) -> None:
        excluded = [
            "media_player",
            "weather",
            "sun",
            "update",
            "persistent_notification",
            "camera",
            "device_tracker",
            "person",
        ]
        for domain in excluded:
            result = filter_layer1_domain(f"{domain}.test", domain, _ALLOWLIST)
            assert result is not None
            assert result.filter_reason == f"domain_excluded:{domain}"

    def test_empty_allowlist_blocks_all(self) -> None:
        result = filter_layer1_domain("sensor.temp", "sensor", frozenset())
        assert result is not None
        assert result.verdict == "filtered"

    def test_single_domain_allowlist(self) -> None:
        allowlist = frozenset({"light"})
        assert filter_layer1_domain("light.bedroom", "light", allowlist) is None
        result = filter_layer1_domain("sensor.temp", "sensor", allowlist)
        assert result is not None
        assert result.filter_reason == "domain_excluded:sensor"

    def test_filter_reason_format_domain_excluded(self) -> None:
        result = filter_layer1_domain("camera.front_door", "camera", _ALLOWLIST)
        assert result is not None
        assert result.filter_reason.startswith("domain_excluded:")
        domain_part = result.filter_reason.split(":", 1)[1]
        assert domain_part == "camera"


# ---------------------------------------------------------------------------
# Layer 2 — Significance filter (tasks 5.2 and 5.3)
# ---------------------------------------------------------------------------


class TestLayer2SignificanceFilter:
    """Layer 2: significance filter numeric thresholds and bypass rules."""

    # -------------------------------------------------------------------
    # Bypass: binary entities (task 5.3)
    # -------------------------------------------------------------------

    def test_binary_on_off_always_passes(self) -> None:
        cache = SignificanceStateCache()
        # Simulate light on -> off transition
        result = filter_layer2_significance("light.bedroom", "None", "on", "off", cache)
        assert result is None

    def test_binary_open_closed_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("cover.garage", None, "closed", "open", cache)
        assert result is None

    def test_binary_locked_unlocked_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("lock.front_door", None, "locked", "unlocked", cache)
        assert result is None

    def test_binary_home_away_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("device_tracker.person1", None, "home", "away", cache)
        assert result is None

    def test_playing_paused_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("media_player.tv", None, "idle", "playing", cache)
        assert result is None

    # -------------------------------------------------------------------
    # Bypass: unavailable/unknown transitions (task 5.3)
    # -------------------------------------------------------------------

    def test_transition_to_unavailable_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance(
            "sensor.temp", "temperature", "21.9", "unavailable", cache
        )
        assert result is None

    def test_transition_from_unavailable_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance(
            "sensor.temp", "temperature", "unavailable", "22.0", cache
        )
        assert result is None

    def test_transition_to_unknown_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", "unknown", cache)
        assert result is None

    def test_transition_from_unknown_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "unknown", "22.5", cache)
        assert result is None

    def test_none_old_state_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", None, "22.0", cache)
        assert result is None

    def test_none_new_state_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", None, cache)
        assert result is None

    # -------------------------------------------------------------------
    # Bypass: no device class (task 5.2)
    # -------------------------------------------------------------------

    def test_no_device_class_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.custom", None, "100", "101", cache)
        assert result is None

    def test_empty_device_class_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.custom", "", "100", "101", cache)
        assert result is None

    # -------------------------------------------------------------------
    # Bypass: unknown device class
    # -------------------------------------------------------------------

    def test_unknown_device_class_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.custom", "proximity", "5", "6", cache)
        assert result is None

    # -------------------------------------------------------------------
    # Bypass: non-numeric new state
    # -------------------------------------------------------------------

    def test_non_numeric_new_state_always_passes(self) -> None:
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.motion", "motion", "cleared", "detected", cache)
        assert result is None

    # -------------------------------------------------------------------
    # Significance threshold enforcement (task 5.2)
    # -------------------------------------------------------------------

    def test_temperature_above_threshold_passes(self) -> None:
        # Threshold: 0.5; delta = 22.0 - 21.0 = 1.0 > 0.5
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.0", "22.0", cache)
        assert result is None

    def test_temperature_below_threshold_filtered(self) -> None:
        # Threshold: 0.5; delta = 22.0 - 21.9 = 0.1 <= 0.5
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", "22.0", cache)
        assert result is not None
        assert result.verdict == "filtered"
        assert result.stage == "significance_filter"
        assert result.filter_reason.startswith("insignificant_delta:temperature:")

    def test_temperature_at_exactly_threshold_filtered(self) -> None:
        # Threshold: 0.5; delta = 0.5 — exactly at threshold → filtered (not strictly above)
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.5", "22.0", cache)
        assert result is not None
        assert result.verdict == "filtered"

    def test_humidity_above_threshold_passes(self) -> None:
        # Threshold: 2.0; delta = 5.0 > 2.0
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.humidity", "humidity", "50.0", "55.0", cache)
        assert result is None

    def test_humidity_below_threshold_filtered(self) -> None:
        # Threshold: 2.0; delta = 1.0 <= 2.0
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.humidity", "humidity", "50.0", "51.0", cache)
        assert result is not None
        assert result.filter_reason.startswith("insignificant_delta:humidity:")

    def test_energy_above_threshold_passes(self) -> None:
        # Threshold: 0.1; delta = 0.5 > 0.1
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.energy", "energy", "10.0", "10.5", cache)
        assert result is None

    def test_energy_below_threshold_filtered(self) -> None:
        # Threshold: 0.1; delta = 0.05 <= 0.1
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.energy", "energy", "10.0", "10.05", cache)
        assert result is not None
        assert result.filter_reason.startswith("insignificant_delta:energy:")

    def test_illuminance_above_threshold_passes(self) -> None:
        # Threshold: 50.0; delta = 100 > 50
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.lux", "illuminance", "200", "300", cache)
        assert result is None

    def test_illuminance_below_threshold_filtered(self) -> None:
        # Threshold: 50.0; delta = 10 <= 50
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.lux", "illuminance", "200", "210", cache)
        assert result is not None
        assert result.filter_reason.startswith("insignificant_delta:illuminance:")

    def test_filter_reason_includes_delta(self) -> None:
        # Ensure the delta value is encoded in the filter reason
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", "22.0", cache)
        assert result is not None
        # filter_reason = "insignificant_delta:temperature:0.1"
        parts = result.filter_reason.split(":")
        assert parts[0] == "insignificant_delta"
        assert parts[1] == "temperature"
        assert parts[2] == "0.1"

    # -------------------------------------------------------------------
    # Cache update behaviour
    # -------------------------------------------------------------------

    def test_cache_updated_when_passed(self) -> None:
        cache = SignificanceStateCache()
        # Large delta passes significance
        filter_layer2_significance("sensor.temp", "temperature", "20.0", "25.0", cache)
        assert cache.get("sensor.temp") == 25.0

    def test_cache_updated_even_when_filtered(self) -> None:
        # Even insignificant changes update the cache to track latest value
        cache = SignificanceStateCache()
        filter_layer2_significance("sensor.temp", "temperature", "21.9", "22.0", cache)
        # The new value (22.0) should be stored
        assert cache.get("sensor.temp") == 22.0

    def test_no_previous_cached_value_passes(self) -> None:
        # First event with no prior cached value → always pass
        cache = SignificanceStateCache()
        result = filter_layer2_significance("sensor.temp", "temperature", None, "22.0", cache)
        assert result is None
        assert cache.get("sensor.temp") == 22.0

    def test_cache_used_for_delta_computation(self) -> None:
        # The cache value should take precedence over old_state_str for delta
        cache = SignificanceStateCache()
        cache.set("sensor.temp", 20.0)  # Cached value differs from old_state_str
        # Delta from cache: |22.0 - 20.0| = 2.0 > 0.5 threshold → pass
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", "22.0", cache)
        assert result is None  # Large delta from cached value

    def test_old_state_str_used_when_no_cached_value(self) -> None:
        # When no cache entry exists, use old_state_str for delta
        cache = SignificanceStateCache()
        # Delta: |22.0 - 21.9| = 0.1 ≤ 0.5 threshold → filtered
        result = filter_layer2_significance("sensor.temp", "temperature", "21.9", "22.0", cache)
        assert result is not None
        assert result.verdict == "filtered"

    def test_custom_thresholds_respected(self) -> None:
        # Use a very tight threshold: 0.01
        cache = SignificanceStateCache()
        custom_thresholds = {"temperature": 0.01}
        # Delta = 0.1 > 0.01 → pass with tight threshold
        result = filter_layer2_significance(
            "sensor.temp", "temperature", "21.9", "22.0", cache, custom_thresholds
        )
        assert result is None

    def test_custom_thresholds_blocks_small_delta(self) -> None:
        # Use a very large threshold: 10.0
        cache = SignificanceStateCache()
        custom_thresholds = {"temperature": 10.0}
        # Delta = 1.0 ≤ 10.0 → filtered with large threshold
        result = filter_layer2_significance(
            "sensor.temp", "temperature", "20.0", "21.0", cache, custom_thresholds
        )
        assert result is not None
        assert result.verdict == "filtered"


# ---------------------------------------------------------------------------
# Layer 3 — Discretion evaluator (task 5.4)
# ---------------------------------------------------------------------------


class TestLayer3Discretion:
    """Layer 3: DiscretionEvaluator integration with HA owner-equivalent weight."""

    async def test_forward_verdict_returns_none(self) -> None:
        evaluator = _make_evaluator("FORWARD")
        result = await filter_layer3_discretion(
            entity_id="sensor.temp",
            normalized_text="Living Room Temperature: 21.9 -> 22.5 °C",
            evaluator=evaluator,
        )
        assert result is None

    async def test_ignore_verdict_returns_filtered(self) -> None:
        """IGNORE verdict from discretion (when weight does NOT trigger bypass) → filtered.

        The weight-bypass path requires weight >= weight_bypass.  To trigger the
        LLM (and thus get IGNORE), we set weight_bypass=2.0 so that weight=1.0
        falls through to the LLM call.
        """
        dispatcher = _make_stub_dispatcher("IGNORE")
        evaluator = DiscretionEvaluator(
            source_name=_ENDPOINT_IDENTITY,
            dispatcher=dispatcher,
            weight_bypass=2.0,  # Requires weight >= 2.0 for bypass; weight=1.0 → LLM called
            weight_fail_open=0.5,
        )
        result = await filter_layer3_discretion(
            entity_id="light.bedroom",
            normalized_text="Bedroom: off -> on",
            evaluator=evaluator,
        )
        assert result is not None
        assert result.verdict == "filtered"
        assert result.stage == "discretion"
        assert result.filter_reason == "discretion_ignore"

    async def test_weight_1_bypass_skips_llm_call(self) -> None:
        """HA events use weight=1.0, which triggers the weight-bypass path.

        With weight_bypass=1.0, the evaluator never calls the dispatcher LLM
        and always returns FORWARD.  Verify the dispatcher is NOT called.
        """
        dispatcher = _make_stub_dispatcher("IGNORE")  # Would return IGNORE if called
        evaluator = DiscretionEvaluator(
            source_name=_ENDPOINT_IDENTITY,
            dispatcher=dispatcher,
            weight_bypass=1.0,
        )
        result = await filter_layer3_discretion(
            entity_id="sensor.temp",
            normalized_text="Temperature change",
            evaluator=evaluator,
        )
        # Because weight=1.0 >= weight_bypass=1.0, LLM is bypassed → FORWARD
        assert result is None
        dispatcher.call.assert_not_awaited()

    async def test_context_entry_added_to_window(self) -> None:
        evaluator = _make_evaluator("FORWARD")
        assert len(evaluator.window) == 0

        await filter_layer3_discretion(
            entity_id="sensor.temp",
            normalized_text="Temperature: 21.9 -> 22.5",
            evaluator=evaluator,
        )
        # Even with bypass, the entry is appended to the context window
        assert len(evaluator.window) == 1

    async def test_timestamp_forwarded_to_evaluator(self) -> None:
        """Verify that the timestamp parameter is forwarded to the ContextEntry.

        Use a recent timestamp (close to now) to ensure the entry is not pruned
        by the ContextWindow's max_age_seconds constraint.
        """
        import time as _time

        evaluator = _make_evaluator("FORWARD")
        ts = _time.time()  # Use current time so entry is within the age window

        await filter_layer3_discretion(
            entity_id="sensor.temp",
            normalized_text="Test",
            evaluator=evaluator,
            time_fired_ts=ts,
        )
        entries = evaluator.window.entries
        assert len(entries) == 1
        assert entries[0].timestamp == ts


# ---------------------------------------------------------------------------
# Full pipeline integration (HAFilterPipeline)
# ---------------------------------------------------------------------------


class TestHAFilterPipeline:
    """Integration tests for the complete three-layer pipeline."""

    async def test_allowed_domain_numeric_significant_passes(self) -> None:
        """Temperature delta > threshold + allowed domain → pass all layers."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="sensor.living_room_temperature",
            domain="sensor",
            device_class="temperature",
            old_state_str="20.0",
            new_state_str="21.5",  # delta=1.5 > threshold=0.5
        )
        assert result.verdict == "pass"
        assert result.stage == "passed"

    async def test_excluded_domain_blocked_at_layer1(self) -> None:
        """Media player domain → blocked at Layer 1."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="media_player.tv",
            domain="media_player",
            old_state_str="off",
            new_state_str="playing",
        )
        assert result.verdict == "filtered"
        assert result.stage == "domain_filter"
        assert result.filter_reason == "domain_excluded:media_player"

    async def test_small_temp_delta_blocked_at_layer2(self) -> None:
        """Temperature delta ≤ threshold (0.5) → blocked at Layer 2."""
        pipeline = _make_pipeline()
        # Prime the cache so delta can be computed
        pipeline.state_cache.set("sensor.temp", 21.9)
        result = await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str="21.9",
            new_state_str="22.0",  # delta=0.1 ≤ 0.5
        )
        assert result.verdict == "filtered"
        assert result.stage == "significance_filter"
        assert result.filter_reason.startswith("insignificant_delta:temperature:")

    async def test_binary_sensor_always_passes_layer2(self) -> None:
        """Binary sensor (on/off) always passes Layer 2."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="binary_sensor.motion",
            domain="binary_sensor",
            device_class="motion",
            old_state_str="off",
            new_state_str="on",
        )
        assert result.verdict == "pass"

    async def test_unavailable_transition_passes_layer2(self) -> None:
        """Transition to 'unavailable' always passes Layer 2."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str="22.0",
            new_state_str="unavailable",
        )
        assert result.verdict == "pass"

    async def test_lock_transition_passes_all_layers(self) -> None:
        """Lock state change (binary) passes all layers."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="lock.front_door",
            domain="lock",
            old_state_str="locked",
            new_state_str="unlocked",
        )
        assert result.verdict == "pass"

    async def test_discretion_ignore_blocked_at_layer3(self) -> None:
        """Events that pass Layers 1+2 but get IGNORE from discretion → filtered."""
        dispatcher = _make_stub_dispatcher("IGNORE")
        evaluator = DiscretionEvaluator(
            source_name=_ENDPOINT_IDENTITY,
            dispatcher=dispatcher,
            weight_bypass=2.0,  # weight_bypass > 1.0 so LLM IS called for weight=1.0
            weight_fail_open=0.5,
        )
        pipeline = _make_pipeline(evaluator=evaluator)

        result = await pipeline.run(
            entity_id="light.bedroom",
            domain="light",
            old_state_str="off",
            new_state_str="on",
            normalized_text="Bedroom: off -> on",
        )
        assert result.verdict == "filtered"
        assert result.stage == "discretion"
        assert result.filter_reason == "discretion_ignore"

    async def test_no_evaluator_skips_layer3(self) -> None:
        """When no evaluator is provided, Layer 3 is skipped and event passes."""
        pipeline = _make_pipeline(evaluator=None)
        result = await pipeline.run(
            entity_id="light.bedroom",
            domain="light",
            old_state_str="off",
            new_state_str="on",
        )
        assert result.verdict == "pass"

    async def test_domain_derived_from_entity_id(self) -> None:
        """Domain is correctly derived from entity_id when not explicitly provided."""
        pipeline = _make_pipeline()
        # No domain argument — should derive "sensor" from "sensor.temp"
        result = await pipeline.run(
            entity_id="sensor.temp",
            device_class="temperature",
            old_state_str="20.0",
            new_state_str="22.0",  # delta=2.0 > 0.5
        )
        assert result.verdict == "pass"

    async def test_excluded_domain_derived_from_entity_id(self) -> None:
        """Domain derived from entity_id, excluded domain still blocked."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="weather.home",
            # domain not provided — derived as "weather"
        )
        assert result.verdict == "filtered"
        assert result.stage == "domain_filter"
        assert result.filter_reason == "domain_excluded:weather"

    async def test_state_cache_persists_across_events(self) -> None:
        """Consecutive events for same entity use cached previous value."""
        pipeline = _make_pipeline()

        # First event: no cache → passes
        r1 = await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str=None,
            new_state_str="22.0",
        )
        assert r1.verdict == "pass"
        assert pipeline.state_cache.get("sensor.temp") == 22.0

        # Second event: small delta from cached 22.0 → filtered
        r2 = await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str="22.0",
            new_state_str="22.2",  # delta=0.2 ≤ 0.5 threshold
        )
        assert r2.verdict == "filtered"
        assert r2.stage == "significance_filter"

        # Third event: large delta → passes
        r3 = await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str="22.2",
            new_state_str="24.0",  # delta=1.8 > 0.5 threshold from cached 22.2
        )
        assert r3.verdict == "pass"

    async def test_automation_passes_all_layers(self) -> None:
        """Automation triggers (no numeric state) pass all layers."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="automation.morning_lights",
            domain="automation",
            normalized_text="Automation triggered: Morning Lights",
        )
        assert result.verdict == "pass"

    async def test_script_passes_all_layers(self) -> None:
        """Script entities pass all layers."""
        pipeline = _make_pipeline()
        result = await pipeline.run(
            entity_id="script.wake_up_sequence",
            domain="script",
        )
        assert result.verdict == "pass"


# ---------------------------------------------------------------------------
# Filter pipeline metrics (task 5.5)
# ---------------------------------------------------------------------------


class TestFilterPipelineMetrics:
    """Verify Prometheus counter increments for each pipeline outcome."""

    def _make_mock_metrics(self) -> MagicMock:
        """Return a MagicMock shaped like HAConnectorMetrics."""
        m = MagicMock()
        m.inc_events = MagicMock()
        m.inc_discretion = MagicMock()
        m.observe_filter_pipeline = MagicMock()
        return m

    async def test_domain_excluded_increments_domain_filter_filtered(self) -> None:
        metrics = self._make_mock_metrics()
        pipeline = _make_pipeline(metrics=metrics)

        await pipeline.run(entity_id="media_player.tv", domain="media_player")

        metrics.inc_events.assert_called_once_with("domain_filter", "filtered")

    async def test_significance_filtered_increments_both_stages(self) -> None:
        metrics = self._make_mock_metrics()
        pipeline = _make_pipeline(metrics=metrics)
        pipeline.state_cache.set("sensor.temp", 21.9)

        await pipeline.run(
            entity_id="sensor.temp",
            domain="sensor",
            device_class="temperature",
            old_state_str="21.9",
            new_state_str="22.0",
        )

        calls = [c[0] for c in metrics.inc_events.call_args_list]
        assert ("domain_filter", "passed") in calls
        assert ("significance_filter", "filtered") in calls

    async def test_all_passed_increments_all_three_stages(self) -> None:
        metrics = self._make_mock_metrics()
        pipeline = _make_pipeline(metrics=metrics)

        await pipeline.run(
            entity_id="light.bedroom",
            domain="light",
            old_state_str="off",
            new_state_str="on",
        )

        calls = [c[0] for c in metrics.inc_events.call_args_list]
        assert ("domain_filter", "passed") in calls
        assert ("significance_filter", "passed") in calls
        assert ("discretion", "passed") in calls

    async def test_pipeline_timing_observed(self) -> None:
        metrics = self._make_mock_metrics()
        pipeline = _make_pipeline(metrics=metrics)

        await pipeline.run(entity_id="light.bedroom", domain="light")

        metrics.observe_filter_pipeline.assert_called_once()
        elapsed = metrics.observe_filter_pipeline.call_args[0][0]
        assert isinstance(elapsed, float)
        assert elapsed >= 0.0

    async def test_discretion_ignored_increments_ignore_verdict(self) -> None:
        metrics = self._make_mock_metrics()
        dispatcher = _make_stub_dispatcher("IGNORE")
        evaluator = DiscretionEvaluator(
            source_name=_ENDPOINT_IDENTITY,
            dispatcher=dispatcher,
            weight_bypass=2.0,  # Force LLM call for weight=1.0
            weight_fail_open=0.5,
        )
        pipeline = _make_pipeline(evaluator=evaluator, metrics=metrics)

        await pipeline.run(
            entity_id="light.bedroom",
            domain="light",
            old_state_str="off",
            new_state_str="on",
            normalized_text="Bedroom: off -> on",
        )

        metrics.inc_discretion.assert_called_once_with(verdict="ignore")

    async def test_discretion_forwarded_increments_forward_verdict(self) -> None:
        metrics = self._make_mock_metrics()
        dispatcher = _make_stub_dispatcher("FORWARD")
        evaluator = DiscretionEvaluator(
            source_name=_ENDPOINT_IDENTITY,
            dispatcher=dispatcher,
            weight_bypass=2.0,  # Force LLM call for weight=1.0
            weight_fail_open=0.5,
        )
        pipeline = _make_pipeline(evaluator=evaluator, metrics=metrics)

        await pipeline.run(
            entity_id="light.bedroom",
            domain="light",
            old_state_str="off",
            new_state_str="on",
            normalized_text="Bedroom: off -> on",
        )

        metrics.inc_discretion.assert_called_once_with(verdict="forward")

    async def test_no_metrics_no_error(self) -> None:
        """Passing metrics=None does not raise."""
        pipeline = _make_pipeline(metrics=None)
        result = await pipeline.run(entity_id="light.bedroom", domain="light")
        assert result.verdict == "pass"


# ---------------------------------------------------------------------------
# HAFilterPipelineConfig
# ---------------------------------------------------------------------------


class TestHAFilterPipelineConfig:
    """Verify config defaults and customization."""

    def test_default_domain_allowlist(self) -> None:
        config = HAFilterPipelineConfig()
        expected = {
            "light",
            "switch",
            "sensor",
            "climate",
            "lock",
            "cover",
            "binary_sensor",
            "automation",
            "script",
        }
        assert config.domain_allowlist == frozenset(expected)

    def test_default_significance_thresholds(self) -> None:
        config = HAFilterPipelineConfig()
        assert config.significance_thresholds["temperature"] == 0.5
        assert config.significance_thresholds["humidity"] == 2.0
        assert config.significance_thresholds["energy"] == 0.1
        assert config.significance_thresholds["illuminance"] == 50.0

    def test_custom_allowlist(self) -> None:
        config = HAFilterPipelineConfig(domain_allowlist=frozenset({"light", "lock"}))
        assert config.domain_allowlist == frozenset({"light", "lock"})

    def test_custom_thresholds_override(self) -> None:
        custom = {"temperature": 1.0, "humidity": 5.0}
        config = HAFilterPipelineConfig(significance_thresholds=custom)
        assert config.significance_thresholds["temperature"] == 1.0
        assert config.significance_thresholds["humidity"] == 5.0

    def test_pipeline_uses_config(self) -> None:
        config = HAFilterPipelineConfig(
            domain_allowlist=frozenset({"light"}),
        )
        pipeline = HAFilterPipeline(config=config)
        assert pipeline._config.domain_allowlist == frozenset({"light"})
