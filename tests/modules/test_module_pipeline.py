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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import (
    MessagePipeline,
    PipelineConfig,
    PipelineModule,
    RoutingResult,
    _build_decomposition_prompt,
    _build_routing_prompt,
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
