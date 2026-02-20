"""GeminiAdapter — RuntimeAdapter implementation for Google Gemini CLI.

Encapsulates all Gemini CLI-specific logic:
- Subprocess invocation of the ``gemini`` binary
- MCP config file generation (JSON format with mcpServers key)
- GEMINI.md / AGENTS.md system prompt reading
- Result parsing: extracts text output and tool call records

The Gemini CLI is invoked with ``--prompt`` to pass the user prompt
and ``--sandbox=false`` to disable sandboxing. The system prompt
(from GEMINI.md or AGENTS.md) is passed inline via ``--system-prompt``.
MCP server configs are written to a temporary config file.

Environment filtering ensures GOOGLE_API_KEY is passed through
while ANTHROPIC_API_KEY is excluded.

If the Gemini CLI binary is not installed on PATH, invoke() raises
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

# Default timeout for Gemini CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300

# Keys that must NOT be passed to Gemini subprocess
_EXCLUDED_ENV_KEYS = frozenset({"ANTHROPIC_API_KEY"})


def _find_gemini_binary() -> str:
    """Locate the gemini binary on PATH.

    Returns
    -------
    str
        Absolute path to the gemini binary.

    Raises
    ------
    FileNotFoundError
        If the gemini binary is not found on PATH.
    """
    path = shutil.which("gemini")
    if path is None:
        raise FileNotFoundError(
            "Gemini CLI binary not found on PATH. "
            "Install it with: npm install -g @anthropic-ai/gemini-cli "
            "or see https://github.com/google-gemini/gemini-cli"
        )
    return path


def _filter_env(env: dict[str, str]) -> dict[str, str]:
    """Filter environment variables for Gemini subprocess.

    Ensures GOOGLE_API_KEY is present (if in source env) and
    ANTHROPIC_API_KEY is excluded.

    Parameters
    ----------
    env:
        Source environment variables.

    Returns
    -------
    dict[str, str]
        Filtered environment variables safe for Gemini.
    """
    return {k: v for k, v in env.items() if k not in _EXCLUDED_ENV_KEYS}


def _parse_gemini_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse Gemini CLI output into (result_text, tool_calls).

    The Gemini CLI may output JSON or plain text to stdout. We attempt
    to parse JSON-lines first:
    - ``type: "message"`` — contains the assistant's text response
    - ``type: "tool_use"`` or ``type: "functionCall"`` — tool invocations
    - ``type: "result"`` — final result

    If the output is not valid JSON, we treat the entire stdout as
    plain text result.

    Parameters
    ----------
    stdout:
        Raw stdout from the Gemini process.
    stderr:
        Raw stderr from the Gemini process.
    returncode:
        Exit code from the Gemini process.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]]]
        (result_text, tool_calls)
    """
    if returncode != 0:
        error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        logger.error("Gemini CLI exited with code %d: %s", returncode, error_detail)
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
                        elif block.get("type") in ("tool_use", "functionCall"):
                            tool_calls.append(_extract_tool_call(block))

        elif obj_type in ("tool_use", "functionCall"):
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
    """Extract a normalized tool call dict from a Gemini JSON object.

    Handles both standard ``tool_use`` format and Gemini's
    ``functionCall`` format.

    Parameters
    ----------
    obj:
        A JSON object representing a tool call.

    Returns
    -------
    dict[str, Any]
        Normalized tool call with 'id', 'name', and 'input' keys.
    """
    # Gemini functionCall format: {"functionCall": {"name": ..., "args": ...}}
    fn_call = obj.get("functionCall", {})
    if fn_call and isinstance(fn_call, dict):
        return {
            "id": obj.get("id", ""),
            "name": fn_call.get("name", ""),
            "input": fn_call.get("args", fn_call.get("arguments", {})),
        }

    # Standard tool_use format
    return {
        "id": obj.get("id", ""),
        "name": obj.get("name", ""),
        "input": obj.get("input", obj.get("args", obj.get("arguments", {}))),
    }


