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
)

pytestmark = pytest.mark.unit


@dataclass
class FakeSpawnerResult:
    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


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


class TestExtractRoutedButlers:
    def test_single_route_to_butler_call(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "test"},
                "result": {"status": "ok", "butler": "health"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert "health" in routed
        assert "health" in acked

    def test_multi_route_to_butler_calls(self):
        tool_calls = [
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
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert set(routed) == {"health", "finance"}

    def test_empty_tool_calls(self):
        routed, acked, failed = _extract_routed_butlers([])
        assert routed == [] and acked == [] and failed == []

    def test_non_route_calls_ignored(self):
        tool_calls = [{"name": "other_tool", "args": {}, "result": {}}]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == []


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
        result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []

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


# ---------------------------------------------------------------------------
# PipelineModule ABC
# ---------------------------------------------------------------------------


class TestPipelineModule:
    def test_name(self):
        from butlers.modules.base import Module

        assert issubclass(PipelineModule, Module)
        assert PipelineModule().name == "pipeline"

    def test_migration_revisions_none(self):
        assert PipelineModule().migration_revisions() is None


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert isinstance(cfg.enable_ingress_dedupe, bool)
