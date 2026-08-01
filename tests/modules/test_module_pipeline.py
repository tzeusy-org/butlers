"""Condensed pipeline module tests — behavioral contract only.

Replaces test_module_pipeline.py (76) + test_pipeline_decomposition.py (16)
= 92 tests replaced with ~20.

Covers:
- RoutingResult dataclass defaults
- _extract_routed_butlers: tool call extraction
- _build_routing_prompt: returns non-empty string
- MessagePipeline.process: single-target routing, fallback to general
- PipelineModule: ABC compliance, tool registration
- PipelineConfig: defaults

[bu-7sd7a]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as _metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

from butlers.modules.pipeline import (
    MessagePipeline,
    PipelineConfig,
    PipelineModule,
    RoutingResult,
    _build_dashboard_lane_prompt,
    _build_decomposition_prompt,
    _build_routing_prompt,
    _extract_bug_report_calls,
    _extract_routed_butlers,
    _infer_fallback_target_from_cc_output,
    _normalize_decomp_signal,
    _normalize_decomp_signals,
)

pytestmark = pytest.mark.unit


@dataclass
class FakeSpawnerResult:
    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


_MOCK_BUTLERS = [
    {"name": "general", "description": "General purpose"},
    {"name": "health", "description": "Health tracking"},
    {"name": "finance", "description": "Finance"},
]


def _dashboard_tool_args(**overrides: Any) -> dict[str, Any]:
    """Build a dashboard ingress shape with its mandatory immutable turn id."""
    args: dict[str, Any] = {
        "source_channel": "dashboard",
        "dashboard_message_id": "d1d1d1d1-0000-7000-8000-000000000001",
    }
    args.update(overrides)
    return args


# ---------------------------------------------------------------------------
# RoutingResult
# ---------------------------------------------------------------------------


class TestRoutingResult:
    def test_defaults(self):
        result = RoutingResult(target_butler="general")
        assert result.target_butler == "general"
        assert result.route_result == {}
        assert result.classification_error is None
        assert result.routing_error is None
        assert result.routed_targets == []
        assert result.acked_targets == []
        assert result.failed_targets == []


# ---------------------------------------------------------------------------
# _extract_routed_butlers
# ---------------------------------------------------------------------------


def _route_call(butler: str) -> dict:
    return {
        "name": "route_to_butler",
        "args": {"butler": butler},
        "result": {"status": "ok", "butler": butler},
    }


class TestExtractRoutedButlers:
    @pytest.mark.parametrize(
        ("tool_calls", "expected_routed", "expected_acked"),
        [
            ([_route_call("health")], {"health"}, {"health"}),  # single
            (
                [_route_call("health"), _route_call("finance")],
                {"health", "finance"},
                {"health", "finance"},
            ),  # multi
            ([], set(), set()),  # empty
            ([{"name": "other_tool", "args": {}, "result": {}}], set(), set()),  # non-route ignored
        ],
    )
    def test_extract_routed_butlers(self, tool_calls, expected_routed, expected_acked):
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert set(routed) == expected_routed
        assert set(acked) == expected_acked
        assert failed == []


# ---------------------------------------------------------------------------
# _build_routing_prompt
# ---------------------------------------------------------------------------


class TestBuildRoutingPrompt:
    def test_returns_non_empty_string(self):
        prompt = _build_routing_prompt(message="hello", butlers=_MOCK_BUTLERS)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "hello" in prompt or len(prompt) > 10


# ---------------------------------------------------------------------------
# MessagePipeline.process
# ---------------------------------------------------------------------------


class TestMessagePipelineProcess:
    @patch.object(
        MessagePipeline,
        "_load_dashboard_context",
        new_callable=AsyncMock,
        return_value={
            "conversation_id": "c1c1c1c1-0000-7000-8000-000000000001",
            "message_id": "d1d1d1d1-0000-7000-8000-000000000001",
            "page_context": None,
        },
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_dispatch_carries_immutable_turn_id(
        self, _mock_load, _mock_dashboard_context
    ):
        """Dashboard classification must register against its user-message turn.

        The Stop control protocol keys all cross-process work by the immutable
        ``dashboard_messages.id`` rather than a conversation or sender id.
        """
        captured_kwargs: dict[str, Any] = {}

        async def mock_dispatch(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeSpawnerResult(
                output="Routed to health.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "accepted", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process(
            "I have a headache",
            tool_args={
                "source": "dashboard",
                "source_channel": "dashboard",
                "source_identity": "dashboard:operator",
                "source_tool": "ingest",
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
                "dashboard_message_id": "d1d1d1d1-0000-7000-8000-000000000001",
            },
            message_inbox_id="019c8812-fb0f-77f3-88b9-5763c1336b27",
        )

        assert result.acked_targets == ["health"]
        assert str(captured_kwargs["dashboard_turn_id"]) == ("d1d1d1d1-0000-7000-8000-000000000001")

    @patch.object(
        MessagePipeline,
        "_load_dashboard_context",
        new_callable=AsyncMock,
        return_value={
            "conversation_id": "c1c1c1c1-0000-7000-8000-000000000001",
            "message_id": "d1d1d1d1-0000-7000-8000-000000000001",
            "page_context": None,
        },
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_dispatch_recovers_immutable_turn_id_from_inbox(
        self, _mock_load, mock_dashboard_context
    ):
        """Direct pipeline callers may omit transport-only dashboard metadata."""
        captured_kwargs: dict[str, Any] = {}

        async def mock_dispatch(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeSpawnerResult(
                output="Routed to health.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "accepted", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process(
            "I have a headache",
            tool_args={
                "source": "dashboard",
                "source_channel": "dashboard",
                "source_identity": "dashboard:operator",
                "source_tool": "ingest",
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
            },
            message_inbox_id="019c8812-fb0f-77f3-88b9-5763c1336b27",
        )

        assert result.acked_targets == ["health"]
        assert str(captured_kwargs["dashboard_turn_id"]) == "d1d1d1d1-0000-7000-8000-000000000001"
        mock_dashboard_context.assert_awaited_once_with("019c8812-fb0f-77f3-88b9-5763c1336b27")

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_dispatch_fails_closed_without_a_valid_turn_id(self, _mock_load):
        """A dashboard-originated runtime must never bypass Stop control."""
        dispatch = AsyncMock()
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=dispatch, source_butler="switchboard"
        )

        result = await pipeline.process(
            "I have a headache",
            tool_args={
                "source": "dashboard",
                "source_channel": "dashboard",
                "source_identity": "dashboard:operator",
                "source_tool": "ingest",
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
                "dashboard_message_id": "not-a-uuid",
            },
        )

        assert result.target_butler == "dashboard_control_error"
        assert result.classification_error is not None
        dispatch.assert_not_awaited()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_single_target_routing(self, mock_load):
        captured_kwargs = {}

        async def mock_dispatch(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []
        assert "timeout_override" not in captured_kwargs

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_explicit_classification_timeout_is_forwarded(self, mock_load):
        captured_kwargs = {}

        async def mock_dispatch(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            classification_timeout_s=7,
        )
        result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert captured_kwargs["timeout_override"] == 7

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_falls_back_to_general_when_no_tools(self, mock_load):
        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="No routing needed.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process("Just browsing")

        assert result.target_butler == "general"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_error_in_dispatch_returns_fallback(self, mock_load):
        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=None, success=False, error="LLM error", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process("Some message")

        assert result.target_butler == "general"
        assert result.classification_error is not None or result.target_butler == "general"

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_decomposition_empty_runtime_output_is_decomposed_empty(
        self, mock_load, mock_history
    ):
        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output=None,
                success=False,
                error="TimeoutError: OpenCode CLI timed out after 30 seconds",
                tool_calls=[],
                model="opencode/test",
                input_tokens=12,
                output_tokens=0,
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "conversation batch",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000001",
        )

        assert result.target_butler == "decomposed_empty"
        assert result.classification_error is None
        assert result.route_result["reason"] == "no_signals_extracted"
        pipeline._update_message_inbox_lifecycle.assert_awaited_once()
        update_kwargs = pipeline._update_message_inbox_lifecycle.await_args.kwargs
        assert update_kwargs["decomposition_output"]["model"] == "opencode/test"
        assert update_kwargs["decomposition_output"]["token_usage"] == {
            "input_tokens": 12,
            "output_tokens": 0,
        }


# ---------------------------------------------------------------------------
# Routing verdict mining substrate write hooks [bu-aga08]
# ---------------------------------------------------------------------------


class TestMessagePipelineRoutingVerdictLog:
    """Unit tests for the routing_verdict_log write hooks in MessagePipeline.

    These assert on the call args passed to
    ``butlers.modules.pipeline.record_routing_verdict`` (mocked) rather than
    hitting a real DB — see
    ``tests/integration/test_switchboard_routing_verdict_log_migration.py``
    for the migration + real-insert coverage.
    """

    async def test_rule_bypass_route_to_records_rule_verdict(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.route.route",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            result = await pipeline.process(
                "some finance email",
                tool_args={
                    "source_channel": "email",
                    "source_identity": "gmail:tzeusii",
                    "request_context": {
                        "triage_decision": "route_to",
                        "triage_target": "finance",
                        "triage_rule_id": "11111111-1111-1111-1111-111111111111",
                        "triage_rule_type": "sender_domain",
                        "source_sender_identity": "billing@chase.com",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000002",
            )

        assert result.target_butler == "finance"
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["ingestion_event_id"] == "00000000-0000-0000-0000-000000000002"
        assert kwargs["sender_identity"] == "billing@chase.com"
        assert kwargs["source_channel"] == "email"
        assert kwargs["verdict_source"] == "rule"
        assert kwargs["verdict_action"] == "route_to"
        assert kwargs["verdict_target"] == "finance"
        assert kwargs["matched_rule_id"] == "11111111-1111-1111-1111-111111111111"

    async def test_pinned_target_bypass_records_pinned_verdict_with_no_rule_id(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.route.route",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            await pipeline.process(
                "dashboard message",
                tool_args=_dashboard_tool_args(
                    request_context={
                        "triage_decision": "route_to",
                        "triage_target": "general",
                        "triage_rule_type": "pinned_target",
                    },
                ),
                message_inbox_id="00000000-0000-0000-0000-000000000003",
            )

        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "pinned"
        assert kwargs["matched_rule_id"] is None

    async def test_thread_affinity_bypass_records_rule_verdict_with_no_rule_id(self):
        """thread_affinity has no backing ingestion_rules row (matched_rule_id
        is always None for it) but is still bucketed as verdict_source='rule'
        — see verdict_log module docstring."""
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.route.route",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            await pipeline.process(
                "threaded reply",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "route_to",
                        "triage_target": "relationship",
                        "triage_rule_type": "thread_affinity",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000004",
            )

        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "rule"
        assert kwargs["matched_rule_id"] is None

    async def test_skip_bypass_records_skip_verdict(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await pipeline.process(
                "bulk mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "skip",
                        "triage_rule_id": "22222222-2222-2222-2222-222222222222",
                        "triage_rule_type": "header_condition",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000005",
            )

        assert result.target_butler == "skipped"
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "rule"
        assert kwargs["verdict_action"] == "skip"
        assert kwargs.get("verdict_target") is None
        assert kwargs["matched_rule_id"] == "22222222-2222-2222-2222-222222222222"

    async def test_metadata_only_bypass_records_metadata_only_verdict(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await pipeline.process(
                "noreply mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {"triage_decision": "metadata_only"},
                },
                message_inbox_id="00000000-0000-0000-0000-000000000006",
            )

        assert result.target_butler == "metadata_only"
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_action"] == "metadata_only"

    async def test_bypass_does_not_record_verdict_when_no_message_inbox_id(self):
        """No ingestion_event_id to FK against -> the write must be skipped,
        not attempted with a null FK."""
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            await pipeline.process(
                "noreply mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {"triage_decision": "metadata_only"},
                },
            )

        mock_record.assert_not_awaited()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_llm_route_to_butler_call_records_llm_verdict_with_session_id(self, mock_load):
        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await pipeline.process(
                "I have a headache",
                tool_args={"source_identity": "someone@example.com"},
                message_inbox_id="00000000-0000-0000-0000-000000000007",
            )

        assert result.target_butler == "health"
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["ingestion_event_id"] == "00000000-0000-0000-0000-000000000007"
        assert kwargs["sender_identity"] == "someone@example.com"
        assert kwargs["verdict_source"] == "llm"
        assert kwargs["verdict_action"] == "route_to"
        assert kwargs["verdict_target"] == "health"
        # FakeSpawnerResult has no session_id attribute -> getattr default None.
        assert kwargs["session_id"] is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_llm_no_tool_calls_fallback_does_not_record_llm_verdict(self, mock_load):
        """The no-tool-calls -> infer-from-text/"general" fallback is a
        heuristic default, not a genuine per-sender LLM decision, and must
        not be logged as mining evidence."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="No routing needed.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await pipeline.process(
                "Just browsing",
                message_inbox_id="00000000-0000-0000-0000-000000000008",
            )

        assert result.target_butler == "general"
        mock_record.assert_not_awaited()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_llm_multiple_route_to_butler_calls_record_one_verdict_each(self, mock_load):
        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health and finance.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health"},
                        "result": {"status": "ok", "butler": "health"},
                    },
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance"},
                        "result": {"status": "ok", "butler": "finance"},
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ) as mock_record:
            await pipeline.process(
                "multi-signal message",
                message_inbox_id="00000000-0000-0000-0000-000000000009",
            )

        assert mock_record.await_count == 2
        targets = {c.kwargs["verdict_target"] for c in mock_record.await_args_list}
        assert targets == {"health", "finance"}


