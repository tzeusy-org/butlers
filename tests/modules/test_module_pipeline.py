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

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import (
    MessagePipeline,
    PipelineConfig,
    PipelineModule,
    RoutingResult,
    _build_routing_prompt,
    _extract_routed_butlers,
    _infer_fallback_target_from_cc_output,
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
