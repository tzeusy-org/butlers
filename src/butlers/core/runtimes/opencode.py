"""OpenCodeAdapter — RuntimeAdapter implementation for OpenCode CLI.

Encapsulates all OpenCode CLI-specific logic:
- Subprocess invocation of the ``opencode`` binary
- MCP config file generation (JSONC format with mcp key)
- OPENCODE.md / AGENTS.md system prompt reading
- Result parsing: extracts text output and tool call records

The OpenCode CLI is invoked with ``opencode run --format json`` and
``--model <provider/model>`` to pass the model. System prompt is written
to a temp file and referenced in the config's ``instructions`` array.
MCP server configs are written to a temporary JSONC config file and
passed via the ``OPENCODE_CONFIG`` environment variable.

If the opencode binary is not installed on PATH, invoke() raises
FileNotFoundError.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default timeout for OpenCode CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300


def parse_system_prompt_file(config_dir: Path) -> str:
    """Read system prompt from the butler's config directory.

    OpenCode prefers OPENCODE.md as its system prompt file, falling back
    to AGENTS.md if OPENCODE.md is not present or empty. Returns the
    file contents, or an empty string if neither file exists.

    Parameters
    ----------
    config_dir:
        Path to the butler's config directory.

    Returns
    -------
    str
        The parsed system prompt text.
    """
    for filename in ("OPENCODE.md", "AGENTS.md"):
        prompt_file = config_dir / filename
        if prompt_file.exists():
            content = prompt_file.read_text().strip()
            if content:
                return content
    return ""


def build_config_file(
    mcp_servers: dict[str, Any],
    tmp_dir: Path,
) -> Path:
    """Write MCP config in OpenCode-compatible JSONC format.

    OpenCode uses a JSONC config with an ``mcp`` section containing
    ``remote`` type server entries. Each server entry includes
    ``type``, ``url``, and ``enabled`` fields. The config file is
    written as ``opencode.jsonc`` in the temporary directory.

    Parameters
    ----------
    mcp_servers:
        Dict mapping server name to config (must include 'url' key).
    tmp_dir:
        Temporary directory to write the config file into.

    Returns
    -------
    Path
        Path to the generated opencode.jsonc file.
    """
    mcp_section: dict[str, Any] = {}
    for server_name, server_cfg in mcp_servers.items():
        if not isinstance(server_cfg, dict):
            logger.warning(
                "Skipping OpenCode MCP server %r: config must be a dict, got %r",
                server_name,
                type(server_cfg).__name__,
            )
            continue
        url = server_cfg.get("url")
        if not isinstance(url, str) or not url.strip():
            logger.warning(
                "Skipping OpenCode MCP server %r: missing or empty 'url' key",
                server_name,
            )
            continue
        mcp_section[server_name] = {
            "type": "remote",
            "url": url.strip(),
            "enabled": True,
        }

    config: dict[str, Any] = {
        "mcp": mcp_section,
        "permission": {},
    }

    config_path = tmp_dir / "opencode.jsonc"
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


class OpenCodeAdapter(RuntimeAdapter):
    """Runtime adapter for the OpenCode CLI.

    Invokes the OpenCode CLI binary via subprocess. The adapter handles:
    - Locating the ``opencode`` binary on PATH
    - Writing MCP config in OpenCode-compatible JSONC format
    - Reading system prompts from OPENCODE.md or AGENTS.md
    - Passing config via OPENCODE_CONFIG env var
    - Parsing CLI output into (result_text, tool_calls, usage)

    Parameters
    ----------
    opencode_binary:
        Path to the opencode binary. If None, will be auto-detected on PATH
        at invocation time.
    """

    def __init__(self, opencode_binary: str | None = None) -> None:
        self._opencode_binary = opencode_binary

    def create_worker(self) -> RuntimeAdapter:
        """Create an independent adapter for a pooled spawner worker."""
        return OpenCodeAdapter(opencode_binary=self._opencode_binary)

    @property
    def binary_name(self) -> str:
        return "opencode"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read system prompt from the butler's config directory.

        OpenCode prefers OPENCODE.md as its system prompt file, falling
        back to AGENTS.md if OPENCODE.md is not present or empty. Returns
        the file contents, or an empty string if neither file exists.

        Parameters
        ----------
        config_dir:
            Path to the butler's config directory.

        Returns
        -------
        str
            The parsed system prompt text.
        """
        return parse_system_prompt_file(config_dir)

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write MCP config in OpenCode-compatible JSONC format.

        OpenCode uses a JSONC config with an ``mcp`` section containing
        ``remote`` type server entries. The config file is written as
        ``opencode.jsonc`` in the temporary directory.

        Parameters
        ----------
        mcp_servers:
            Dict mapping server name to config (must include 'url' key).
        tmp_dir:
            Temporary directory to write the config file into.

        Returns
        -------
        Path
            Path to the generated opencode.jsonc file.
        """
        return build_config_file(mcp_servers, tmp_dir)

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
        """Invoke the OpenCode CLI with the given prompt and configuration.

        NOTE: This is a stub implementation. The full invoke() logic
        (subprocess launch, temp config, output parsing) is implemented
        in a separate task (butlers-n3fr.2).

        Parameters
        ----------
        prompt:
            The user prompt to send to OpenCode.
        system_prompt:
            System-level instructions (from OPENCODE.md or AGENTS.md).
        mcp_servers:
            MCP server configurations for the butler.
        env:
            Environment variables for the subprocess.
        max_turns:
            Maximum number of turns (not used by OpenCode CLI).
        model:
            Model to use in provider/model format (e.g. anthropic/claude-sonnet-4-5).
        runtime_args:
            Optional additional CLI arguments.
        cwd:
            Working directory for the OpenCode process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage).

        Raises
        ------
        NotImplementedError
            Full invoke() implementation is a separate task.
        """
        raise NotImplementedError(
            "OpenCodeAdapter.invoke() is not yet implemented. "
            "This is a stub — the full implementation is in butlers-n3fr.2."
        )


# Register the OpenCode adapter
register_adapter("opencode", OpenCodeAdapter)
