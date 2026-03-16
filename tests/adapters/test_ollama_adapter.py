"""Tests for OllamaAdapter — Ollama OpenAI-compatible HTTP runtime adapter.

Covers:
- Registration: get_adapter('ollama') returns OllamaAdapter.
- invoke(): successful chat completion response parsing.
- invoke(): non-200 HTTP error response raises RuntimeError.
- invoke(): timeout raises httpx.TimeoutException.
- Base URL resolution: runtime_args --base-url override.
- Base URL resolution: shared.provider_config DB query.
- Base URL resolution: fallback to http://localhost:11434 when nothing configured.
- parse_system_prompt_file(): reads AGENTS.md, returns empty string when absent.
- build_config_file(): no-op — returns empty JSON placeholder.
- _parse_chat_completion_response(): text extraction, tool call extraction, usage extraction.
- binary_name property returns 'ollama'.
- create_worker() returns independent instance.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.core.runtimes import OllamaAdapter, get_adapter
from butlers.core.runtimes.ollama import (
    _DEFAULT_BASE_URL,
    _extract_base_url_from_runtime_args,
    _parse_chat_completion_response,
    _resolve_base_url_from_db,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_ollama_adapter_registered():
    """get_adapter('ollama') returns OllamaAdapter."""
    assert get_adapter("ollama") is OllamaAdapter


def test_ollama_adapter_importable_from_runtimes():
    """OllamaAdapter is importable from butlers.core.runtimes."""
    import butlers.core.runtimes as runtimes_module

    assert runtimes_module.OllamaAdapter is OllamaAdapter


# ---------------------------------------------------------------------------
# Basic adapter properties
# ---------------------------------------------------------------------------


def test_ollama_adapter_instantiates():
    """OllamaAdapter can be instantiated without arguments."""
    adapter = OllamaAdapter()
    assert adapter is not None


def test_ollama_adapter_binary_name():
    """binary_name returns 'ollama'."""
    assert OllamaAdapter().binary_name == "ollama"


def test_ollama_adapter_create_worker_returns_distinct_instance():
    """create_worker() returns a distinct OllamaAdapter instance."""
    adapter = OllamaAdapter(base_url="http://myhost:11434")
    worker = adapter.create_worker()

    assert worker is not adapter
    assert isinstance(worker, OllamaAdapter)
    assert worker._base_url == "http://myhost:11434"


def test_ollama_adapter_create_worker_no_base_url():
    """create_worker() preserves None base_url and db_pool."""
    adapter = OllamaAdapter()
    worker = adapter.create_worker()

    assert worker is not adapter
    assert worker._base_url is None
    assert worker._db_pool is None


# ---------------------------------------------------------------------------
# parse_system_prompt_file
# ---------------------------------------------------------------------------


def test_parse_system_prompt_file_reads_agents_md(tmp_path: Path):
    """parse_system_prompt_file() returns AGENTS.md contents."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("You are a helpful assistant.\n")

    adapter = OllamaAdapter()
    result = adapter.parse_system_prompt_file(tmp_path)

    assert result == "You are a helpful assistant."


def test_parse_system_prompt_file_missing_returns_empty(tmp_path: Path):
    """parse_system_prompt_file() returns empty string when AGENTS.md is absent."""
    adapter = OllamaAdapter()
    result = adapter.parse_system_prompt_file(tmp_path)
    assert result == ""


def test_parse_system_prompt_file_empty_file_returns_empty(tmp_path: Path):
    """parse_system_prompt_file() returns empty string when AGENTS.md is empty."""
    (tmp_path / "AGENTS.md").write_text("   \n")
    adapter = OllamaAdapter()
    result = adapter.parse_system_prompt_file(tmp_path)
    assert result == ""


# ---------------------------------------------------------------------------
# build_config_file
# ---------------------------------------------------------------------------


def test_build_config_file_returns_empty_json(tmp_path: Path):
    """build_config_file() writes an empty JSON placeholder and returns its path."""
    adapter = OllamaAdapter()
    config_path = adapter.build_config_file(mcp_servers={"foo": "bar"}, tmp_dir=tmp_path)

    assert config_path.exists()
    assert json.loads(config_path.read_text()) == {}


def test_build_config_file_named_ollama_json(tmp_path: Path):
    """build_config_file() creates 'ollama.json' in the given directory."""
    adapter = OllamaAdapter()
    config_path = adapter.build_config_file(mcp_servers={}, tmp_dir=tmp_path)
    assert config_path.name == "ollama.json"


