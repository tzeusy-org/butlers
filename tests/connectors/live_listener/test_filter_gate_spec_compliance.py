"""Tests for GAP-3 and GAP-4 spec-compliance fixes in filter_gate and connector.

GAP-3: One-time WARNING log for non-mic_id rule types (warn_non_mic_id_rules).
GAP-4: Initial filter load before audio capture (ensure_loaded called in _pipeline_once).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.live_listener.filter_gate import (
    _warned_non_mic_id_rule_ids,
    warn_non_mic_id_rules,
)
from butlers.ingestion_policy import IngestionPolicyEvaluator

pytestmark = pytest.mark.unit

_FILTER_GATE_LOGGER = "butlers.connectors.live_listener.filter_gate"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(rule_id: str, rule_type: str) -> dict[str, Any]:
    """Build a minimal rule dict as returned by the DB loader."""
    return {
        "id": rule_id,
        "rule_type": rule_type,
        "condition": {"mic_id": "*"} if rule_type == "mic_id" else {"domain": "example.com"},
        "action": "pass_through",
        "priority": 0,
        "name": f"test-rule-{rule_id}",
        "created_at": "2026-01-01T00:00:00",
    }


def _make_evaluator_with_rules(rules: list[dict[str, Any]]) -> IngestionPolicyEvaluator:
    """Return an evaluator with rules injected directly (bypasses DB load)."""
    evaluator = IngestionPolicyEvaluator(scope="connector:live-listener:mic:kitchen", db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = 1.0  # mark as loaded
    return evaluator


# ---------------------------------------------------------------------------
# GAP-3: warn_non_mic_id_rules
# ---------------------------------------------------------------------------


class TestWarnNonMicIdRules:
    """GAP-3: one-time WARNING per filter ID for non-mic_id rule types."""

    def setup_method(self) -> None:
        """Clear the module-level warned set between tests."""
        _warned_non_mic_id_rule_ids.clear()

    def test_no_warning_for_mic_id_rules(self, caplog: pytest.LogCaptureFixture) -> None:
        """mic_id rules produce no warning."""
        evaluator = _make_evaluator_with_rules([_make_rule("rule-1", "mic_id")])
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
        assert not any("source_key_type" in r.message for r in caplog.records)

    def test_warning_for_non_mic_id_rule(self, caplog: pytest.LogCaptureFixture) -> None:
        """A non-mic_id rule type triggers a WARNING log."""
        evaluator = _make_evaluator_with_rules([_make_rule("rule-99", "sender_domain")])
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
        matching = [r for r in caplog.records if "rule-99" in r.message]
        assert len(matching) == 1
        assert "sender_domain" in matching[0].message

    def test_warning_only_once_per_rule_id(self, caplog: pytest.LogCaptureFixture) -> None:
        """Calling warn_non_mic_id_rules twice emits the warning only once per rule ID."""
        evaluator = _make_evaluator_with_rules([_make_rule("rule-42", "chat_id")])
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
            warn_non_mic_id_rules(evaluator)
        matching = [r for r in caplog.records if "rule-42" in r.message]
        assert len(matching) == 1, "warning should fire exactly once per rule ID"

    def test_warning_per_distinct_rule_id(self, caplog: pytest.LogCaptureFixture) -> None:
        """Each distinct non-mic_id rule ID gets its own warning."""
        evaluator = _make_evaluator_with_rules(
            [
                _make_rule("rule-a", "sender_domain"),
                _make_rule("rule-b", "chat_id"),
                _make_rule("rule-c", "mic_id"),  # should not warn
            ]
        )
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
        rule_a_warns = [r for r in caplog.records if "rule-a" in r.message]
        rule_b_warns = [r for r in caplog.records if "rule-b" in r.message]
        rule_c_warns = [r for r in caplog.records if "rule-c" in r.message]
        assert len(rule_a_warns) == 1
        assert len(rule_b_warns) == 1
        assert len(rule_c_warns) == 0

    def test_warning_includes_scope(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning message includes the evaluator scope for debuggability."""
        evaluator = _make_evaluator_with_rules([_make_rule("rule-xyz", "channel_id")])
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
        matching = [r for r in caplog.records if "rule-xyz" in r.message]
        assert len(matching) == 1
        assert "connector:live-listener:mic:kitchen" in matching[0].message

    def test_empty_rule_set_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """No rules → no warnings."""
        evaluator = _make_evaluator_with_rules([])
        with caplog.at_level(logging.WARNING, logger=_FILTER_GATE_LOGGER):
            warn_non_mic_id_rules(evaluator)
        assert len(caplog.records) == 0

    def test_rule_with_no_id_skipped_gracefully(self, caplog: pytest.LogCaptureFixture) -> None:
        """A rule dict missing 'id' is silently skipped (no crash, no warning)."""
        rule_no_id = {
            "rule_type": "sender_domain",
            "condition": {"domain": "example.com"},
            "action": "pass_through",
            "priority": 0,
        }
        evaluator = _make_evaluator_with_rules([rule_no_id])
        # Should not raise
        warn_non_mic_id_rules(evaluator)
        # No warning emitted because rule_id is falsy (None/"")
        matching = [r for r in caplog.records if "source_key_type" in r.message]
        assert len(matching) == 0


