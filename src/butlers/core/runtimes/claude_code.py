"""ClaudeCodeAdapter — RuntimeAdapter implementation for Claude Agent SDK.

Encapsulates all Claude Agent SDK-specific logic:
- claude_agent_sdk imports (ClaudeAgentOptions, ResultMessage, ToolUseBlock,
  McpSSEServerConfig, query)
- MCP config file writing (JSON format with mcpServers key)
- SDK invocation (building ClaudeAgentOptions, calling query(),
  parsing ResultMessage/ToolUseBlock)
- CLAUDE.md reading logic
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, ToolUseBlock, query
from claude_agent_sdk.types import McpSSEServerConfig

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(RuntimeAdapter):
    """Runtime adapter for the Claude Agent SDK.

    Handles SDK invocation, MCP config file generation (JSON format),
    and CLAUDE.md system prompt reading.

    Parameters
    ----------
    sdk_query:
        Callable to use for the actual SDK invocation. Defaults to
        ``claude_agent_sdk.query``. Override in tests to inject a mock.
    butler_name:
        Name of the butler this adapter serves. Used to construct per-butler
        stderr log paths. Optional; when omitted stderr is not captured.
    log_root:
        Root directory for log files. When set, stderr from Claude Code CLI
        subprocesses is written to
        ``{log_root}/butlers/{butler_name}_cc_stderr.log``. When ``None``,
        stderr capture is disabled.
    """

    def __init__(
        self,
        sdk_query: Any = None,
        butler_name: str | None = None,
        log_root: Path | None = None,
    ) -> None:
        self._sdk_query = sdk_query or query
        self._butler_name = butler_name
        self._log_root = log_root

    @property
    def binary_name(self) -> str:
        return "claude"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Invoke Claude Agent SDK with the given prompt and configuration.

        Builds ClaudeAgentOptions with MCP server configs (as
        McpSSEServerConfig), calls query(), and parses
        ResultMessage/ToolUseBlock from the response stream.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Usage contains
            token counts extracted from the SDK's ResultMessage.
        """
        # Build MCP server config objects for SDK
        sdk_mcp_servers: dict[str, McpSSEServerConfig] = {}
        for name, server_cfg in mcp_servers.items():
            if isinstance(server_cfg, dict):
                sdk_mcp_servers[name] = McpSSEServerConfig(
                    type="sse",
                    url=server_cfg["url"],
                )
            else:
                # Already an McpSSEServerConfig or compatible object
                sdk_mcp_servers[name] = server_cfg

        # Open per-butler stderr log file for Claude Code CLI diagnostics
        stderr_file = None
        stderr_kwargs: dict[str, Any] = {}
        if self._butler_name and self._log_root is not None:
            try:
                stderr_dir = self._log_root / "butlers"
                stderr_dir.mkdir(parents=True, exist_ok=True)
                stderr_path = stderr_dir / f"{self._butler_name}_cc_stderr.log"
                stderr_file = open(stderr_path, "a", buffering=1)  # noqa: SIM115
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                stderr_file.write(f"\n--- runtime session start: {ts} ---\n")
                stderr_file.flush()
                stderr_kwargs = {
                    "debug_stderr": stderr_file,
                    "extra_args": {"debug-to-stderr": None},
                }
            except OSError:
                logger.warning(
                    "Could not open CC stderr log for %s", self._butler_name, exc_info=True
                )

        try:
            options = ClaudeAgentOptions(
                system_prompt=system_prompt,
                mcp_servers=sdk_mcp_servers,
                permission_mode="bypassPermissions",
                model=model,
                env=env,
                max_turns=max_turns,
                cwd=str(cwd) if cwd else None,
                **stderr_kwargs,
            )

            result_text: str | None = None
            tool_calls: list[dict[str, Any]] = []
            usage: dict[str, Any] | None = None

            async for message in self._sdk_query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    # Extract token usage from the ResultMessage
                    if message.usage:
                        usage = dict(message.usage)
                elif hasattr(message, "content"):
                    for block in getattr(message, "content", []):
                        if isinstance(block, ToolUseBlock):
                            tool_calls.append(
                                {
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                }
                            )

            return result_text, tool_calls, usage
        finally:
            if stderr_file is not None:
                try:
                    stderr_file.close()
                except OSError:
                    pass

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write MCP config as JSON file with mcpServers key.

        Parameters
        ----------
        mcp_servers:
            Dict mapping server name to config (must include 'url' key).
        tmp_dir:
            Temporary directory to write the config file into.

        Returns
        -------
        Path
            Path to the generated mcp.json file.
        """
        config = {"mcpServers": mcp_servers}
        mcp_json_path = tmp_dir / "mcp.json"
        mcp_json_path.write_text(json.dumps(config, indent=2))
        return mcp_json_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read CLAUDE.md from the butler's config directory.

        Returns the file contents, or an empty string if the file is missing
        or empty.

        Parameters
        ----------
        config_dir:
            Path to the butler's config directory.

        Returns
        -------
        str
            The parsed system prompt text.
        """
        claude_md = config_dir / "CLAUDE.md"

        if not claude_md.exists():
            return ""

        content = claude_md.read_text().strip()
        return content


# Register the real Claude Code adapter (replaces the stub in base.py)
register_adapter("claude-code", ClaudeCodeAdapter)
