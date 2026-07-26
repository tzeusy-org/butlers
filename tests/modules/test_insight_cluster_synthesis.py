"""Unit tests for the insight broker's best-effort LLM cluster synthesis
(bu-ep4ks.9 slice 3) and the hold-until-first-active daily cadence's fallback
deadline helper (slice 5).

These are pure unit tests (no Docker): they patch the model-routing/runtime
primitives ``_synthesize_cluster_sentence`` calls lazily inside the broker,
matching the mocking pattern used for ``get_suppressing_context_signal`` in
``test_insight_context_bus_suppression.py`` — patch the SOURCE module
(``butlers.core.model_routing`` / ``butlers.core.runtimes.base``), not the
broker's re-export, since the import happens inside the function body at
call time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.unit]


def _catalog_result(runtime_type: str = "api"):
    # (runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s, effective_tier)
    return (runtime_type, "test-cheap-model", [], uuid4(), 30, "cheap")


def _quota(allowed: bool = True):
    from butlers.core.model_routing import QuotaStatus

    return QuotaStatus(allowed, 0, None, 0, None)


class TestClusterSynthesis:
    async def test_successful_synthesis_returns_sentence(self):
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "travel", "message": "Flight to Tokyo departs Tuesday"},
            {"origin_butler": "finance", "message": "Card payment due Tuesday"},
        ]

        mock_adapter = AsyncMock()
        mock_adapter.invoke = AsyncMock(
            return_value=(
                "Both relate to the Tokyo trip.",
                [],
                {"input_tokens": 50, "output_tokens": 8},
            )
        )

        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result()),
            ),
            patch(
                "butlers.core.model_routing.check_token_quota",
                new=AsyncMock(return_value=_quota()),
            ),
            patch(
                "butlers.core.model_routing.record_token_usage",
                new=AsyncMock(),
            ) as mock_record,
            patch("butlers.core.runtimes.base.create_adapter", return_value=mock_adapter),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)

        assert result == "Both relate to the Tokyo trip."
        mock_record.assert_awaited_once()
        assert mock_record.call_args.kwargs["purpose"] == "insight_cluster_synthesis"

    async def test_no_catalog_entry_fails_open(self):
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        with patch(
            "butlers.core.model_routing.resolve_model_with_effective_tier",
            new=AsyncMock(return_value=None),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result is None

    async def test_non_api_runtime_fails_open_without_touching_adapter(self):
        """A resolved runtime other than the direct-API lane (e.g. a CLI
        subprocess adapter) must never be invoked inline — synthesis bails
        out before ever constructing an adapter."""
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result(runtime_type="opencode")),
            ),
            patch("butlers.core.runtimes.base.create_adapter") as mock_create_adapter,
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result is None
        mock_create_adapter.assert_not_called()

    async def test_over_quota_fails_open(self):
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result()),
            ),
            patch(
                "butlers.core.model_routing.check_token_quota",
                new=AsyncMock(return_value=_quota(allowed=False)),
            ),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result is None

    async def test_adapter_exception_fails_open(self):
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        mock_adapter = AsyncMock()
        mock_adapter.invoke = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result()),
            ),
            patch(
                "butlers.core.model_routing.check_token_quota",
                new=AsyncMock(return_value=_quota()),
            ),
            patch("butlers.core.runtimes.base.create_adapter", return_value=mock_adapter),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result is None

    async def test_blank_response_fails_open(self):
        from butlers.tools.switchboard.insight.broker import _synthesize_cluster_sentence

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        mock_adapter = AsyncMock()
        mock_adapter.invoke = AsyncMock(return_value=("   ", [], None))
        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result()),
            ),
            patch(
                "butlers.core.model_routing.check_token_quota",
                new=AsyncMock(return_value=_quota()),
            ),
            patch("butlers.core.runtimes.base.create_adapter", return_value=mock_adapter),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result is None

    async def test_long_response_truncated_to_first_line_and_max_chars(self):
        from butlers.tools.switchboard.insight.broker import (
            _SYNTHESIS_MAX_SENTENCE_CHARS,
            _synthesize_cluster_sentence,
        )

        cluster = [
            {"origin_butler": "a", "message": "x"},
            {"origin_butler": "b", "message": "y"},
        ]
        long_sentence = "a" * (_SYNTHESIS_MAX_SENTENCE_CHARS + 50)
        mock_adapter = AsyncMock()
        mock_adapter.invoke = AsyncMock(
            return_value=(f"{long_sentence}\nextra ignored line", [], None)
        )
        with (
            patch(
                "butlers.core.model_routing.resolve_model_with_effective_tier",
                new=AsyncMock(return_value=_catalog_result()),
            ),
            patch(
                "butlers.core.model_routing.check_token_quota",
                new=AsyncMock(return_value=_quota()),
            ),
            patch("butlers.core.runtimes.base.create_adapter", return_value=mock_adapter),
        ):
            result = await _synthesize_cluster_sentence(object(), cluster)
        assert result == long_sentence[:_SYNTHESIS_MAX_SENTENCE_CHARS]
        assert "extra ignored line" not in result


class TestFormatDigestSynthesisIntegration:
    """_format_digest's slice-3 wiring: pool=None (every pre-slice-3 caller)
    skips synthesis entirely; a supplied pool attempts it per multi-candidate
    cluster only."""

    async def test_pool_none_skips_synthesis_entirely(self):
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "travel", "message": "A", "metadata": {"entity_id": "x"}},
            {"origin_butler": "finance", "message": "B", "metadata": {"entity_id": "x"}},
        ]
        with patch(
            "butlers.tools.switchboard.insight.broker._synthesize_cluster_sentence",
            new=AsyncMock(),
        ) as mock_synth:
            msg = await _format_digest(candidates, pool=None)
        mock_synth.assert_not_awaited()
        line = next(line for line in msg.splitlines() if "Correlated" in line)
        assert line.strip() == "1. Correlated (2):"

    async def test_synthesis_success_renders_inline_with_label(self):
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "travel", "message": "A", "metadata": {"entity_id": "x"}},
            {"origin_butler": "finance", "message": "B", "metadata": {"entity_id": "x"}},
        ]
        with patch(
            "butlers.tools.switchboard.insight.broker._synthesize_cluster_sentence",
            new=AsyncMock(return_value="Both about the same trip."),
        ):
            msg = await _format_digest(candidates, pool=object())
        line = next(line for line in msg.splitlines() if "Correlated" in line)
        assert line.strip() == "1. Correlated (2): Both about the same trip."
        # Member bullets still render unchanged beneath the label.
        assert "- [Travel] A" in msg
        assert "- [Finance] B" in msg

    async def test_synthesis_none_falls_back_to_plain_label(self):
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [
            {"origin_butler": "travel", "message": "A", "metadata": {"entity_id": "x"}},
            {"origin_butler": "finance", "message": "B", "metadata": {"entity_id": "x"}},
        ]
        with patch(
            "butlers.tools.switchboard.insight.broker._synthesize_cluster_sentence",
            new=AsyncMock(return_value=None),
        ):
            msg = await _format_digest(candidates, pool=object())
        line = next(line for line in msg.splitlines() if "Correlated" in line)
        assert line.strip() == "1. Correlated (2):"

    async def test_singleton_candidates_never_call_synthesis(self):
        from butlers.tools.switchboard.insight.broker import _format_digest

        candidates = [{"origin_butler": "health", "message": "Log blood pressure"}]
        with patch(
            "butlers.tools.switchboard.insight.broker._synthesize_cluster_sentence",
            new=AsyncMock(),
        ) as mock_synth:
            await _format_digest(candidates, pool=object())
        mock_synth.assert_not_awaited()


class TestDailyHoldFallbackDeadline:
    """Unit coverage for the slice-5 hard fallback deadline helper — the
    delivery_cycle()-level suppression-bypass/travel-defer behavior itself
    is covered against a real pool in
    ``tests/modules/test_insight_attention_ledger.py::TestDailyHoldMode``."""

    async def test_before_fallback_hour_not_reached(self):
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            _daily_hold_fallback_reached,
        )

        now = datetime(2026, 7, 26, _DAILY_HOLD_FALLBACK_UTC_HOUR - 1, 59, tzinfo=UTC)
        assert _daily_hold_fallback_reached(now) is False

    async def test_at_fallback_hour_reached(self):
        from butlers.tools.switchboard.insight.broker import (
            _DAILY_HOLD_FALLBACK_UTC_HOUR,
            _daily_hold_fallback_reached,
        )

        now = datetime(2026, 7, 26, _DAILY_HOLD_FALLBACK_UTC_HOUR, 0, tzinfo=UTC)
        assert _daily_hold_fallback_reached(now) is True

    async def test_well_after_fallback_hour_reached(self):
        from butlers.tools.switchboard.insight.broker import _daily_hold_fallback_reached

        now = datetime(2026, 7, 26, 23, 0, tzinfo=UTC)
        assert _daily_hold_fallback_reached(now) is True
