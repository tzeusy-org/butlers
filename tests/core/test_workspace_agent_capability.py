"""WORKSPACE_AGENT: CLI runtimes can run an engineering executor; the direct API cannot."""

from __future__ import annotations

import pytest

from butlers.core.model_capabilities import ModelFeature, Support, capability_baseline_of_adapter
from butlers.core.runtimes.api import ApiAdapter
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.codex import CodexAdapter
from butlers.core.runtimes.gemini import GeminiAdapter
from butlers.core.runtimes.opencode import OpenCodeAdapter


@pytest.mark.parametrize(
    "adapter", [ClaudeCodeAdapter, CodexAdapter, GeminiAdapter, OpenCodeAdapter]
)
def test_cli_adapters_are_workspace_agents(adapter: type) -> None:
    baseline = capability_baseline_of_adapter(adapter)
    assert baseline.support(ModelFeature.WORKSPACE_AGENT) is Support.SUPPORTED


def test_the_api_adapter_is_hard_unfit_for_executors() -> None:
    baseline = capability_baseline_of_adapter(ApiAdapter)
    assert baseline.support(ModelFeature.WORKSPACE_AGENT) is Support.UNSUPPORTED
