"""OllamaAdapter — RuntimeAdapter implementation for Ollama via OpenAI-compatible HTTP API.

Encapsulates all Ollama-specific logic:
- Async HTTP POST to Ollama's OpenAI-compatible /v1/chat/completions endpoint
- Base URL resolution: shared.provider_config table, runtime_args override, fallback to localhost
- AGENTS.md system prompt reading (same pattern as OpenCode/Gemini adapters)
- No-op config file (HTTP adapter does not need config files)
- Result parsing: extracts text output, tool calls, and token usage from OpenAI-format response

Unlike subprocess-based adapters, OllamaAdapter sends a single HTTP request per invocation
rather than spawning a child process. The ``binary_name`` property returns 'ollama' for display
purposes only — Ollama does not need to be on PATH when using the HTTP API.

If the Ollama server is unreachable or returns a non-200 response, invoke() raises RuntimeError.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default Ollama API base URL
_DEFAULT_BASE_URL = "http://localhost:11434"

# Default timeout for Ollama HTTP requests (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300


def _extract_base_url_from_runtime_args(runtime_args: list[str] | None) -> str | None:
    """Extract --base-url value from runtime_args list.

    Looks for '--base-url' followed by a URL string in the args list.

    Parameters
    ----------
    runtime_args:
        Optional list of runtime arguments, e.g. ['--base-url', 'http://...'].

    Returns
    -------
    str | None
        The base URL string if found, or None.
    """
    if not runtime_args:
        return None
    for i, arg in enumerate(runtime_args):
        if arg == "--base-url" and i + 1 < len(runtime_args):
            return runtime_args[i + 1]
    return None


async def _resolve_base_url_from_db(db_pool: Any | None) -> str | None:
    """Query shared.provider_config for Ollama base URL.

    Looks for a row WHERE provider_type = 'ollama' and reads config.base_url.
    Handles the case where the table does not yet exist (returns None).

    Parameters
    ----------
    db_pool:
        An asyncpg connection pool with access to the shared schema, or None.

    Returns
    -------
    str | None
        The configured base URL, or None if not found or table missing.
    """
    if db_pool is None:
        return None
    try:
        row = await db_pool.fetchrow(
            """
            SELECT config FROM shared.provider_config
            WHERE provider_type = 'ollama'
            LIMIT 1
            """,
        )
        if row is None:
            return None
        config = row["config"]
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(config, dict):
            base_url = config.get("base_url")
            if isinstance(base_url, str) and base_url.strip():
                return base_url.strip()
        return None
    except Exception as exc:  # noqa: BLE001
        # Table may not exist yet (parallel PR #648); log at DEBUG and fall back
        logger.debug("Could not query shared.provider_config for Ollama base URL: %s", exc)
        return None


def _parse_chat_completion_response(
    response_data: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Parse an OpenAI-format chat completion response.

    Extracts assistant text, tool calls, and token usage from the response.

    The response shape is::

        {
            "choices": [{"message": {"role": "assistant", "content": "...",
                                     "tool_calls": [...]}}],
            "usage": {"prompt_tokens": N, "completion_tokens": M, "total_tokens": K}
        }

    Parameters
    ----------
    response_data:
        Parsed JSON dict from the Ollama /v1/chat/completions endpoint.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
        (result_text, tool_calls, usage)
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, Any] | None = None

    # Extract usage
    usage_data = response_data.get("usage")
    if isinstance(usage_data, dict):
        prompt_tokens = usage_data.get("prompt_tokens")
        completion_tokens = usage_data.get("completion_tokens")
        if prompt_tokens is not None or completion_tokens is not None:
            usage = {
                "input_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "output_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
            }

    # Extract content and tool calls from choices
    choices = response_data.get("choices", [])
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message", {})
        if not isinstance(message, dict):
            continue

        # Text content
        content = message.get("content")
        if isinstance(content, str) and content:
            text_parts.append(content)

        # Tool calls
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for tc in raw_tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id", "")
                function = tc.get("function", {})
                if not isinstance(function, dict):
                    continue
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                # OpenAI format may return arguments as a JSON string
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, ValueError):
                        pass  # Leave as string
                tool_calls.append(
                    {
                        "id": tc_id if isinstance(tc_id, str) else "",
                        "name": name if isinstance(name, str) else "",
                        "input": arguments if isinstance(arguments, dict) else {},
                    }
                )

    result_text = "\n".join(part for part in text_parts if part) or None
    return result_text, tool_calls, usage


class OllamaAdapter(RuntimeAdapter):
    """Runtime adapter for Ollama via its OpenAI-compatible HTTP API.

    Sends an async HTTP POST to Ollama's /v1/chat/completions endpoint instead
    of spawning a subprocess. The 'ollama' binary does not need to be on PATH
    when using the HTTP adapter; ``binary_name`` is used for display only.

    Base URL resolution order:
    1. ``runtime_args`` override (``--base-url <url>``)
    2. ``shared.provider_config`` table (``provider_type = 'ollama'``)
    3. Default: ``http://localhost:11434``

    Parameters
    ----------
    base_url:
        Optional fixed base URL. If provided, skips DB lookup and runtime_args parsing.
    db_pool:
        Optional asyncpg connection pool for reading shared.provider_config.
        When None, only runtime_args and the default URL are used.
    """

    def __init__(
        self,
        base_url: str | None = None,
        db_pool: Any | None = None,
    ) -> None:
        self._base_url = base_url
        self._db_pool = db_pool

    @property
    def binary_name(self) -> str:
        """Return 'ollama' for display purposes (binary not required on PATH)."""
        return "ollama"

    def create_worker(self) -> RuntimeAdapter:
        """Return an independent adapter for pooled spawner workers."""
        return OllamaAdapter(base_url=self._base_url, db_pool=self._db_pool)

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read system prompt from the butler's config directory.

        Reads AGENTS.md from the butler's config directory. Returns the file
        contents, or an empty string if the file does not exist.

        Parameters
        ----------
        config_dir:
            Path to the butler's config directory.

        Returns
        -------
        str
            The parsed system prompt text.
        """
        agents_md = config_dir / "AGENTS.md"
        if agents_md.exists():
            content = agents_md.read_text().strip()
            if content:
                return content
        return ""

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """No-op for HTTP adapters — returns an empty JSON placeholder file.

        Ollama communicates via HTTP, so no runtime config file is needed.
        An empty JSON file is written to satisfy the ABC interface.

        Parameters
        ----------
        mcp_servers:
            Ignored (Ollama HTTP adapter does not use MCP config files).
        tmp_dir:
            Temporary directory to write the placeholder into.

        Returns
        -------
        Path
            Path to the generated placeholder file.
        """
        config_path = tmp_dir / "ollama.json"
        config_path.write_text("{}")
        return config_path

    async def _get_base_url(self, runtime_args: list[str] | None) -> str:
        """Resolve the effective base URL for this invocation.

        Resolution order:
        1. Fixed base_url set at construction time.
        2. ``--base-url`` from runtime_args.
        3. shared.provider_config DB lookup.
        4. Default: http://localhost:11434.

        Parameters
        ----------
        runtime_args:
            Optional per-call runtime args that may contain ``--base-url``.

        Returns
        -------
        str
            The resolved base URL (no trailing slash).
        """
        if self._base_url is not None:
            return self._base_url.rstrip("/")

        # runtime_args override takes precedence over DB config
        args_url = _extract_base_url_from_runtime_args(runtime_args)
        if args_url:
            return args_url.rstrip("/")

        # Try DB-configured URL
        db_url = await _resolve_base_url_from_db(self._db_pool)
        if db_url:
            return db_url.rstrip("/")

        return _DEFAULT_BASE_URL

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Invoke Ollama via async HTTP POST to /v1/chat/completions.

        Constructs a messages array from system_prompt and prompt, sends a single
        HTTP request to Ollama's OpenAI-compatible endpoint, and parses the response.

        Parameters
        ----------
        prompt:
            The user prompt to send.
        system_prompt:
            System-level instructions (from AGENTS.md).
        mcp_servers:
            Ignored (Ollama HTTP adapter does not use MCP servers).
        env:
            Ignored (no subprocess to receive env vars).
        max_turns:
            Ignored (single-turn HTTP request).
        model:
            Model identifier to use (e.g. 'llama3.2', 'mistral'). Required.
        runtime_args:
            Optional args; ``--base-url <url>`` overrides the default base URL.
        cwd:
            Ignored (no subprocess).
        timeout:
            Maximum seconds to wait for the HTTP response.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage).

        Raises
        ------
        RuntimeError
            If the server returns a non-200 HTTP response.
        httpx.TimeoutException
            If the request exceeds the timeout.
        """
        effective_timeout = float(timeout or _DEFAULT_TIMEOUT_SECONDS)
        base_url = await self._get_base_url(runtime_args)
        endpoint = f"{base_url}/v1/chat/completions"

        # Build messages array
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model or "llama3.2",
            "messages": messages,
        }

        logger.debug(
            "OllamaAdapter: POST %s (model=%r, messages=%d)",
            endpoint,
            payload["model"],
            len(messages),
        )

        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            try:
                response = await client.post(endpoint, json=payload)
            except httpx.TimeoutException:
                logger.error(
                    "OllamaAdapter: request to %s timed out after %.0fs",
                    endpoint,
                    effective_timeout,
                )
                raise

        if response.status_code != 200:
            error_body = response.text[:500]
            logger.error(
                "OllamaAdapter: HTTP %d from %s: %s",
                response.status_code,
                endpoint,
                error_body,
            )
            raise RuntimeError(f"Ollama returned HTTP {response.status_code}: {error_body}")

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Ollama returned non-JSON response: {response.text[:200]}") from exc

        return _parse_chat_completion_response(data)


# Register the Ollama adapter
register_adapter("ollama", OllamaAdapter)