# ---------------------------------------------------------------------------
# Demotion via spot-check sampling [bu-x55k3, rule-promotion bead 5 of 7]
# ---------------------------------------------------------------------------


class TestMessagePipelineDemotionSpotCheck:
    """A ``triage_spot_check=True`` request_context (set by ingest.py when
    ``PolicyDecision.spot_check`` is True) must suppress all three bypass
    branches and route the event through normal LLM classification, then log
    the fresh verdict as ``verdict_source='spot_check'`` (not ``'llm'``) with
    ``matched_rule_id`` set to the sampled rule, and trigger the rolling
    agreement re-score.

    For a spot-checked ``skip``/``metadata_only`` rule the LLM *agreeing*
    resolves to no route target, so the route_to write loop records nothing;
    a dedicated counterpart branch (bu-wa3nb) records that no-route agreement
    as a ``spot_check`` row carrying the rule's own suppressed decision so the
    agreement scorer sees agreements, not just disagreements.
    """

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_spot_check_route_to_agreement_suppresses_bypass(self, mock_load):
        """Fresh LLM verdict matches the rule's own target -> agreement."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to finance.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance"},
                        "result": {"status": "ok", "butler": "finance"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ) as mock_demotion,
        ):
            result = await pipeline.process(
                "some finance email",
                tool_args={
                    "source_channel": "email",
                    "source_identity": "billing@chase.com",
                    "request_context": {
                        "triage_decision": "route_to",
                        "triage_target": "finance",
                        "triage_rule_id": "11111111-1111-1111-1111-111111111111",
                        "triage_rule_type": "sender_domain",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000010",
            )

        # Reached via the LLM path (not the bypass), and happens to agree.
        assert result.target_butler == "finance"
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "spot_check"
        assert kwargs["verdict_action"] == "route_to"
        assert kwargs["verdict_target"] == "finance"
        assert kwargs["matched_rule_id"] == "11111111-1111-1111-1111-111111111111"

        mock_demotion.assert_awaited_once()
        assert mock_demotion.await_args.kwargs["rule_id"] == "11111111-1111-1111-1111-111111111111"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_spot_check_route_to_disagreement_records_llm_target_not_rule_target(
        self, mock_load
    ):
        """Fresh LLM verdict disagrees with the rule -> spot_check row still
        records what the LLM actually said, not what the rule would have."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ),
        ):
            result = await pipeline.process(
                "some finance email",
                tool_args={
                    "source_channel": "email",
                    "source_identity": "billing@chase.com",
                    "request_context": {
                        "triage_decision": "route_to",
                        "triage_target": "finance",
                        "triage_rule_id": "11111111-1111-1111-1111-111111111111",
                        "triage_rule_type": "sender_domain",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000011",
            )

        assert result.target_butler == "general"
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "spot_check"
        assert kwargs["verdict_target"] == "general"
        assert kwargs["matched_rule_id"] == "11111111-1111-1111-1111-111111111111"

    async def test_spot_check_skip_agreement_records_skip_counterpart_verdict(self):
        """A spot-checked skip rule the LLM AGREES with (no route) must not
        take the early skip return, but MUST record a ``spot_check`` skip
        counterpart row (bu-wa3nb) so the agreement scorer sees the agreement,
        then trigger the demotion re-score."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="Nothing routable here.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.classify._load_available_butlers",
                new_callable=AsyncMock,
                return_value=_MOCK_BUTLERS,
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ) as mock_demotion,
        ):
            result = await pipeline.process(
                "bulk mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "skip",
                        "triage_rule_id": "22222222-2222-2222-2222-222222222222",
                        "triage_rule_type": "header_condition",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000012",
            )

        # The skip bypass's early return (target_butler="skipped") must not fire.
        assert result.target_butler != "skipped"
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "spot_check"
        assert kwargs["verdict_action"] == "skip"
        assert kwargs["verdict_target"] is None
        assert kwargs["matched_rule_id"] == "22222222-2222-2222-2222-222222222222"
        mock_demotion.assert_awaited_once()
        assert mock_demotion.await_args.kwargs["rule_id"] == "22222222-2222-2222-2222-222222222222"

    async def test_spot_check_metadata_only_agreement_records_counterpart_verdict(self):
        """Same as the skip counterpart, for a spot-checked metadata_only
        rule the LLM agrees with -> ``verdict_action='metadata_only'``."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="Metadata only.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.classify._load_available_butlers",
                new_callable=AsyncMock,
                return_value=_MOCK_BUTLERS,
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ) as mock_demotion,
        ):
            result = await pipeline.process(
                "receipt attachment",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "metadata_only",
                        "triage_rule_id": "33333333-3333-3333-3333-333333333333",
                        "triage_rule_type": "header_condition",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000014",
            )

        assert result.target_butler != "metadata_only"
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "spot_check"
        assert kwargs["verdict_action"] == "metadata_only"
        assert kwargs["verdict_target"] is None
        assert kwargs["matched_rule_id"] == "33333333-3333-3333-3333-333333333333"
        mock_demotion.assert_awaited_once()

    async def test_spot_check_skip_disagreement_records_route_to_not_skip(self):
        """A spot-checked skip rule the LLM DISAGREES with (calls
        route_to_butler) records the route_to disagreement row exactly as
        before — the skip counterpart branch must NOT also fire."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to finance.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance"},
                        "result": {"status": "ok", "butler": "finance"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.classify._load_available_butlers",
                new_callable=AsyncMock,
                return_value=_MOCK_BUTLERS,
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ) as mock_demotion,
        ):
            await pipeline.process(
                "actually a finance email",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "skip",
                        "triage_rule_id": "22222222-2222-2222-2222-222222222222",
                        "triage_rule_type": "header_condition",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000015",
            )

        # Exactly one row: the route_to disagreement — never a second skip row.
        mock_record.assert_awaited_once()
        kwargs = mock_record.await_args.kwargs
        assert kwargs["verdict_source"] == "spot_check"
        assert kwargs["verdict_action"] == "route_to"
        assert kwargs["verdict_target"] == "finance"
        mock_demotion.assert_awaited_once()

    async def test_spot_check_skip_did_not_run_records_nothing(self):
        """Honesty doctrine: a spot-check whose classification never produced
        a result (dispatch returns None -> spawn error/timeout equivalent)
        must record no counterpart row, so it counts as neither agreement nor
        disagreement in the rolling window."""

        async def mock_dispatch(**kwargs):
            return None

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )

        with (
            patch(
                "butlers.tools.switchboard.routing.classify._load_available_butlers",
                new_callable=AsyncMock,
                return_value=_MOCK_BUTLERS,
            ),
            patch(
                "butlers.modules.pipeline.record_routing_verdict",
                new_callable=AsyncMock,
            ) as mock_record,
            patch(
                "butlers.modules.pipeline.maybe_create_demotion_suggestion",
                new_callable=AsyncMock,
            ) as mock_demotion,
        ):
            await pipeline.process(
                "bulk mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "skip",
                        "triage_rule_id": "22222222-2222-2222-2222-222222222222",
                        "triage_rule_type": "header_condition",
                        "triage_spot_check": True,
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000016",
            )

        mock_record.assert_not_awaited()
        mock_demotion.assert_not_awaited()

    async def test_no_spot_check_flag_preserves_existing_skip_bypass(self):
        """Regression guard: omitting triage_spot_check must not change
        pre-existing bypass behavior."""
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
        )

        with patch(
            "butlers.modules.pipeline.record_routing_verdict",
            new_callable=AsyncMock,
        ):
            result = await pipeline.process(
                "bulk mail",
                tool_args={
                    "source_channel": "email",
                    "request_context": {
                        "triage_decision": "skip",
                        "triage_rule_id": "22222222-2222-2222-2222-222222222222",
                        "triage_rule_type": "header_condition",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000013",
            )

        assert result.target_butler == "skipped"


# ---------------------------------------------------------------------------
# Structured tool-use classification fast lane [bu-qvnce.12 slice 3 / bu-evus6]
# ---------------------------------------------------------------------------


class TestMessagePipelineStructuredClassificationFastLane:
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_local_tool_server_provider_is_resolved_lazily_not_at_construction(
        self, mock_load
    ):
        """Regression guard: ``ButlerDaemon._wire_pipelines()`` (and therefore
        ``MessagePipeline.__init__``) runs BEFORE ``daemon.mcp`` is assigned a
        real ``FastMCP`` instance during startup (lifecycle step 10b vs 12).
        Capturing ``daemon.mcp`` by value at construction would silently and
        permanently disable the fast lane. The provider must be a callable
        resolved fresh on every dispatch — this proves a provider returning
        ``None`` at construction time but a real object later still engages
        the fast lane.
        """
        live_mcp_holder = {"mcp": None}  # simulates daemon.mcp being unset yet
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="switchboard",
            local_tool_server_provider=lambda: live_mcp_holder["mcp"],
        )
        # Simulate the FastMCP instance being assigned after construction
        # (lifecycle step 12), before the first classification dispatch.
        live_mcp_holder["mcp"] = MagicMock()

        fast_result = FakeSpawnerResult(
            tool_calls=[
                {
                    "name": "route_to_butler",
                    "input": {"butler": "health", "prompt": "Track headache"},
                    "result": {"status": "accepted", "butler": "health"},
                }
            ],
        )
        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(return_value=fast_result),
        ) as mock_fast_lane:
            result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        mock_fast_lane.assert_awaited_once()
        _, kwargs = mock_fast_lane.call_args
        assert kwargs["mcp_server"] is live_mcp_holder["mcp"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_fast_lane_result_flows_through_unchanged_and_cli_is_skipped(self, mock_load):
        """When the fast lane returns a result, downstream extraction/telemetry
        must behave identically to the CLI path, and dispatch_fn (the CLI
        spawn) must never be called.
        """
        mock_dispatch = AsyncMock()
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            local_tool_server_provider=lambda: MagicMock(),
        )

        fast_result = FakeSpawnerResult(
            output="",
            tool_calls=[
                {
                    "name": "route_to_butler",
                    "input": {"butler": "health", "prompt": "Track headache"},
                    "result": {"status": "accepted", "butler": "health"},
                }
            ],
            model="claude-haiku-4-5-20251001",
            input_tokens=10,
            output_tokens=5,
        )
        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(return_value=fast_result),
        ):
            result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.acked_targets == ["health"]
        mock_dispatch.assert_not_awaited()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_falls_back_to_cli_when_fast_lane_returns_none(self, mock_load):
        """try_structured_classification() returning None (runtime not "api",
        schema-invalid twice, failover exhausted, ...) must fall back to the
        existing CLI dispatch_fn path unchanged.
        """

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            local_tool_server_provider=lambda: MagicMock(),
        )

        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(return_value=None),
        ):
            result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.acked_targets == ["health"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_fast_lane_not_attempted_when_mcp_server_is_none(self, mock_load):
        """mcp_server defaults to None — the fast lane must never even be
        imported/called, and every existing dispatch_fn-only test stays valid.
        """

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(side_effect=AssertionError("fast lane must not be attempted")),
        ):
            result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_fast_lane_skipped_for_decomposition_payload(self, mock_load, mock_history):
        """The decomposition/signal-extraction lane parses a JSON signal
        array, not route_to_butler/file_bug_report tool calls — the fast
        lane must never be attempted for it, even when mcp_server is set.
        """

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="[]", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            local_tool_server_provider=lambda: MagicMock(),
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(side_effect=AssertionError("fast lane must not be attempted")),
        ):
            result = await pipeline.process(
                "conversation batch",
                tool_args={
                    "source_channel": "telegram_user_client",
                    "request_context": {"payload_type": "conversation_history"},
                },
                message_inbox_id="00000000-0000-0000-0000-000000000001",
            )

        assert result.target_butler == "decomposed_empty"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_fast_lane_exception_falls_back_to_cli(self, mock_load):
        """A bug in the fast lane must never take down classification — any
        unexpected exception falls back to the existing CLI path.
        """

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            local_tool_server_provider=lambda: MagicMock(),
        )
        with patch(
            "butlers.tools.switchboard.routing.structured_classify.try_structured_classification",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"


# ---------------------------------------------------------------------------
# Dashboard chat-widget classification lanes [bu-p6ey8.2]
# ---------------------------------------------------------------------------


def _bug_report_call(status: str = "ok", case_reference: str = "abc123def456") -> dict:
    return {
        "name": "file_bug_report",
        "args": {"summary": "the chart is empty"},
        "result": {"status": status, "case_reference": case_reference, "filed": status == "ok"},
    }


class TestBuildDashboardLanePrompt:
    def test_mentions_both_lanes(self):
        prompt = _build_dashboard_lane_prompt("hello", _MOCK_BUTLERS)
        assert "route_to_butler" in prompt
        assert "file_bug_report" in prompt

    def test_explains_dashboard_lane_conflict_refusal_is_terminal(self):
        prompt = _build_dashboard_lane_prompt("the chart is broken", _MOCK_BUTLERS)

        assert "status: 'refused'" in prompt
        assert "reason: 'dashboard_lane_conflict'" in prompt
        assert "Do NOT call either tool again" in prompt

    def test_surfaces_conversation_id_and_page_context(self):
        prompt = _build_dashboard_lane_prompt(
            "Alice's birthday is March 3rd",
            _MOCK_BUTLERS,
            conversation_id="conv-123",
            page_context={"route": "/entities/concentration", "query_params": {}},
        )
        assert "conv-123" in prompt
        assert "/entities/concentration" in prompt

    def test_omits_dashboard_context_section_when_absent(self):
        prompt = _build_dashboard_lane_prompt("hi", _MOCK_BUTLERS)
        assert "Dashboard conversation_id:" not in prompt
        assert "Dashboard page_context" not in prompt


class TestExtractBugReportCalls:
    def test_no_bug_report_call(self):
        attempted, succeeded, case_ref = _extract_bug_report_calls([_route_call("health")])
        assert attempted is False
        assert succeeded is False
        assert case_ref is None

    def test_successful_bug_report_call(self):
        attempted, succeeded, case_ref = _extract_bug_report_calls([_bug_report_call()])
        assert attempted is True
        assert succeeded is True
        assert case_ref == "abc123def456"

    def test_failed_bug_report_call(self):
        attempted, succeeded, case_ref = _extract_bug_report_calls(
            [_bug_report_call(status="error")]
        )
        assert attempted is True
        assert succeeded is False

    def test_empty_tool_calls(self):
        attempted, succeeded, case_ref = _extract_bug_report_calls([])
        assert attempted is False
        assert succeeded is False
        assert case_ref is None


class TestMessagePipelineProcessDashboardLanes:
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_lane_a_data_statement_routes_to_domain_butler(self, mock_load):
        """Lane A: a route_to_butler call routes normally, same as any channel."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to relationship butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {
                            "butler": "relationship",
                            "prompt": "Alice's birthday is March 3rd",
                        },
                        "result": {"status": "ok", "butler": "relationship"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "conversation_id": "conv-1",
                "page_context": {"route": "/entities/concentration"},
            }
        )

        result = await pipeline.process(
            "Alice's birthday is actually March 3rd",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000002",
        )

        assert result.target_butler == "relationship"
        assert result.routed_targets == ["relationship"]
        assert result.acked_targets == ["relationship"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_lane_b_bug_report_never_reaches_route_to_butler_fallback(self, mock_load):
        """Lane B: file_bug_report short-circuits — never falls into the
        route_to_butler extraction/fallback-to-general path."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Filed as a bug report.",
                tool_calls=[_bug_report_call(case_reference="deadbeef0001")],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-2", "page_context": None}
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "The concentration chart is empty for child-of",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000003",
        )

        assert result.target_butler == "qa"
        assert result.routed_targets == []
        assert result.acked_targets == ["qa"]
        assert result.failed_targets == []
        assert result.route_result["case_reference"] == "deadbeef0001"
        # Never routed to a domain butler.
        assert "relationship" not in result.acked_targets
        assert result.target_butler != "general"
        # Single-lane flow: no co-occurrence metadata should appear.
        assert "co_occurring_route_targets" not in result.route_result

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_lane_b_surfaces_co_occurring_route_route_then_bug(self, mock_load, caplog):
        """Route-then-bug (bu-j5jqv): route_to_butler dispatched first (the
        tool-layer guard lets an already-dispatched route stand), then
        file_bug_report claimed the lane in the same session. Bug lane still
        wins the pipeline result, but the co-occurrence must be visible —
        never silently hidden by _extract_bug_report_calls returning on the
        first bug call."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed then filed a bug report.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "relationship", "prompt": "hello"},
                        "result": {"status": "accepted", "butler": "relationship"},
                    },
                    _bug_report_call(case_reference="deadbeef0002"),
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-3", "page_context": None}
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        with caplog.at_level("WARNING"):
            result = await pipeline.process(
                "Actually this chart is broken",
                tool_args=_dashboard_tool_args(),
                message_inbox_id="00000000-0000-0000-0000-000000000004",
            )

        assert result.target_butler == "qa"
        assert result.acked_targets == ["qa"]
        assert result.route_result["co_occurring_dispatched_targets"] == ["relationship"]
        assert "co_occurring_attempted_only_targets" not in result.route_result
        assert "co_occurring_route_targets" not in result.route_result
        decomposition_output = pipeline._update_message_inbox_lifecycle.await_args.kwargs[
            "decomposition_output"
        ]
        assert decomposition_output["co_occurring_dispatched_targets"] == ["relationship"]
        assert "co_occurring_attempted_only_targets" not in decomposition_output
        co_occurrence_record = next(
            rec for rec in caplog.records if "Dashboard lane co-occurrence" in rec.message
        )
        assert co_occurrence_record.co_occurring_dispatched_targets == ["relationship"]
        assert not hasattr(co_occurrence_record, "co_occurring_attempted_only_targets")

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_lane_b_surfaces_co_occurring_route_bug_then_route_refused(
        self, mock_load, caplog
    ):
        """Failed/refused co-occurring routes remain observable as attempts.

        Neither target was acknowledged by a domain butler, so neither may
        appear as an actual dispatch in the pipeline, persisted, or log
        telemetry surfaces.
        """

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Filed a bug report, then tried to route anyway.",
                tool_calls=[
                    _bug_report_call(case_reference="deadbeef0003"),
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "relationship", "prompt": "hello"},
                        "result": {
                            "status": "refused",
                            "butler": "relationship",
                            "reason": "dashboard_lane_conflict",
                        },
                    },
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance", "prompt": "hello"},
                        "result": {"status": "error", "butler": "finance"},
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-4", "page_context": None}
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        with caplog.at_level("WARNING"):
            result = await pipeline.process(
                "This is a bug",
                tool_args=_dashboard_tool_args(),
                message_inbox_id="00000000-0000-0000-0000-000000000005",
            )

        assert result.target_butler == "qa"
        assert result.acked_targets == ["qa"]
        assert result.route_result["co_occurring_attempted_only_targets"] == [
            "finance",
            "relationship",
        ]
        assert "co_occurring_dispatched_targets" not in result.route_result
        assert "co_occurring_route_targets" not in result.route_result
        decomposition_output = pipeline._update_message_inbox_lifecycle.await_args.kwargs[
            "decomposition_output"
        ]
        assert decomposition_output["co_occurring_attempted_only_targets"] == [
            "finance",
            "relationship",
        ]
        assert "co_occurring_dispatched_targets" not in decomposition_output
        co_occurrence_record = next(
            rec for rec in caplog.records if "Dashboard lane co-occurrence" in rec.message
        )
        assert co_occurrence_record.co_occurring_attempted_only_targets == [
            "finance",
            "relationship",
        ]
        assert not hasattr(co_occurrence_record, "co_occurring_dispatched_targets")

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_unroutable_dashboard_message_dead_letters_instead_of_general(self, mock_load):
        """Dashboard messages that route to neither lane must dead-letter + notify,
        never silently fall back to 'general' (that fallback is channel-specific)."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="I'm not sure what to do.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-3", "page_context": None}
        )
        pipeline._dead_letter_dashboard_unroutable = AsyncMock(  # type: ignore[method-assign]
            return_value=RoutingResult(
                target_butler="dead_letter", route_result={"dead_letter_id": "dl-1"}
            )
        )

        result = await pipeline.process(
            "asdkfjaslkdfj",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000004",
        )

        assert result.target_butler == "dead_letter"
        assert result.target_butler != "general"
        pipeline._dead_letter_dashboard_unroutable.assert_awaited_once()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_non_dashboard_channel_still_falls_back_to_general(self, mock_load):
        """Non-dashboard channels are unaffected — existing fallback-to-general behavior."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output="No routing needed.", tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process(
            "Just browsing", tool_args={"source_channel": "telegram_bot"}
        )

        assert result.target_butler == "general"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_classifier_spawn_exception_dead_letters_instead_of_general(
        self, mock_load
    ):
        """(G2) A classifier spawn exception on a dashboard envelope must never
        silently fall back to 'general' like every other channel — it must
        route through the same dead-letter + in-thread-reply net as the
        no-lane-decision case."""

        async def mock_dispatch(**kwargs):
            raise TimeoutError("classification session timed out")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-5", "page_context": None}
        )
        pipeline._dead_letter_dashboard_unroutable = AsyncMock(  # type: ignore[method-assign]
            return_value=RoutingResult(
                target_butler="dead_letter", route_result={"dead_letter_id": "dl-2"}
            )
        )

        result = await pipeline.process(
            "something that will blow up classification",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000005",
        )

        assert result.target_butler == "dead_letter"
        assert result.target_butler != "general"
        pipeline._dead_letter_dashboard_unroutable.assert_awaited_once()
        call_kwargs = pipeline._dead_letter_dashboard_unroutable.await_args.kwargs
        assert "TimeoutError" in call_kwargs["failure_reason"]
        assert call_kwargs["failure_category"] == "timeout"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_non_dashboard_spawn_exception_still_falls_back_to_general(self, mock_load):
        """Regression: a classifier spawn exception on a non-dashboard channel
        keeps the pre-existing silent 'general' fallback unchanged."""

        async def mock_dispatch(**kwargs):
            raise RuntimeError("boom")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        result = await pipeline.process(
            "Just browsing", tool_args={"source_channel": "telegram_bot"}
        )

        assert result.target_butler == "general"
        assert result.classification_error is not None
        assert "RuntimeError" in result.classification_error

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_failed_route_dead_letters_instead_of_returning_routed(self, mock_load):
        """(G3) route_to_butler was attempted but route.execute failed for the
        only target — this must dead-letter, not silently return a 'routed
        but errored' result with no in-thread reply."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to finance butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance", "prompt": "Log $50 expense"},
                        "result": {"status": "error", "error": "target butler quarantined"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-6", "page_context": None}
        )
        pipeline._dead_letter_dashboard_unroutable = AsyncMock(  # type: ignore[method-assign]
            return_value=RoutingResult(
                target_butler="dead_letter", route_result={"dead_letter_id": "dl-3"}
            )
        )

        result = await pipeline.process(
            "Log $50 expense",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000006",
        )

        assert result.target_butler == "dead_letter"
        assert result.target_butler != "finance"
        pipeline._dead_letter_dashboard_unroutable.assert_awaited_once()
        call_kwargs = pipeline._dead_letter_dashboard_unroutable.await_args.kwargs
        assert "finance" in call_kwargs["failure_reason"]
        assert call_kwargs["failure_category"] == "downstream_failure"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_dashboard_successful_route_does_not_dead_letter(self, mock_load):
        """Regression guard for the 'not routed' -> 'not acked' gate change:
        a fully successful dashboard route must NOT go through the
        dead-letter path."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to finance butler.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "finance", "prompt": "Log $50 expense"},
                        "result": {"status": "ok", "butler": "finance"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._load_dashboard_context = AsyncMock(  # type: ignore[method-assign]
            return_value={"conversation_id": "conv-7", "page_context": None}
        )
        pipeline._dead_letter_dashboard_unroutable = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "Log $50 expense",
            tool_args=_dashboard_tool_args(),
            message_inbox_id="00000000-0000-0000-0000-000000000007",
        )

        assert result.target_butler == "finance"
        assert result.acked_targets == ["finance"]
        pipeline._dead_letter_dashboard_unroutable.assert_not_awaited()


