"""CodexAdapter — RuntimeAdapter implementation for OpenAI Codex CLI.

Encapsulates all Codex CLI-specific logic:
- Subprocess invocation of the ``codex`` binary
- MCP config file generation (JSON format with mcpServers key)
- AGENTS.md system prompt reading (Codex convention)
- Result parsing: extracts text output and tool call records

The Codex CLI is invoked in ``--full-auto`` approval mode with
``--quiet`` to suppress interactive UI. The system prompt (from
AGENTS.md) is passed via ``--instructions`` flag. MCP server configs
are written to a temporary config file pointed to by ``--config``.

If the Codex CLI binary is not installed on PATH, invoke() raises
FileNotFoundError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default timeout for Codex CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300


def _find_codex_binary() -> str:
    """Locate the codex binary on PATH.

    Returns
    -------
    str
        Absolute path to the codex binary.

    Raises
    ------
    FileNotFoundError
        If the codex binary is not found on PATH.
    """
    path = shutil.which("codex")
    if path is None:
        raise FileNotFoundError(
            "Codex CLI binary not found on PATH. Install it with: npm install -g @openai/codex"
        )
    return path


def _parse_codex_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse Codex CLI output into (result_text, tool_calls).

    The Codex CLI in quiet mode outputs JSON-lines to stdout. Each line
    may be a JSON object with a ``type`` field. We look for:
    - ``type: "message"`` — contains the assistant's text response
    - ``type: "tool_use"`` or ``type: "function_call"`` — tool invocations

    If the output is not valid JSON-lines, we treat the entire stdout
    as plain text result.

    Parameters
    ----------
    stdout:
        Raw stdout from the Codex process.
    stderr:
        Raw stderr from the Codex process.
    returncode:
        Exit code from the Codex process.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]]]
        (result_text, tool_calls)
    """
    if returncode != 0:
        error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
        return (f"Error: {error_detail}", [])

    result_text: str | None = None
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []

    # Try to parse as JSON-lines (one JSON object per line)
    lines = stdout.strip().splitlines()
    parsed_any_json = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Not JSON — accumulate as plain text
            text_parts.append(line)
            continue

        if not isinstance(obj, dict):
            text_parts.append(line)
            continue

        parsed_any_json = True
        obj_type = obj.get("type", "")

        if obj_type == "message":
            # Extract text content from message objects
            content = obj.get("content", "")
            if isinstance(content, str) and content:
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") in ("tool_use", "function_call"):
                            tool_calls.append(_extract_tool_call(block))

        elif obj_type in ("tool_use", "function_call"):
            tool_calls.append(_extract_tool_call(obj))

        elif obj_type == "result":
            # Final result object
            result_content = obj.get("result", obj.get("text", ""))
            if result_content:
                text_parts.append(str(result_content))

        else:
            # Unknown type — check for text or content fields
            if "text" in obj:
                text_parts.append(str(obj["text"]))
            elif "content" in obj and isinstance(obj["content"], str):
                text_parts.append(obj["content"])

    # If we couldn't parse any JSON, treat entire stdout as result text
    if not parsed_any_json and not text_parts:
        text_parts = [stdout.strip()] if stdout.strip() else []

    result_text = "\n".join(text_parts) if text_parts else None
    return result_text, tool_calls


