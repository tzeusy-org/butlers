"""Tests for ApiAdapter — direct Anthropic Messages API runtime adapter (bu-qvnce.12).

Covers:
- mcp_servers must be empty (invoke() and build_config_file() both fail loud)
- API key resolution order: caller env > credential store > os.environ
- messages.create() call shape (model, max_tokens, system, messages)
- response parsing: text extraction, tool_use extraction, usage mapping
  (cache_read/cache_creation tokens kept separate from input_tokens)
- timeout and generic-error paths set last_process_info and re-raise
- reset() drops the cached client
- parse_system_prompt_file() reads CLAUDE.md like the CLI adapters
- create_adapter("api", ...) resolves via the registry
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import ApiAdapter, create_adapter
from butlers.core.runtimes.base import get_adapter

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _default_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed a dummy ANTHROPIC_API_KEY so tests unrelated to key resolution don't
    need to thread one through every ``env={}`` call. Tests exercising key
    resolution itself override this via ``patch.dict(os.environ, ..., clear=True)``.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-default-key")


def _mock_response(
    text: str | None = "FORWARD: looks like a real question",
    tool_calls: list[dict] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> SimpleNamespace:
    content = []
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    for call in tool_calls or []:
        content.append(
            SimpleNamespace(type="tool_use", id=call["id"], name=call["name"], input=call["input"])
        )
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
    )
    return SimpleNamespace(content=content, usage=usage)


def _adapter_with_client(response: SimpleNamespace | None = None) -> tuple[ApiAdapter, AsyncMock]:
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response or _mock_response())
    adapter = ApiAdapter(client=client)
    return adapter, client


def test_registered_in_adapter_registry() -> None:
    """create_adapter('api', ...) resolves to ApiAdapter via the registry."""
    assert get_adapter("api") is ApiAdapter
    instance = create_adapter("api")
    assert isinstance(instance, ApiAdapter)


async def test_invoke_rejects_nonempty_mcp_servers() -> None:
    """ApiAdapter never bridges MCP tool wiring — fail loud, not silent."""
    adapter, client = _adapter_with_client()
    with pytest.raises(RuntimeError, match="does not support MCP tool wiring"):
        await adapter.invoke(
            prompt="hi",
            system_prompt="",
            mcp_servers={"some-server": {}},
            env={},
            model="claude-haiku-4-5-20251001",
        )
    client.messages.create.assert_not_called()


def test_build_config_file_rejects_nonempty_mcp_servers(tmp_path: Path) -> None:
    adapter = ApiAdapter()
    with pytest.raises(RuntimeError, match="does not support MCP tool wiring"):
        adapter.build_config_file({"some-server": {}}, tmp_path)

    path = adapter.build_config_file({}, tmp_path)
    assert path.exists() and path.read_text() == "{}"


async def test_invoke_requires_explicit_model() -> None:
    adapter, _client = _adapter_with_client()
    with pytest.raises(ValueError, match="requires an explicit model id"):
        await adapter.invoke(prompt="hi", system_prompt="", mcp_servers={}, env={}, model=None)


async def test_invoke_call_shape_and_text_result() -> None:
    """messages.create() receives model/max_tokens/system/messages; text is extracted."""
    adapter, client = _adapter_with_client(_mock_response(text="IGNORE"))

    result_text, tool_calls, usage = await adapter.invoke(
        prompt="ambient chatter",
        system_prompt="You are a discretion filter.",
        mcp_servers={},
        env={},
        model="claude-haiku-4-5-20251001",
    )

    assert result_text == "IGNORE"
    assert tool_calls == []
    assert usage == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "claude-haiku-4-5-20251001"
    assert kwargs["max_tokens"] > 0
    assert kwargs["system"] == "You are a discretion filter."
    assert kwargs["messages"] == [{"role": "user", "content": "ambient chatter"}]


async def test_invoke_omits_system_when_blank() -> None:
    """An empty system_prompt is omitted (NOT_GIVEN), not sent as system=''."""
    import anthropic

    adapter, client = _adapter_with_client()
    await adapter.invoke(
        prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
    )
    _, kwargs = client.messages.create.call_args
    assert kwargs["system"] is anthropic.NOT_GIVEN


async def test_invoke_extracts_tool_calls_and_cache_tokens() -> None:
    """tool_use blocks and cache_read/cache_creation buckets are mapped through."""
    response = _mock_response(
        text=None,
        tool_calls=[{"id": "tu_1", "name": "classify", "input": {"label": "spam"}}],
        input_tokens=50,
        output_tokens=10,
        cache_read_input_tokens=40,
        cache_creation_input_tokens=5,
    )
    adapter, _client = _adapter_with_client(response)

    result_text, tool_calls, usage = await adapter.invoke(
        prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
    )

    assert result_text is None
    assert tool_calls == [{"id": "tu_1", "name": "classify", "input": {"label": "spam"}}]
    assert usage["input_tokens"] == 50
    assert usage["cache_read_input_tokens"] == 40
    assert usage["cache_creation_input_tokens"] == 5


async def test_invoke_timeout_sets_last_process_info_and_reraises() -> None:
    client = AsyncMock()

    async def _hang(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)

    client.messages.create = AsyncMock(side_effect=_hang)
    adapter = ApiAdapter(client=client)

    with pytest.raises(TimeoutError, match="timed out"):
        await adapter.invoke(
            prompt="hi",
            system_prompt="",
            mcp_servers={},
            env={},
            model="claude-haiku-4-5-20251001",
            timeout=0.01,
        )
    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == -1
    assert info["is_pre_tool_call"] is True
    assert info["runtime_type"] == "api"


async def test_invoke_sdk_error_wrapped_as_runtime_error() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("upstream 500"))
    adapter = ApiAdapter(client=client)

    with pytest.raises(RuntimeError, match="ApiAdapter invocation failed"):
        await adapter.invoke(
            prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
        )
    info = adapter.last_process_info
    assert info is not None
    assert info["error_detail"] == "upstream 500"
    assert info["is_pre_tool_call"] is True


async def test_invoke_success_clears_last_process_info_error_fields() -> None:
    adapter, _client = _adapter_with_client()
    await adapter.invoke(
        prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
    )
    info = adapter.last_process_info
    assert info == {
        "pid": None,
        "exit_code": 0,
        "command": "api:claude-haiku-4-5-20251001",
        "stderr": "",
        "runtime_type": "api",
    }


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


async def test_api_key_resolution_caller_env_wins() -> None:
    adapter, client = _adapter_with_client()
    await adapter.invoke(
        prompt="hi",
        system_prompt="",
        mcp_servers={},
        env={"ANTHROPIC_API_KEY": "from-caller-env"},
        model="claude-haiku-4-5-20251001",
    )
    client.messages.create.assert_awaited_once()


async def test_api_key_resolution_credential_store_used_when_env_absent() -> None:
    store = AsyncMock()
    store.load = AsyncMock(return_value="from-store")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_response())
    adapter = ApiAdapter(credential_store=store, client=client)

    await adapter.invoke(
        prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
    )
    store.load.assert_awaited_once_with("cli-auth/claude")


async def test_api_key_resolution_env_var_fallback_and_missing_key_raises() -> None:
    adapter, client = _adapter_with_client()

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-os-environ"}, clear=False):
        await adapter.invoke(
            prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
        )
    client.messages.create.assert_awaited_once()

    adapter2, client2 = _adapter_with_client()
    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch.dict(os.environ, env_without_key, clear=True):
        with pytest.raises(RuntimeError, match="no Anthropic API key available"):
            await adapter2.invoke(
                prompt="hi",
                system_prompt="",
                mcp_servers={},
                env={},
                model="claude-haiku-4-5-20251001",
            )
    client2.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Misc adapter contract
# ---------------------------------------------------------------------------


def test_parse_system_prompt_file(tmp_path: Path) -> None:
    adapter = ApiAdapter()
    assert adapter.parse_system_prompt_file(tmp_path) == ""

    (tmp_path / "CLAUDE.md").write_text("You are a butler.")
    assert adapter.parse_system_prompt_file(tmp_path) == "You are a butler."


async def test_reset_drops_cached_client() -> None:
    adapter, _client = _adapter_with_client()
    await adapter.invoke(
        prompt="hi", system_prompt="", mcp_servers={}, env={}, model="claude-haiku-4-5-20251001"
    )
    # client override always wins over the cache, so directly exercise the
    # cache-building path without an override to prove reset() clears it.
    real_adapter = ApiAdapter()
    with patch("butlers.core.runtimes.api.anthropic.AsyncAnthropic") as mock_ctor:
        mock_ctor.return_value = AsyncMock()
        await real_adapter._resolve_api_key({"ANTHROPIC_API_KEY": "k"})
        real_adapter._get_client("k")
        assert real_adapter._client is not None
        await real_adapter.reset()
        assert real_adapter._client is None
        assert real_adapter._client_api_key is None


def test_binary_name_is_diagnostic_placeholder() -> None:
    assert ApiAdapter().binary_name == "anthropic-api"


# ---------------------------------------------------------------------------
# invoke_structured() — structured tool-use fast lane (bu-qvnce.12 slice 3)
# ---------------------------------------------------------------------------

_ROUTE_TOOL_SCHEMA = {
    "name": "route_to_butler",
    "description": "Route to a butler.",
    "input_schema": {
        "type": "object",
        "properties": {"butler": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["butler", "prompt"],
    },
}


async def test_invoke_structured_requires_tools() -> None:
    adapter, client = _adapter_with_client()
    with pytest.raises(ValueError, match="requires a non-empty tools list"):
        await adapter.invoke_structured(
            prompt="hi", system_prompt="", tools=[], env={}, model="claude-haiku-4-5-20251001"
        )
    client.messages.create.assert_not_called()


async def test_invoke_structured_requires_explicit_model() -> None:
    adapter, client = _adapter_with_client()
    with pytest.raises(ValueError, match="requires an explicit model id"):
        await adapter.invoke_structured(
            prompt="hi", system_prompt="", tools=[_ROUTE_TOOL_SCHEMA], env={}, model=None
        )
    client.messages.create.assert_not_called()


async def test_invoke_structured_call_shape_forces_tool_choice() -> None:
    """messages.create() receives tools + a forced tool_choice, unlike invoke()."""
    response = _mock_response(
        text=None,
        tool_calls=[
            {"id": "tu_1", "name": "route_to_butler", "input": {"butler": "health", "prompt": "x"}}
        ],
    )
    adapter, client = _adapter_with_client(response)

    tool_calls, text, usage = await adapter.invoke_structured(
        prompt="classify this",
        system_prompt="",
        tools=[_ROUTE_TOOL_SCHEMA],
        env={},
        model="claude-haiku-4-5-20251001",
    )

    assert text is None
    assert tool_calls == [
        {"id": "tu_1", "name": "route_to_butler", "input": {"butler": "health", "prompt": "x"}}
    ]
    assert usage is not None

    _, kwargs = client.messages.create.call_args
    assert kwargs["tools"] == [_ROUTE_TOOL_SCHEMA]
    assert kwargs["tool_choice"] == {"type": "any"}
    assert kwargs["messages"] == [{"role": "user", "content": "classify this"}]


async def test_invoke_structured_timeout_sets_last_process_info_and_reraises() -> None:
    client = AsyncMock()

    async def _hang(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)

    client.messages.create = AsyncMock(side_effect=_hang)
    adapter = ApiAdapter(client=client)

    with pytest.raises(TimeoutError, match="structured invocation timed out"):
        await adapter.invoke_structured(
            prompt="hi",
            system_prompt="",
            tools=[_ROUTE_TOOL_SCHEMA],
            env={},
            model="claude-haiku-4-5-20251001",
            timeout=0.01,
        )
    info = adapter.last_process_info
    assert info is not None
    assert info["exit_code"] == -1
    assert info["is_pre_tool_call"] is True


async def test_invoke_structured_sdk_error_wrapped_as_runtime_error() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("upstream 500"))
    adapter = ApiAdapter(client=client)

    with pytest.raises(RuntimeError, match="ApiAdapter structured invocation failed"):
        await adapter.invoke_structured(
            prompt="hi",
            system_prompt="",
            tools=[_ROUTE_TOOL_SCHEMA],
            env={},
            model="claude-haiku-4-5-20251001",
        )
    info = adapter.last_process_info
    assert info is not None
    assert "upstream 500" in info["error_detail"]
    assert info["is_pre_tool_call"] is True


async def test_invoke_structured_no_api_key_raises() -> None:
    adapter, client = _adapter_with_client()
    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch.dict(os.environ, env_without_key, clear=True):
        with pytest.raises(RuntimeError, match="no Anthropic API key available"):
            await adapter.invoke_structured(
                prompt="hi",
                system_prompt="",
                tools=[_ROUTE_TOOL_SCHEMA],
                env={},
                model="claude-haiku-4-5-20251001",
            )
    client.messages.create.assert_not_called()