# ---------------------------------------------------------------------------
# GAP-4: ensure_loaded called before MicPipeline
# ---------------------------------------------------------------------------


class TestEnsureLoadedBeforeAudioCapture:
    """GAP-4: ensure_loaded() must be called before audio capture begins."""

    async def test_pipeline_once_calls_ensure_loaded_before_mic_pipeline(self) -> None:
        """ensure_loaded() is called before MicPipeline is opened."""
        from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
        from butlers.connectors.live_listener.connector import LiveListenerConnector
        from butlers.connectors.live_listener.metrics import LiveListenerMetrics

        config = LiveListenerConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            devices=[MicDeviceSpec(name="kitchen", device="hw:0")],
            transcription_url="tcp://localhost:10300",
            reconnect_base_s=0.01,
            reconnect_max_s=0.1,
            ring_buffer_seconds=1.0,
        )
        mock_mcp = AsyncMock()
        connector = LiveListenerConnector(config=config, mcp_client=mock_mcp)
        # Pre-populate per-mic components that _pipeline_once expects (normally set in start()).
        connector._ll_metrics["kitchen"] = LiveListenerMetrics(mic="kitchen")

        spec = MicDeviceSpec(name="kitchen", device="hw:0")

        call_order: list[str] = []

        mock_evaluator = AsyncMock(spec=IngestionPolicyEvaluator)
        mock_evaluator.scope = "connector:live-listener:mic:kitchen"
        mock_evaluator.rules = []

        async def fake_ensure_loaded() -> None:
            call_order.append("ensure_loaded")

        mock_evaluator.ensure_loaded = fake_ensure_loaded

        class FakeMicPipeline:
            """Minimal MicPipeline stand-in that records when it's entered."""

            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> FakeMicPipeline:
                call_order.append("mic_pipeline_enter")
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        import asyncio

        with (
            patch(
                "butlers.connectors.live_listener.connector.create_filter_evaluator",
                return_value=mock_evaluator,
            ),
            patch(
                "butlers.connectors.live_listener.connector.warn_non_mic_id_rules",
            ),
            patch(
                "butlers.connectors.live_listener.audio.MicPipeline",
                FakeMicPipeline,
            ),
        ):
            task = asyncio.create_task(connector._pipeline_once(spec))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert "ensure_loaded" in call_order, "ensure_loaded was not called"
        assert "mic_pipeline_enter" in call_order, "MicPipeline was never entered"
        ensure_idx = call_order.index("ensure_loaded")
        mic_idx = call_order.index("mic_pipeline_enter")
        assert ensure_idx < mic_idx, (
            f"ensure_loaded (pos {ensure_idx}) must precede mic_pipeline_enter (pos {mic_idx})"
        )

    async def test_warn_non_mic_id_rules_called_after_ensure_loaded(self) -> None:
        """warn_non_mic_id_rules is called after ensure_loaded completes."""
        from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
        from butlers.connectors.live_listener.connector import LiveListenerConnector
        from butlers.connectors.live_listener.metrics import LiveListenerMetrics

        config = LiveListenerConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            devices=[MicDeviceSpec(name="kitchen", device="hw:0")],
            transcription_url="tcp://localhost:10300",
            reconnect_base_s=0.01,
            reconnect_max_s=0.1,
            ring_buffer_seconds=1.0,
        )
        mock_mcp = AsyncMock()
        connector = LiveListenerConnector(config=config, mcp_client=mock_mcp)
        # Pre-populate per-mic components that _pipeline_once expects (normally set in start()).
        connector._ll_metrics["kitchen"] = LiveListenerMetrics(mic="kitchen")
        spec = MicDeviceSpec(name="kitchen", device="hw:0")

        mock_evaluator = AsyncMock(spec=IngestionPolicyEvaluator)
        mock_evaluator.scope = "connector:live-listener:mic:kitchen"
        mock_evaluator.rules = []
        mock_evaluator.ensure_loaded = AsyncMock()

        warn_calls: list[Any] = []

        def fake_warn(ev: Any) -> None:
            warn_calls.append(ev)

        class FakeMicPipeline:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> FakeMicPipeline:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        import asyncio

        with (
            patch(
                "butlers.connectors.live_listener.connector.create_filter_evaluator",
                return_value=mock_evaluator,
            ),
            patch(
                "butlers.connectors.live_listener.connector.warn_non_mic_id_rules",
                side_effect=fake_warn,
            ),
            patch(
                "butlers.connectors.live_listener.audio.MicPipeline",
                FakeMicPipeline,
            ),
        ):
            task = asyncio.create_task(connector._pipeline_once(spec))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_evaluator.ensure_loaded.assert_awaited_once()
        assert len(warn_calls) == 1, "warn_non_mic_id_rules should have been called once"
        assert warn_calls[0] is mock_evaluator