def _extract_tool_call(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized tool call dict from a Codex JSON object.

    Parameters
    ----------
    obj:
        A JSON object representing a tool call.

    Returns
    -------
    dict[str, Any]
        Normalized tool call with 'id', 'name', and 'input' keys.
    """
    return {
        "id": obj.get("id", ""),
        "name": obj.get("name", obj.get("function", {}).get("name", "")),
        "input": obj.get(
            "input",
            obj.get("arguments", obj.get("function", {}).get("arguments", {})),
        ),
    }


class CodexAdapter(RuntimeAdapter):
    """Runtime adapter for the OpenAI Codex CLI.

    Invokes the Codex CLI binary via subprocess. The adapter handles:
    - Locating the ``codex`` binary on PATH
    - Passing system prompts via ``--instructions`` flag
    - Writing MCP config in Codex-compatible JSON format
    - Parsing CLI output into (result_text, tool_calls)

    Parameters
    ----------
    codex_binary:
        Path to the codex binary. If None, will be auto-detected on PATH
        at invocation time.
    """

    def __init__(self, codex_binary: str | None = None) -> None:
        self._codex_binary = codex_binary

    def create_worker(self) -> RuntimeAdapter:
        """Create an independent adapter for a pooled spawner worker."""
        return CodexAdapter(codex_binary=self._codex_binary)

    @property
    def binary_name(self) -> str:
        return "codex"

    def _get_binary(self) -> str:
        """Get the codex binary path, auto-detecting if needed."""
        if self._codex_binary is not None:
            return self._codex_binary
        return _find_codex_binary()

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
        """Invoke the Codex CLI with the given prompt and configuration.

        Builds the command line, passes the system prompt via
        ``--instructions``, writes MCP config if servers are provided,
        and parses the output.

        Parameters
        ----------
        prompt:
            The user prompt to send to Codex.
        system_prompt:
            System-level instructions (from AGENTS.md).
        mcp_servers:
            MCP server configurations for the butler.
        env:
            Filtered environment variables for the subprocess.
        cwd:
            Working directory for the Codex process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Usage is always
            None for the Codex adapter (no token reporting).

        Raises
        ------
        FileNotFoundError
            If the codex binary is not found on PATH.
        TimeoutError
            If the Codex process exceeds the timeout.
        """
        binary = self._get_binary()
        effective_timeout = timeout or _DEFAULT_TIMEOUT_SECONDS

        # Build command
        cmd = [
            binary,
            "--full-auto",
            "--quiet",
        ]

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        # Pass system prompt via --instructions flag
        if system_prompt:
            cmd.extend(["--instructions", system_prompt])

        # Add the prompt as the final positional argument
        cmd.append(prompt)

        logger.debug("Invoking Codex CLI: %s", " ".join(cmd[:3]) + " ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env if env else None,
                cwd=str(cwd) if cwd else None,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if stderr:
                logger.debug("Codex stderr: %s", stderr[:500])

            returncode = proc.returncode or 0
            if returncode != 0:
                error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
                logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
                raise RuntimeError(f"Codex CLI exited with code {returncode}: {error_detail}")

            result_text, tool_calls = _parse_codex_output(stdout, stderr, returncode)
            return result_text, tool_calls, None

        except TimeoutError:
            logger.error("Codex CLI timed out after %ds", effective_timeout)
            if proc:
                proc.kill()
                await proc.wait()
            raise TimeoutError(f"Codex CLI timed out after {effective_timeout} seconds") from None

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write MCP config in Codex-compatible JSON format.

        Codex uses a similar JSON config format with an ``mcpServers``
        key. The config file is written as ``codex.json`` in the
        temporary directory.

        Parameters
        ----------
        mcp_servers:
            Dict mapping server name to config (must include 'url' key).
        tmp_dir:
            Temporary directory to write the config file into.

        Returns
        -------
        Path
            Path to the generated codex.json file.
        """
        config = {"mcpServers": mcp_servers}
        config_path = tmp_dir / "codex.json"
        config_path.write_text(json.dumps(config, indent=2))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read AGENTS.md from the butler's config directory.

        Codex uses AGENTS.md as its system prompt file (as opposed to
        CLAUDE.md used by Claude Code). Returns the file contents, or
        an empty string if the file is missing or empty.

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

        if not agents_md.exists():
            return ""

        content = agents_md.read_text().strip()
        return content


# Register the real Codex adapter (replaces the stub in base.py)
register_adapter("codex", CodexAdapter)
