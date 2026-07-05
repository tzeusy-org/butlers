"""ApiAdapter — RuntimeAdapter implementation calling the Anthropic SDK directly.

No CLI subprocess, no MCP bootstrap: a single ``AsyncAnthropic.messages.create``
call per ``invoke()``. Intended for the highest-volume, lowest-latency-budget
call sites — connector discretion screening and switchboard classification —
where the cold CLI-spawn + MCP-handshake latency of the subprocess adapters
(``claude_code.py``, ``codex.py``, ``gemini.py``, ``opencode.py``) dominates a
call that never needed tools in the first place.

Scope (bu-qvnce.12 slice 1): text-only, tool-free invocation. ``mcp_servers``
must be empty — this adapter intentionally does not bridge MCP tool wiring
into the Anthropic tool-use protocol; that is out of scope here (a future
structured tool-use fast lane, if built, would pass tool schemas through a
mechanism of its own rather than through ``mcp_servers``). Passing a non-empty
``mcp_servers`` dict raises ``RuntimeError`` — fail loud rather than silently
dropping tool access a caller thinks it has.

Credential resolution mirrors ``ClaudeCodeAdapter`` exactly: the caller's
``env`` dict wins if it already carries a non-empty ``ANTHROPIC_API_KEY``;
otherwise the adapter resolves the key from the injected ``CredentialStore``
under the same ``"cli-auth/claude"`` path used by the CLI auth dashboard flow,
falling back to ``os.environ["ANTHROPIC_API_KEY"]`` for dev/test convenience.

Token-bucket contract (see ``base.RuntimeAdapter.invoke``): Anthropic's
``usage.input_tokens`` already excludes cache reads/writes (unlike OpenAI's
folded ``prompt_tokens``), so the mapping onto the shared contract is 1:1 —
no subtraction needed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# Default timeout for a single Messages API call.
_DEFAULT_TIMEOUT_SECONDS = 60

# max_tokens for the completion. Discretion/classification responses are
# short (a verdict word/line or a tool-call payload); this is generous
# headroom without inviting runaway generations.
_DEFAULT_MAX_TOKENS = 1024

_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"


def _extract_text(content_blocks: list[Any]) -> str | None:
    """Join text blocks from an Anthropic Messages response's content list."""
    parts = [block.text for block in content_blocks if getattr(block, "type", None) == "text"]
    return "\n".join(parts) if parts else None


def _extract_tool_calls(content_blocks: list[Any]) -> list[dict[str, Any]]:
    """Extract tool_use blocks from an Anthropic Messages response's content list.

    Present for forward-compatibility with a future tool-use fast lane; this
    adapter never sends ``tools`` today so the model has nothing to invoke,
    but a defensively-empty implementation here would hide a real response
    shape change, so it stays a faithful (currently-dead) mapping.
    """
    return [
        {"id": block.id, "name": block.name, "input": block.input}
        for block in content_blocks
        if getattr(block, "type", None) == "tool_use"
    ]


def _extract_usage(usage: Any) -> dict[str, Any] | None:
    """Map an Anthropic ``Usage`` object onto the shared token-bucket contract."""
    if usage is None:
        return None
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