# ---------------------------------------------------------------------------
# _extract_base_url_from_runtime_args
# ---------------------------------------------------------------------------


def test_extract_base_url_from_runtime_args_found():
    """_extract_base_url_from_runtime_args() returns URL after --base-url flag."""
    result = _extract_base_url_from_runtime_args(["--base-url", "http://myhost:11434"])
    assert result == "http://myhost:11434"


def test_extract_base_url_from_runtime_args_none():
    """_extract_base_url_from_runtime_args() returns None for empty/None args."""
    assert _extract_base_url_from_runtime_args(None) is None
    assert _extract_base_url_from_runtime_args([]) is None


def test_extract_base_url_from_runtime_args_no_flag():
    """_extract_base_url_from_runtime_args() returns None when --base-url absent."""
    assert _extract_base_url_from_runtime_args(["--other-flag", "value"]) is None


def test_extract_base_url_from_runtime_args_flag_at_end():
    """_extract_base_url_from_runtime_args() returns None when --base-url is last arg."""
    assert _extract_base_url_from_runtime_args(["--base-url"]) is None


# ---------------------------------------------------------------------------
# _resolve_base_url_from_db
# ---------------------------------------------------------------------------


async def test_resolve_base_url_from_db_returns_url():
    """_resolve_base_url_from_db() returns base_url from provider_config row."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = {"config": json.dumps({"base_url": "http://dbhost:11434"})}

    result = await _resolve_base_url_from_db(mock_pool)

    assert result == "http://dbhost:11434"
    mock_pool.fetchrow.assert_awaited_once()


async def test_resolve_base_url_from_db_no_row_returns_none():
    """_resolve_base_url_from_db() returns None when no row found."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None

    result = await _resolve_base_url_from_db(mock_pool)
    assert result is None


async def test_resolve_base_url_from_db_none_pool_returns_none():
    """_resolve_base_url_from_db() returns None immediately with None pool."""
    result = await _resolve_base_url_from_db(None)
    assert result is None


async def test_resolve_base_url_from_db_table_missing_returns_none():
    """_resolve_base_url_from_db() returns None when table doesn't exist yet (fallback)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.side_effect = Exception("relation shared.provider_config does not exist")

    result = await _resolve_base_url_from_db(mock_pool)
    assert result is None


async def test_resolve_base_url_from_db_dict_config():
    """_resolve_base_url_from_db() handles asyncpg Record-like dict config."""
    mock_pool = AsyncMock()
    # asyncpg may return config already as a dict if using jsonb column
    mock_pool.fetchrow.return_value = {"config": {"base_url": "http://pgdict:11434"}}

    result = await _resolve_base_url_from_db(mock_pool)
    assert result == "http://pgdict:11434"


# ---------------------------------------------------------------------------
# _parse_chat_completion_response
# ---------------------------------------------------------------------------


def test_parse_chat_completion_response_text():
    """_parse_chat_completion_response() extracts assistant text content."""
    response = {
        "choices": [{"message": {"role": "assistant", "content": "Hello, world!"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    result_text, tool_calls, usage = _parse_chat_completion_response(response)

    assert result_text == "Hello, world!"
    assert tool_calls == []
    assert usage == {"input_tokens": 10, "output_tokens": 5}


def test_parse_chat_completion_response_no_content():
    """_parse_chat_completion_response() returns None text when content is empty."""
    response = {
        "choices": [{"message": {"role": "assistant", "content": ""}}],
    }
    result_text, tool_calls, usage = _parse_chat_completion_response(response)

    assert result_text is None
    assert tool_calls == []
    assert usage is None


def test_parse_chat_completion_response_tool_calls():
    """_parse_chat_completion_response() extracts tool calls correctly."""
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "function": {
                                "name": "get_weather",
                                "arguments": json.dumps({"city": "London"}),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }
    result_text, tool_calls, usage = _parse_chat_completion_response(response)

    assert result_text is None
    assert len(tool_calls) == 1
    assert tool_calls[0] == {
        "id": "call_abc123",
        "name": "get_weather",
        "input": {"city": "London"},
    }
    assert usage == {"input_tokens": 20, "output_tokens": 10}


def test_parse_chat_completion_response_tool_calls_dict_arguments():
    """_parse_chat_completion_response() handles tool call arguments already as dict."""
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_xyz",
                            "function": {
                                "name": "my_tool",
                                "arguments": {"key": "value"},
                            },
                        }
                    ]
                }
            }
        ]
    }
    _, tool_calls, _ = _parse_chat_completion_response(response)

    assert tool_calls[0]["input"] == {"key": "value"}


def test_parse_chat_completion_response_usage_none_when_missing():
    """_parse_chat_completion_response() returns None usage when field absent."""
    response = {
        "choices": [{"message": {"content": "hi"}}],
    }
    _, _, usage = _parse_chat_completion_response(response)
    assert usage is None


def test_parse_chat_completion_response_empty_choices():
    """_parse_chat_completion_response() handles empty choices gracefully."""
    result_text, tool_calls, usage = _parse_chat_completion_response({"choices": []})
    assert result_text is None
    assert tool_calls == []
    assert usage is None


# ---------------------------------------------------------------------------
# invoke() — successful path
# ---------------------------------------------------------------------------


async def test_invoke_successful_response():
    """invoke() sends POST and returns parsed (result_text, tool_calls, usage)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "The answer is 42."}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 8},
    }

    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result_text, tool_calls, usage = await adapter.invoke(
            prompt="What is the meaning of life?",
            system_prompt="You are concise.",
            mcp_servers={},
            env={},
            model="llama3.2",
        )

    assert result_text == "The answer is 42."
    assert tool_calls == []
    assert usage == {"input_tokens": 15, "output_tokens": 8}

    # Verify the POST was called with correct endpoint and payload
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://localhost:11434/v1/chat/completions"
    payload = call_args[1]["json"]
    assert payload["model"] == "llama3.2"
    assert payload["messages"] == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "What is the meaning of life?"},
    ]