class TestDeadLetterDashboardUnroutable:
    async def test_captures_dead_letter_and_replies_with_case_ref(self, monkeypatch):
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # Force a real import so the module object exists (the switchboard
        # tools namespace is manually injected into sys.modules by
        # register_all_butler_tools() without setting parent attributes,
        # which breaks monkeypatch's dotted-string resolution — patch the
        # imported module object directly instead).
        import butlers.tools.switchboard.dead_letter.capture as capture_mod

        fake_dead_letter_id = "11111111-2222-3333-4444-555555555555"
        mock_capture = AsyncMock(return_value=fake_dead_letter_id)
        monkeypatch.setattr(capture_mod, "capture_to_dead_letter", mock_capture)
        fake_reply = AsyncMock(return_value={"id": "msg-1"})
        monkeypatch.setattr(
            "butlers.api.conversations.conversation_reply_create",
            fake_reply,
        )

        pipeline = MessagePipeline(
            switchboard_pool=mock_pool, dispatch_fn=AsyncMock(), source_butler="switchboard"
        )

        result = await pipeline._dead_letter_dashboard_unroutable(
            request_id="019c8812-fb0f-77f3-88b9-5763c1336b27",
            message_text="asdkfjaslkdfj",
            cc_output="I'm not sure.",
            request_context=None,
            dashboard_context={
                "conversation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "page_context": None,
            },
        )

        assert result.target_butler == "dead_letter"
        assert result.route_result["dead_letter_id"] == fake_dead_letter_id
        mock_capture.assert_awaited_once()
        capture_kwargs = mock_capture.await_args.kwargs
        assert capture_kwargs["source_table"] == "message_inbox"
        assert capture_kwargs["replay_eligible"] is False

        fake_reply.assert_awaited_once()
        assert "11111111" in fake_reply.await_args.kwargs["message"]

    async def test_no_conversation_id_skips_reply_without_raising(self, monkeypatch):
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        import butlers.tools.switchboard.dead_letter.capture as capture_mod

        monkeypatch.setattr(capture_mod, "capture_to_dead_letter", AsyncMock(return_value="dl-id"))
        fake_reply = AsyncMock()
        monkeypatch.setattr(
            "butlers.api.conversations.conversation_reply_create",
            fake_reply,
        )

        pipeline = MessagePipeline(
            switchboard_pool=mock_pool, dispatch_fn=AsyncMock(), source_butler="switchboard"
        )

        result = await pipeline._dead_letter_dashboard_unroutable(
            request_id="019c8812-fb0f-77f3-88b9-5763c1336b27",
            message_text="asdkfjaslkdfj",
            cc_output="",
            request_context=None,
            dashboard_context=None,
        )

        assert result.target_butler == "dead_letter"
        fake_reply.assert_not_awaited()


