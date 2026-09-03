"""Tests for butlers.core.spawner_provider.resolve_provider_config.

REQ-runtime-opencode-001: the Ollama model key registered in the generated
OpenCode provider config must have its ``ollama/`` prefix stripped exactly
once, via the same removeprefix-shaped convention as the CLI execution
boundary mapper (canonical_to_execution_model), not a bespoke split (bu-g34xh).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from butlers.core.spawner_provider import resolve_provider_config

pytestmark = pytest.mark.unit


def _pool_with_ollama_config(base_url: str) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"config": json.dumps({"base_url": base_url})})
    return pool


async def test_resolve_provider_config_strips_ollama_prefix_from_model_key() -> None:
    pool = _pool_with_ollama_config("http://localhost:11434")

    config = await resolve_provider_config(pool, "ollama/qwen3:8b")

    assert config == {
        "ollama": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": "http://localhost:11434/v1"},
            "models": {"qwen3:8b": {"name": "qwen3:8b"}},
        }
    }


async def test_resolve_provider_config_returns_none_for_non_ollama_provider() -> None:
    pool = _pool_with_ollama_config("http://localhost:11434")

    assert await resolve_provider_config(pool, "anthropic/claude-sonnet-4-5") is None


async def test_resolve_provider_config_returns_none_without_provider_prefix() -> None:
    pool = _pool_with_ollama_config("http://localhost:11434")

    assert await resolve_provider_config(pool, "minimax-m2.7") is None