class ApiAdapter(RuntimeAdapter):
    """Direct Anthropic Messages API adapter — no subprocess, no MCP bootstrap.

    Parameters
    ----------
    butler_name:
        Identity label for logging; not sent to the API.
    credential_store:
        Optional :class:`~butlers.credential_store.CredentialStore` used to
        resolve ``ANTHROPIC_API_KEY`` from the ``"cli-auth/claude"`` secret
        path when the caller-provided ``env`` does not already carry one.
    client:
        Optional pre-built Anthropic client — primarily a test seam; when
        omitted the adapter lazily constructs and caches an
        ``anthropic.AsyncAnthropic`` instance per resolved API key.
    """

    def __init__(
        self,
        *,
        butler_name: str | None = None,
        credential_store: CredentialStore | None = None,
        client: Any | None = None,
    ) -> None:
        self._butler_name = butler_name
        self._credential_store = credential_store
        self._client_override = client
        self._client: Any | None = None
        self._client_api_key: str | None = None
        self._last_process_info: dict[str, Any] | None = None

    @property
    def binary_name(self) -> str:
        """No CLI binary is invoked; returned for ABC/diagnostic compatibility."""
        return "anthropic-api"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return self._last_process_info

    async def _resolve_api_key(self, env: dict[str, str]) -> str | None:
        """Resolve the Anthropic API key, mirroring ``ClaudeCodeAdapter``'s order.

        1. Caller-provided ``env[ANTHROPIC_API_KEY]`` (non-empty, non-whitespace).
        2. ``CredentialStore.load("cli-auth/claude")`` when a store is injected.
        3. ``os.environ["ANTHROPIC_API_KEY"]`` (dev/test convenience).
        """
        caller_value = env.get(_ANTHROPIC_KEY, "").strip()
        if caller_value:
            return caller_value
        if self._credential_store is not None:
            try:
                stored = await self._credential_store.load("cli-auth/claude")
            except Exception:
                logger.debug(
                    "ApiAdapter: failed to resolve %s from credential store",
                    _ANTHROPIC_KEY,
                    exc_info=True,
                )
                stored = None
            if stored:
                return stored
        return os.environ.get(_ANTHROPIC_KEY) or None

    def _get_client(self, api_key: str) -> Any:
        """Return a cached ``AsyncAnthropic`` client, rebuilding on key change."""
        if self._client_override is not None:
            return self._client_override
        if self._client is None or self._client_api_key != api_key:
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
            self._client_api_key = api_key
        return self._client

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
        if mcp_servers:
            raise RuntimeError(
                "ApiAdapter does not support MCP tool wiring (mcp_servers must be "
                "empty). Use a subprocess adapter (claude/codex/gemini/opencode) "
                "for tool-using sessions."
            )
        if not model:
            raise ValueError("ApiAdapter.invoke() requires an explicit model id")

        effective_timeout = _DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
        api_key = await self._resolve_api_key(env)
        if not api_key:
            self._last_process_info = {
                "pid": None,
                "exit_code": -1,
                "command": f"api:{model}",
                "stderr": "",
                "runtime_type": "api",
                "error_detail": "No Anthropic API key available (checked env, "
                "credential store 'cli-auth/claude', ANTHROPIC_API_KEY env var)",
                "is_pre_tool_call": True,
            }
            raise RuntimeError(
                "ApiAdapter: no Anthropic API key available (checked env, "
                "credential store 'cli-auth/claude', ANTHROPIC_API_KEY env var)"
            )

        client = self._get_client(api_key)
        cmd_for_log = f"api:{model}"

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=_DEFAULT_MAX_TOKENS,
                    system=system_prompt or anthropic.NOT_GIVEN,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=effective_timeout,
            )
        except TimeoutError:
            logger.error("ApiAdapter invocation timed out after %ds", effective_timeout)
            self._last_process_info = {
                "pid": None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": "(timeout)",
                "runtime_type": "api",
                "is_pre_tool_call": True,
            }
            raise TimeoutError(
                f"ApiAdapter invocation timed out after {effective_timeout} seconds"
            ) from None
        except Exception as exc:
            logger.error("ApiAdapter invocation failed: %s", exc, exc_info=True)
            self._last_process_info = {
                "pid": None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": str(exc),
                "runtime_type": "api",
                "error_detail": str(exc),
                "is_pre_tool_call": True,
            }
            raise RuntimeError(f"ApiAdapter invocation failed: {exc}") from exc

        self._last_process_info = {
            "pid": None,
            "exit_code": 0,
            "command": cmd_for_log,
            "stderr": "",
            "runtime_type": "api",
        }

        result_text = _extract_text(response.content)
        tool_calls = _extract_tool_calls(response.content)
        usage = _extract_usage(getattr(response, "usage", None))
        return result_text, tool_calls, usage

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        """No MCP bootstrap: writes an empty placeholder and refuses non-empty input."""
        if mcp_servers:
            raise RuntimeError(
                "ApiAdapter does not support MCP tool wiring (mcp_servers must be empty)."
            )
        path = tmp_dir / "api-adapter-noop.json"
        path.write_text("{}")
        return path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Reuse the CLAUDE.md convention; empty string when absent."""
        claude_md = config_dir / "CLAUDE.md"
        if claude_md.exists():
            return claude_md.read_text()
        return ""

    async def reset(self) -> None:
        """Drop the cached SDK client so the next call rebuilds it from scratch."""
        self._client = None
        self._client_api_key = None


register_adapter("api", ApiAdapter)