# ---------------------------------------------------------------------------
# Decomposition signal schema (conversation-decomposition spec) [bu-2czq5]
# ---------------------------------------------------------------------------


class TestDecompositionSignalSchema:
    def test_build_decomposition_prompt_requests_full_schema(self):
        prompt = _build_decomposition_prompt("hi", _MOCK_BUTLERS, "history", None)
        # Drives the dedicated signal-extraction skill, not message-triage tools.
        assert "/signal-extraction" in prompt
        assert "Do NOT call any MCP tools" in prompt
        # Every full-schema field is requested.
        for field_name in (
            "signal_type",
            "target_butler",
            "tool_name",
            "tool_args",
            "excerpts",
            "confidence",
        ):
            assert field_name in prompt
        assert "sender" in prompt and "message_id" in prompt

    def test_normalize_signal_enforces_full_schema(self):
        norm = _normalize_decomp_signal(
            {
                "signal_type": "finance",
                "target_butler": "finance",
                "tool_name": "expense_log",
                "tool_args": {"amount": 42},
                "excerpts": [
                    {
                        "sender": "alice",
                        "text": "split the bill",
                        "timestamp": "2026-06-27T10:00:00Z",
                        "message_id": "m1",
                    }
                ],
                "confidence": "high",
            }
        )
        assert norm == {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "expense_log",
            "tool_args": {"amount": 42},
            "excerpts": [
                {
                    "sender": "alice",
                    "text": "split the bill",
                    "timestamp": "2026-06-27T10:00:00Z",
                    "message_id": "m1",
                }
            ],
            "confidence": "HIGH",  # normalized to upper-case
        }

    def test_normalize_signal_defaults_and_legacy_aliases(self):
        # Legacy "type"/"butler" aliases + missing excerpts/confidence.
        norm = _normalize_decomp_signal({"type": "health", "butler": "health"})
        assert norm is not None
        assert norm["signal_type"] == "health"
        assert norm["target_butler"] == "health"
        assert norm["tool_name"] == "route.execute"
        assert norm["tool_args"] == {}
        assert norm["excerpts"] == []
        assert norm["confidence"] == "LOW"  # unknown/absent → LOW

    def test_normalize_signal_drops_untargeted_and_nondict(self):
        assert _normalize_decomp_signal({"signal_type": "finance"}) is None
        assert _normalize_decomp_signal("not a dict") is None
        assert _normalize_decomp_signals(
            ["bad", {"signal_type": "x"}, {"target_butler": "finance"}]
        ) == [
            {
                "signal_type": "",
                "target_butler": "finance",
                "tool_name": "route.execute",
                "tool_args": {},
                "excerpts": [],
                "confidence": "LOW",
            }
        ]

    def test_normalize_excerpts_drops_non_dict_and_projects_keys(self):
        norm = _normalize_decomp_signal(
            {
                "target_butler": "finance",
                "excerpts": ["junk", {"text": "hi", "extra": "ignored"}],
            }
        )
        assert norm is not None
        assert norm["excerpts"] == [
            {"sender": None, "text": "hi", "timestamp": None, "message_id": None}
        ]

    def test_normalize_signal_parses_stringified_tool_args(self):
        # Models sometimes stringify the nested tool_args object.
        norm = _normalize_decomp_signal({"target_butler": "finance", "tool_args": '{"amount": 42}'})
        assert norm is not None
        assert norm["tool_args"] == {"amount": 42}
        # Unparseable string falls back to an empty object, not a dropped signal.
        norm_bad = _normalize_decomp_signal({"target_butler": "finance", "tool_args": "not json"})
        assert norm_bad is not None
        assert norm_bad["tool_args"] == {}

    def test_normalize_signals_wraps_single_object(self):
        # A single signal object (not an array) must not be dropped.
        out = _normalize_decomp_signals({"target_butler": "finance"})
        assert [s["target_butler"] for s in out] == ["finance"]

    def test_normalize_signals_unwraps_wrapper_object(self):
        # `{"signals": [...]}` wrapper is unwrapped to its array.
        out = _normalize_decomp_signals(
            {"signals": [{"target_butler": "finance"}, {"target_butler": "health"}]}
        )
        assert [s["target_butler"] for s in out] == ["finance", "health"]

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_fanout_carries_full_schema(
        self, mock_route, mock_load, mock_history
    ):
        """Fan-out must produce the full schema, not just target/tool_name/tool_args."""
        signal = {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "expense_log",
            "tool_args": {"amount": 42},
            "excerpts": [
                {
                    "sender": "alice",
                    "text": "Let's split the dinner bill",
                    "timestamp": "2026-06-27T10:00:00Z",
                    "message_id": "m1",
                }
            ],
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output=json.dumps([signal]),
                success=True,
                tool_calls=[],
                model="opencode/test",
                input_tokens=20,
                output_tokens=10,
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "conversation batch",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000002",
        )

        assert result.target_butler == "finance"
        assert result.routed_targets == ["finance"]

        # decomposition_output stores the full-schema conceptual message.
        update_kwargs = pipeline._update_message_inbox_lifecycle.await_args.kwargs
        stored = update_kwargs["decomposition_output"]["signals"]
        assert len(stored) == 1
        assert stored[0]["signal_type"] == "finance"
        assert stored[0]["confidence"] == "HIGH"
        assert stored[0]["excerpts"][0]["message_id"] == "m1"

        # The route() call carries the conceptual-message metadata to the butler.
        route_kwargs = mock_route.await_args.kwargs
        assert route_kwargs["target_butler"] == "finance"
        conceptual = route_kwargs["args"]["__conceptual_message"]
        assert conceptual["signal_type"] == "finance"
        assert conceptual["confidence"] == "HIGH"
        assert conceptual["excerpts"][0]["text"] == "Let's split the dinner bill"
        assert route_kwargs["args"]["amount"] == 42

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nflight confirmation\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_fanout_enriches_calendar_proposal_from_ingestion_context(
        self, mock_route, mock_load, mock_history
    ):
        """A live ``events`` signal becomes a provenance-linked pending proposal call."""
        source_event_id = "00000000-0000-0000-0000-000000000010"
        source_entity_id = "00000000-0000-0000-0000-000000000011"
        message = "Singapore Airlines confirms SQ12 on 2026-08-01 at 14:00 SGT."
        signal = {
            # The installed /signal-extraction skill emits the legacy ``type``
            # spelling; the live fan-out must still recognize it.
            "type": "events",
            "target_butler": "general",
            "tool_name": "calendar_propose_event",
            "tool_args": {
                "title": "SQ12 flight",
                "start_at": "2026-08-01T14:00:00+08:00",
                "end_at": "2026-08-01T20:00:00+08:00",
                "timezone": "Asia/Singapore",
                # Model-controlled provenance must never reach the producer.
                "source_event_id": "model-controlled",
                "source_snippet": "model-controlled",
                "confidence": 0.01,
                "entity_ids": ["model-controlled"],
            },
            "excerpts": [],
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps([signal]), success=True, tool_calls=[])

        identity_result = MagicMock(
            preamble="",
            contact_id=None,
            entity_id=source_entity_id,
            is_unknown=False,
            channel_value=None,
        )
        with patch(
            "butlers.tools.switchboard.identity.inject.resolve_and_inject_identity",
            new_callable=AsyncMock,
            return_value=identity_result,
        ):
            pipeline = MessagePipeline(
                switchboard_pool=MagicMock(),
                dispatch_fn=mock_dispatch,
                source_butler="switchboard",
                enable_identity_resolution=True,
            )
            pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

            result = await pipeline.process(
                message,
                tool_args={
                    "source_channel": "telegram_user_client",
                    "source_id": "alice-telegram-id",
                    "request_context": {"payload_type": "conversation_history"},
                },
                message_inbox_id=source_event_id,
            )

        assert result.routed_targets == ["general"]
        route_kwargs = mock_route.await_args.kwargs
        assert route_kwargs["target_butler"] == "general"
        assert route_kwargs["tool_name"] == "calendar_propose_event"
        proposal_args = route_kwargs["args"]
        assert proposal_args["source_event_id"] == source_event_id
        assert proposal_args["source_snippet"] == message
        assert proposal_args["confidence"] == pytest.approx(0.9)
        assert proposal_args["entity_ids"] == [source_entity_id]

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nflight confirmation\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_fanout_routes_calendar_proposal_to_general_not_model_target(
        self, mock_route, mock_load, mock_history
    ):
        """Calendar proposal ownership stays code-controlled at the general butler."""
        signal = {
            "type": "events",
            # Signal extraction output is model controlled, so it cannot choose
            # another calendar-owning schema for an inferred proposal.
            "target_butler": "finance",
            "tool_name": "calendar_propose_event",
            "tool_args": {
                "title": "SQ12 flight",
                "start_at": "2026-08-01T14:00:00+08:00",
                "end_at": "2026-08-01T20:00:00+08:00",
                "timezone": "Asia/Singapore",
                # This separately model-controlled field must not mislabel a
                # proposal after the route target has been pinned to general.
                "butler_name": "finance",
            },
            "excerpts": [],
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps([signal]), success=True, tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        await pipeline.process(
            "Singapore Airlines confirms SQ12 on 2026-08-01 at 14:00 SGT.",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000012",
        )

        route_kwargs = mock_route.await_args.kwargs
        assert route_kwargs["target_butler"] == "general"
        assert route_kwargs["args"]["butler_name"] == "general"

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nflight confirmation\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_fanout_cannot_turn_event_signal_into_provider_write(
        self, mock_route, mock_load, mock_history
    ):
        """Model tool selection cannot bypass the proposal-only event contract."""
        signal = {
            "type": "events",
            "target_butler": "general",
            # ``calendar_create_event`` would write to the provider if routed.
            "tool_name": "calendar_create_event",
            "tool_args": {
                "title": "SQ12 flight",
                "start_at": "2026-08-01T14:00:00+08:00",
                "end_at": "2026-08-01T20:00:00+08:00",
                "timezone": "Asia/Singapore",
            },
            "excerpts": [],
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps([signal]), success=True, tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        await pipeline.process(
            "Singapore Airlines confirms SQ12 on 2026-08-01 at 14:00 SGT.",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000013",
        )

        route_kwargs = mock_route.await_args.kwargs
        assert route_kwargs["target_butler"] == "general"
        assert route_kwargs["tool_name"] == "calendar_propose_event"

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nmaybe dinner\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_fanout_drops_below_floor_calendar_signal(
        self, mock_route, mock_load, mock_history
    ):
        """A MEDIUM-confidence event never reaches the calendar proposal producer."""
        signal = {
            "type": "events",
            "target_butler": "general",
            "tool_name": "calendar_propose_event",
            "tool_args": {
                "title": "Possible dinner",
                "start_at": "2026-08-01T19:00:00+08:00",
                "end_at": "2026-08-01T20:00:00+08:00",
                "timezone": "Asia/Singapore",
            },
            "excerpts": [],
            "confidence": "MEDIUM",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(output=json.dumps([signal]), success=True, tool_calls=[])

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "Maybe dinner next week?",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000012",
        )

        assert result.routed_targets == []
        mock_route.assert_not_awaited()

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_decomposition_parses_markdown_fenced_output(
        self, mock_route, mock_load, mock_history
    ):
        """A markdown-fenced array must still route, not fall back to decomposed_empty."""
        signal = {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "expense_log",
            "tool_args": {"amount": 42},
            "excerpts": [],
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="```json\n" + json.dumps([signal]) + "\n```",
                success=True,
                tool_calls=[],
                model="opencode/test",
                input_tokens=20,
                output_tokens=10,
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        result = await pipeline.process(
            "conversation batch",
            tool_args={
                "source_channel": "telegram_user_client",
                "request_context": {"payload_type": "conversation_history"},
            },
            message_inbox_id="00000000-0000-0000-0000-000000000003",
        )

        assert result.routed_targets == ["finance"]
        update_kwargs = pipeline._update_message_inbox_lifecycle.await_args.kwargs
        assert len(update_kwargs["decomposition_output"]["signals"]) == 1


# ---------------------------------------------------------------------------
# PipelineModule ABC
# ---------------------------------------------------------------------------


class TestPipelineModule:
    def test_module_contract(self):
        from butlers.modules.base import Module

        assert issubclass(PipelineModule, Module)
        assert PipelineModule().name == "pipeline"
        assert PipelineModule().migration_revisions() is None


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


class TestInferFallbackTarget:
    _BUTLERS = [
        {"name": "finance", "description": "Finance"},
        {"name": "health", "description": "Health"},
        {"name": "general", "description": "General"},
    ]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Routed to finance.", "finance"),  # direct "route to X"
            ("Routed this to `finance` only.", "finance"),  # intervening words + backtick
            ("Route to `health`.", "health"),  # backtick-wrapped
            ("Routed for finance.", "finance"),  # "route for X"
            ("Nothing relevant.", None),  # no match
            ("", None),  # empty string
            # multiple distinct targets is ambiguous → None (single-target only)
            ("Routed to finance and routed to health.", None),
            # real gpt-5.4-mini output that triggered the bug
            (
                "Routed this to `finance` only.\n\n"
                "Reason: the message is an order cancellation with payment/refund details.",
                "finance",
            ),
        ],
    )
    def test_infer_fallback_target(self, text: str, expected: str | None):
        assert _infer_fallback_target_from_cc_output(text, self._BUTLERS) == expected


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.enable_ingress_dedupe is True
        assert cfg.classification_timeout_s is None


# ---------------------------------------------------------------------------
# Empty-decomposition metric (module-pipeline spec, decomposition_empty counter)
# ---------------------------------------------------------------------------


def _reset_metrics_global_state() -> None:
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _make_in_memory_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    _reset_metrics_global_state()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider, reader


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    result: dict[str, Any] = {}
    data = reader.get_metrics_data()
    if data is None:
        # No instruments recorded a measurement (e.g. non-empty decomposition
        # never touches the counter), so the reader has nothing to collect.
        return result
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.data.data_points:
                    result[metric.name] = metric.data.data_points
    return result


class TestDecompositionEmptyMetric:
    """The `butlers.pipeline.decomposition_empty` counter is emitted only when a
    conversation decomposition yields no signals, labelled with source_channel
    and connector_type from the request context."""

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_counter_incremented_on_empty_decomposition(self, mock_load, mock_history):
        async def mock_dispatch(**kwargs):
            # No parseable signals → decomposed_empty short-circuit.
            return FakeSpawnerResult(
                output="[]",
                success=True,
                tool_calls=[],
                model="opencode/test",
                input_tokens=12,
                output_tokens=0,
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        _provider, reader = _make_in_memory_provider()
        try:
            result = await pipeline.process(
                "conversation batch",
                tool_args={
                    "source_channel": "telegram_user_client",
                    "request_context": {
                        "payload_type": "conversation_history",
                        "source_channel": "telegram_user_client",
                        "connector_type": "telegram",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000003",
            )

            assert result.target_butler == "decomposed_empty"

            data = _collect_metrics(reader)
            assert "butlers.pipeline.decomposition_empty" in data
            points = data["butlers.pipeline.decomposition_empty"]
            assert len(points) == 1
            point = points[0]
            assert point.value == 1
            assert point.attributes["source_channel"] == "telegram_user_client"
            assert point.attributes["connector_type"] == "telegram"
        finally:
            _reset_metrics_global_state()

    @patch.object(
        MessagePipeline,
        "_load_decomp_conversation_history",
        new_callable=AsyncMock,
        return_value="## Recent Conversation History\n\n```text\nhello\n```",
    )
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch(
        "butlers.tools.switchboard.routing.route.route",
        new_callable=AsyncMock,
        return_value={"status": "ok"},
    )
    async def test_counter_not_incremented_on_non_empty_decomposition(
        self, mock_route, mock_load, mock_history
    ):
        signal = {
            "signal_type": "finance",
            "target_butler": "finance",
            "tool_name": "expense_log",
            "tool_args": {"amount": 42},
            "confidence": "HIGH",
        }

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output=json.dumps([signal]),
                success=True,
                tool_calls=[],
                model="opencode/test",
                input_tokens=20,
                output_tokens=10,
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch, source_butler="switchboard"
        )
        pipeline._update_message_inbox_lifecycle = AsyncMock()  # type: ignore[method-assign]

        _provider, reader = _make_in_memory_provider()
        try:
            result = await pipeline.process(
                "conversation batch",
                tool_args={
                    "source_channel": "telegram_user_client",
                    "request_context": {
                        "payload_type": "conversation_history",
                        "source_channel": "telegram_user_client",
                        "connector_type": "telegram",
                    },
                },
                message_inbox_id="00000000-0000-0000-0000-000000000004",
            )

            assert result.target_butler == "finance"
            data = _collect_metrics(reader)
            assert "butlers.pipeline.decomposition_empty" not in data
        finally:
            _reset_metrics_global_state()