class GeminiAdapter(RuntimeAdapter):
    """Runtime adapter for the Google Gemini CLI.

    Invokes the Gemini CLI binary via subprocess. The adapter handles:
    - Locating the ``gemini`` binary on PATH
    - Passing system prompts via ``--system-prompt`` flag
    - Writing MCP config in Gemini-compatible JSON format
    - Filtering env vars (include GOOGLE_API_KEY, exclude ANTHROPIC_API_KEY)
    - Parsing CLI output into (result_text, tool_calls)

    Parameters
    ----------
    gemini_binary:
        Path to the gemini binary. If None, will be auto-detected on PATH
        at invocation time.
    """

    def __init__(self, gemini_binary: str | None = None) -> None:
        self._gemini_binary = gemini_binary

    def create_worker(self) -> RuntimeAdapter:
        """Create an independent adapter for a pooled spawner worker."""
        return GeminiAdapter(gemini_binary=self._gemini_binary)

    @property
    def binary_name(self) -> str:
        return "gemini"

    def _get_binary(self) -> str:
        """Get the gemini binary path, auto-detecting if needed."""
        if self._gemini_binary is not None:
            return self._gemini_binary
        return _find_gemini_binary()

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
        """Invoke the Gemini CLI with the given prompt and configuration.

        Builds the command line, passes the system prompt via
        ``--system-prompt``, writes MCP config if servers are provided,
        and parses the output.

        Environment variables are filtered to include GOOGLE_API_KEY
        and exclude ANTHROPIC_API_KEY.

        Parameters
        ----------
        prompt:
            The user prompt to send to Gemini.
        system_prompt:
            System-level instructions (from GEMINI.md or AGENTS.md).
        mcp_servers:
            MCP server configurations for the butler.
        env:
            Environment variables for the subprocess (will be filtered).
        cwd:
            Working directory for the Gemini process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Usage is always
            None for the Gemini adapter (no token reporting).

        Raises
        ------
        FileNotFoundError
            If the gemini binary is not found on PATH.
        TimeoutError
            If the Gemini process exceeds the timeout.
        """
        binary = self._get_binary()
        effective_timeout = timeout or _DEFAULT_TIMEOUT_SECONDS

        # Filter environment variables
        filtered_env = _filter_env(env) if env else None

        # Build command
        cmd = [
            binary,
            "--sandbox=false",
        ]

        # Pass system prompt via --system-prompt flag
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Pass prompt via --prompt flag
        cmd.extend(["--prompt", prompt])

        logger.debug("Invoking Gemini CLI: %s", " ".join(cmd[:3]) + " ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=filtered_env,
                cwd=str(cwd) if cwd else None,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if stderr:
                logger.debug("Gemini stderr: %s", stderr[:500])

            result_text, tool_calls = _parse_gemini_output(stdout, stderr, proc.returncode or 0)
            return result_text, tool_calls, None

        except TimeoutError:
            logger.error("Gemini CLI timed out after %ds", effective_timeout)
            if proc:
                proc.kill()
                await proc.wait()
            raise TimeoutError(f"Gemini CLI timed out after {effective_timeout} seconds") from None

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write MCP config in Gemini-compatible JSON format.

        Gemini uses a JSON config format with an ``mcpServers`` key,
        similar to other runtimes. The config file is written as
        ``gemini_mcp.json`` in the temporary directory.

        Parameters
        ----------
        mcp_servers:
            Dict mapping server name to config (must include 'url' key).
        tmp_dir:
            Temporary directory to write the config file into.

        Returns
        -------
        Path
            Path to the generated gemini_mcp.json file.
        """
        config = {"mcpServers": mcp_servers}
        config_path = tmp_dir / "gemini_mcp.json"
        config_path.write_text(json.dumps(config, indent=2))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read system prompt from the butler's config directory.

        Gemini prefers GEMINI.md as its system prompt file, falling back
        to AGENTS.md if GEMINI.md is not present. Returns the file
        contents, or an empty string if neither file exists.

        Parameters
        ----------
        config_dir:
            Path to the butler's config directory.

        Returns
        -------
        str
            The parsed system prompt text.
        """
        # Prefer GEMINI.md, fall back to AGENTS.md
        gemini_md = config_dir / "GEMINI.md"
        if gemini_md.exists():
            content = gemini_md.read_text().strip()
            if content:
                return content

        agents_md = config_dir / "AGENTS.md"
        if agents_md.exists():
            content = agents_md.read_text().strip()
            if content:
                return content

        return ""


# Register the real Gemini adapter (replaces the stub in base.py)
register_adapter("gemini", GeminiAdapter)