async def test_invoke_no_system_prompt():
    """invoke() excludes system message when system_prompt is empty."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "OK"}}],
    }

    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await adapter.invoke(
            prompt="Hello",
            system_prompt="",
            mcp_servers={},
            env={},
            model="llama3.2",
        )

    payload = mock_client.post.call_args[1]["json"]
    # Only user message, no system message
    assert payload["messages"] == [{"role": "user", "content": "Hello"}]


# ---------------------------------------------------------------------------
# invoke() — error path
# ---------------------------------------------------------------------------


async def test_invoke_non_200_raises_runtime_error():
    """invoke() raises RuntimeError when HTTP status is non-200."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"

    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="HTTP 503"):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
                model="llama3.2",
            )


async def test_invoke_timeout_raises():
    """invoke() propagates httpx.TimeoutException on timeout."""
    adapter = OllamaAdapter(base_url="http://localhost:11434")

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(httpx.TimeoutException):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
                model="llama3.2",
                timeout=1,
            )


# ---------------------------------------------------------------------------
# Base URL resolution in invoke()
# ---------------------------------------------------------------------------


async def test_invoke_uses_runtime_args_base_url():
    """invoke() uses --base-url from runtime_args over the default."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hi"}}],
    }

    adapter = OllamaAdapter()  # no fixed base_url

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            model="llama3.2",
            runtime_args=["--base-url", "http://custom-host:11434"],
        )

    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://custom-host:11434/v1/chat/completions"


async def test_invoke_uses_db_base_url():
    """invoke() uses base URL from provider_config when no runtime_args override."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hi"}}],
    }

    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = {"config": json.dumps({"base_url": "http://dbhost:11434"})}

    adapter = OllamaAdapter(db_pool=mock_pool)

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            model="llama3.2",
        )

    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://dbhost:11434/v1/chat/completions"


async def test_invoke_falls_back_to_default_base_url():
    """invoke() falls back to http://localhost:11434 when nothing is configured."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hi"}}],
    }

    adapter = OllamaAdapter()  # no base_url, no db_pool, no runtime_args

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            model="llama3.2",
        )

    call_url = mock_client.post.call_args[0][0]
    assert call_url == f"{_DEFAULT_BASE_URL}/v1/chat/completions"


async def test_invoke_runtime_args_overrides_db_url():
    """runtime_args --base-url takes precedence over DB-configured URL."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "hi"}}],
    }

    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = {"config": json.dumps({"base_url": "http://dbhost:11434"})}

    adapter = OllamaAdapter(db_pool=mock_pool)

    with patch("butlers.core.runtimes.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await adapter.invoke(
            prompt="test",
            system_prompt="",
            mcp_servers={},
            env={},
            model="llama3.2",
            runtime_args=["--base-url", "http://override:11434"],
        )

    call_url = mock_client.post.call_args[0][0]
    assert call_url == "http://override:11434/v1/chat/completions"
    # DB should NOT have been queried (runtime_args takes priority)
    mock_pool.fetchrow.assert_not_awaited()
