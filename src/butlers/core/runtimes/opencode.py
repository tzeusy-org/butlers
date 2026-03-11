"""OpenCodeAdapter — RuntimeAdapter implementation for OpenCode CLI.

Encapsulates all OpenCode CLI-specific logic:
- Subprocess invocation of the ``opencode`` binary
- Temporary config file generation (JSONC format with mcp/instructions/permission keys)
- OPENCODE.md / AGENTS.md system prompt reading
- Result parsing: extracts text output, tool call records, and token usage

The OpenCode CLI is invoked via ``opencode run --format json``. Config is passed
via the ``OPENCODE_CONFIG`` environment variable pointing to a temporary JSONC file
written per invocation. MCP servers are mapped as ``remote`` type entries. The
system prompt is written to a temp file and referenced in the ``instructions`` array.

Permission prompts are disabled by setting ``"permission": {}`` in the config.

If the OpenCode CLI binary is not installed on PATH, invoke() raises
FileNotFoundError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default timeout for OpenCode CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300


def _find_opencode_binary() -> str:
    """Locate the opencode binary on PATH.

    Returns
    -------
    str
        Absolute path to the opencode binary.

    Raises
    ------
    FileNotFoundError
        If the opencode binary is not found on PATH.
    """
    path = shutil.which("opencode")
    if path is None:
        raise FileNotFoundError(
            "OpenCode CLI binary not found on PATH. "
            "Install it with: npm install -g opencode-ai "
            "or see https://opencode.ai/docs"
        )
    return path


def _parse_opencode_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Parse OpenCode CLI JSON output into (result_text, tool_calls, usage).

    OpenCode CLI emits JSON-lines on stdout when invoked with ``--format json``.

    **OpenCode v1.2+ envelope format** (primary):
    Every event is wrapped in ``{type, timestamp, sessionID, part: {...}}``.
    The ``part`` object contains the actual payload:

    - ``type: "text"`` → ``part.text`` contains the response text
    - ``type: "tool_use"`` → ``part.tool`` (name), ``part.callID`` (id),
      ``part.state.input`` (arguments)
    - ``type: "step_finish"`` → ``part.tokens.{input, output}`` for usage
    - ``type: "step_start"`` → skipped (no useful data)

    **Legacy format** (fallback for future compatibility):
    Non-envelope events are handled as before for backward compatibility:

    - ``type: "text"`` — plain text delta
    - ``type: "message"`` / ``type: "assistant"`` — assistant messages
    - ``type: "result"`` — final result payload
    - ``type: "tool_use"`` / ``type: "tool_call"`` / ``type: "function_call"`` — tool calls
    - ``type: "usage"`` / ``type: "turn.completed"`` / ``type: "response.completed"`` — usage
    - ``type: "item.completed"`` / ``type: "response.output_item.done"`` — nested items

    Unknown event types are logged at DEBUG level and skipped.

    Parameters
    ----------
    stdout:
        Raw stdout from the OpenCode process.
    stderr:
        Raw stderr from the OpenCode process.
    returncode:
        Exit code from the OpenCode process.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
        (result_text, tool_calls, usage) where:
        - result_text is the concatenated assistant response text, or None
        - tool_calls is a list of normalized {id, name, input} dicts
        - usage is {input_tokens, output_tokens} or None when unavailable
    """
    if returncode != 0:
        error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        logger.error("OpenCode CLI exited with code %d: %s", returncode, error_detail)
        return (f"Error: {error_detail}", [], None)

    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []
    usage: dict[str, Any] | None = None

    # Try to parse as JSON-lines (one JSON object per line)
    lines = stdout.strip().splitlines()
    parsed_any_json = False
    fallback_text_parts: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Not JSON — accumulate for possible plain-text fallback
            if not parsed_any_json:
                fallback_text_parts.append(line)
            continue

        if not isinstance(obj, dict):
            if not parsed_any_json:
                fallback_text_parts.append(line)
            continue

        parsed_any_json = True
        obj_type = obj.get("type", "")

        # -----------------------------------------------------------
        # OpenCode v1.2+ envelope detection
        # All events are wrapped: {type, timestamp, sessionID, part: {...}}
        # -----------------------------------------------------------
        part = obj.get("part")
        is_envelope = isinstance(part, dict) and "sessionID" in obj

        if is_envelope:
            if obj_type == "text":
                text_val = part.get("text")
                if isinstance(text_val, str) and text_val:
                    text_parts.append(text_val)

            elif obj_type == "tool_use":
                tool_calls.append(_extract_envelope_tool_call(part))

            elif obj_type in ("step_finish", "step-finish"):
                tokens = part.get("tokens")
                if isinstance(tokens, dict):
                    step_in = tokens.get("input")
                    step_out = tokens.get("output")
                    if isinstance(step_in, int) or isinstance(step_out, int):
                        if usage is None:
                            usage = {"input_tokens": 0, "output_tokens": 0}
                        if isinstance(step_in, int):
                            usage["input_tokens"] += step_in
                        if isinstance(step_out, int):
                            usage["output_tokens"] += step_out

            elif obj_type in ("step_start", "step-start"):
                pass  # No useful data

            else:
                # Unknown envelope event — try to harvest text from part
                if obj_type:
                    logger.debug(
                        "OpenCode: unknown envelope event type %r — skipping",
                        obj_type,
                    )
                text_val = part.get("text")
                if isinstance(text_val, str) and text_val:
                    text_parts.append(text_val)

            continue  # Envelope handled — skip legacy path

        # -----------------------------------------------------------
        # Legacy / non-envelope format (backward compatibility)
        # -----------------------------------------------------------

        if obj_type == "text":
            # Plain text delta — OpenCode may emit incremental text events
            text_val = (
                obj.get("text")
                or obj.get("content")
                or obj.get("value")
                or obj.get("delta")
            )
            if isinstance(text_val, str) and text_val:
                text_parts.append(text_val)

        elif obj_type == "message":
            # Standard message event with text or multi-part content blocks
            content = obj.get("content", "")
            if isinstance(content, str) and content:
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "text":
                            text_val = block.get("text", "")
                            if text_val:
                                text_parts.append(text_val)
                        elif _looks_like_tool_call_event(block):
                            tool_calls.append(
                                _extract_opencode_tool_call(block)
                            )

        elif obj_type == "assistant":
            # Assistant response wrapper — may contain message or content
            inner = obj.get("message") or obj.get("response")
            if isinstance(inner, dict):
                content = inner.get("content", "")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type == "text":
                                text_val = block.get("text", "")
                                if text_val:
                                    text_parts.append(text_val)
                            elif _looks_like_tool_call_event(block):
                                tool_calls.append(
                                    _extract_opencode_tool_call(block)
                                )
            else:
                content = obj.get("content", "")
                if isinstance(content, str) and content:
                    text_parts.append(content)

        elif obj_type == "result":
            # Final result object — plain result or text field
            result_content = obj.get("result") or obj.get("text")
            if result_content:
                text_parts.append(str(result_content))

        elif _looks_like_tool_call_event(obj):
            tool_calls.append(_extract_opencode_tool_call(obj))

        elif obj_type in (
            "item.completed",
            "item.started",
            "response.output_item.done",
            "response.output_item.added",
        ):
            # Wrapper events with nested item payloads
            item = obj.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type in ("agent_message", "text"):
                text_val = item.get("text") or item.get("content", "")
                if isinstance(text_val, str) and text_val:
                    text_parts.append(text_val)
            elif _looks_like_tool_call_event(item):
                tool_calls.append(_extract_opencode_tool_call(item))

        elif obj_type == "usage":
            # Standalone usage event
            usage = _extract_usage(obj)

        elif obj_type in ("turn.completed", "response.completed"):
            # Final usage wrapper
            extracted = _extract_usage(obj)
            if extracted is not None:
                usage = extracted
            else:
                response_obj = obj.get("response")
                if isinstance(response_obj, dict):
                    extracted = _extract_usage(
                        response_obj.get("usage") or response_obj
                    )
                    if extracted is not None:
                        usage = extracted

        else:
            # Unknown event type — log and skip, but harvest text/content
            if obj_type:
                logger.debug(
                    "OpenCode: unknown event type %r — skipping", obj_type
                )
            if "text" in obj and isinstance(obj["text"], str):
                text_parts.append(obj["text"])
            elif "content" in obj and isinstance(obj["content"], str):
                text_parts.append(obj["content"])

    # If we couldn't parse any JSON, treat entire stdout as result text
    if not parsed_any_json and not text_parts:
        text_parts = fallback_text_parts or (
            [stdout.strip()] if stdout.strip() else []
        )

    result_text = "\n".join(part for part in text_parts if part) or None
    return result_text, tool_calls, usage


def _extract_envelope_tool_call(part: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized tool call from an OpenCode v1.2+ envelope ``part``.

    OpenCode v1.2+ wraps tool calls in an envelope where the ``part`` object
    contains:

    .. code-block:: json

        {
            "type": "tool",
            "callID": "call_...",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "ls -1", "workdir": "/tmp"},
                "output": "file.txt\\n",
                "metadata": {...},
                "time": {...}
            }
        }

    Parameters
    ----------
    part:
        The ``part`` dict from an OpenCode envelope event with ``type: "tool_use"``.

    Returns
    -------
    dict[str, Any]
        Normalized tool call with 'id', 'name', and 'input' keys.
    """
    state = part.get("state")
    input_payload: Any = {}
    if isinstance(state, dict):
        inp = state.get("input")
        if isinstance(inp, dict):
            input_payload = inp
        elif isinstance(inp, str):
            # Try to parse stringified JSON
            try:
                parsed = json.loads(inp)
                input_payload = parsed if isinstance(parsed, dict) else inp
            except (json.JSONDecodeError, ValueError):
                input_payload = inp

    tool_id = part.get("callID") or part.get("id") or ""
    tool_name = part.get("tool") or part.get("name") or ""

    return {
        "id": tool_id if isinstance(tool_id, str) else "",
        "name": tool_name if isinstance(tool_name, str) else "",
        "input": input_payload,
    }


def _extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Extract token usage from a usage dict or event object.

    Looks for ``input_tokens`` and ``output_tokens`` keys in the given object.
    Also accepts ``prompt_tokens`` / ``completion_tokens`` (OpenAI format).

    Parameters
    ----------
    obj:
        An event or usage sub-object from OpenCode output.

    Returns
    -------
    dict[str, Any] | None
        A dict with ``input_tokens`` and ``output_tokens`` (int or None),
        or None if no recognizable usage fields are found.
    """
    if not isinstance(obj, dict):
        return None

    # Primary: input_tokens / output_tokens (Anthropic / OpenCode native)
    input_tokens = obj.get("input_tokens") or obj.get("prompt_tokens")
    output_tokens = obj.get("output_tokens") or obj.get("completion_tokens")

    # Also check a nested "usage" key (e.g. turn.completed with usage={...})
    if input_tokens is None and output_tokens is None:
        nested = obj.get("usage")
        if isinstance(nested, dict):
            input_tokens = nested.get("input_tokens") or nested.get("prompt_tokens")
            output_tokens = nested.get("output_tokens") or nested.get("completion_tokens")

    if input_tokens is None and output_tokens is None:
        return None

    return {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
        "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
    }


def _looks_like_tool_call_event(obj: dict[str, Any]) -> bool:
    """Return True when an event object appears to encode a tool call.

    Uses heuristic matching: checks known tool-call type strings first,
    then falls back to checking for a name field + input/arguments fields.

    Parameters
    ----------
    obj:
        A JSON event object to inspect.

    Returns
    -------
    bool
        True if the object appears to be a tool call.
    """
    obj_type = str(obj.get("type", ""))
    if obj_type in {
        "tool_use",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "mcp_tool_use",
        "custom_tool_call",
        "command_execution",
    }:
        return True

    # Heuristic: look for nested call containers
    nested_containers = [
        container
        for container in (
            obj.get("function"),
            obj.get("tool"),
            obj.get("call"),
            obj.get("tool_call"),
            obj.get("toolCall"),
        )
        if isinstance(container, dict)
    ]

    name = (
        obj.get("name")
        or obj.get("tool_name")
        or obj.get("toolName")
        or next(
            (
                container.get("name") or container.get("tool_name") or container.get("toolName")
                for container in nested_containers
                if (
                    container.get("name") or container.get("tool_name") or container.get("toolName")
                )
            ),
            None,
        )
    )
    has_args = any(
        any(key in container for key in ("input", "arguments", "args", "parameters"))
        for container in [obj, *nested_containers]
    )
    if isinstance(name, str) and name.strip() and has_args:
        return True

    return False


def _extract_opencode_tool_call(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized tool call dict from an OpenCode JSON event.

    Handles multiple event shapes:
    - Standard tool_use: ``{"id": ..., "name": ..., "input": {...}}``
    - Function call: ``{"id": ..., "function": {"name": ..., "arguments": {...}}}``
    - MCP tool call with nested call: ``{"id": ..., "call": {"name": ..., "arguments": {...}}}``
    - toolCall camelCase variant
    - Stringified JSON arguments (parsed into dict when possible)

    Parameters
    ----------
    obj:
        A JSON object representing a tool call event.

    Returns
    -------
    dict[str, Any]
        Normalized tool call with 'id', 'name', and 'input' keys.
    """
    # Collect nested call container candidates
    nested_containers = [
        container
        for container in (
            obj.get("function"),
            obj.get("tool"),
            obj.get("call"),
            obj.get("tool_call"),
            obj.get("toolCall"),
        )
        if isinstance(container, dict)
    ]

    # Resolve tool name — check top-level and nested containers
    tool_name = (
        obj.get("name")
        or obj.get("tool_name")
        or obj.get("toolName")
        or next(
            (
                container.get("name") or container.get("tool_name") or container.get("toolName")
                for container in nested_containers
                if (
                    container.get("name") or container.get("tool_name") or container.get("toolName")
                )
            ),
            "",
        )
    )

    # Resolve tool ID — check top-level and nested containers
    tool_id = (
        obj.get("id")
        or obj.get("call_id")
        or next(
            (
                container.get("id") or container.get("call_id")
                for container in nested_containers
                if container.get("id") or container.get("call_id")
            ),
            None,
        )
    )

    # Resolve input payload — check top-level then nested containers
    input_payload: Any = obj.get("input")
    if input_payload is None:
        input_payload = obj.get("args")
    if input_payload is None:
        input_payload = obj.get("arguments")
    if input_payload is None:
        input_payload = obj.get("parameters")
    if input_payload is None:
        for container in nested_containers:
            for key in ("input", "args", "arguments", "parameters"):
                if key in container:
                    input_payload = container[key]
                    break
            if input_payload is not None:
                break
    if input_payload is None:
        input_payload = {}

    # Parse stringified JSON arguments
    if isinstance(input_payload, str):
        try:
            parsed = json.loads(input_payload)
            input_payload = parsed if isinstance(parsed, dict) else input_payload
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "id": tool_id if isinstance(tool_id, str) else "",
        "name": tool_name if isinstance(tool_name, str) else "",
        "input": input_payload,
    }


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
    instructions_path: Path | None = None,
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
    instructions_path:
        Optional path to a system prompt file to include in the
        ``instructions`` array. The absolute path string is used directly.

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

    if instructions_path is not None:
        config["instructions"] = [str(instructions_path)]

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
        self._last_process_info: dict[str, Any] | None = None

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        """Process-level metadata from the most recent invoke() call."""
        return self._last_process_info

    def create_worker(self) -> RuntimeAdapter:
        """Create an independent adapter for a pooled spawner worker."""
        return OpenCodeAdapter(opencode_binary=self._opencode_binary)

    @property
    def binary_name(self) -> str:
        return "opencode"

    def _get_binary(self) -> str:
        """Get the opencode binary path, auto-detecting if needed."""
        if self._opencode_binary is not None:
            return self._opencode_binary
        return _find_opencode_binary()

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

        Builds the command line for ``opencode run --format json``, writes a
        temporary JSONC config with MCP servers and system prompt instructions,
        passes the config path via ``OPENCODE_CONFIG`` env var, and parses the
        JSON-line output events.

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
            Maximum number of turns (not used by OpenCode CLI directly).
        model:
            Model to use in provider/model format
            (e.g. ``anthropic/claude-sonnet-4-5``).
        runtime_args:
            Optional additional CLI arguments appended before the prompt.
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
        FileNotFoundError
            If the opencode binary is not found on PATH.
        TimeoutError
            If the OpenCode process exceeds the timeout.
        RuntimeError
            If the OpenCode process exits with a non-zero exit code.
        """
        binary = self._get_binary()
        effective_timeout = timeout or _DEFAULT_TIMEOUT_SECONDS

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            # Write system prompt to a temp file if provided, then reference it
            # in the config's instructions array
            instructions_path: Path | None = None
            if system_prompt:
                instructions_path = tmp_dir / "_system_prompt.md"
                instructions_path.write_text(system_prompt)

            # Build OpenCode JSONC config with MCP servers and instructions
            config_path = build_config_file(
                mcp_servers=mcp_servers,
                tmp_dir=tmp_dir,
                instructions_path=instructions_path,
            )

            # Build command: opencode run --format json [--model <model>] [runtime_args] <prompt>
            cmd = [
                binary,
                "run",
                "--format",
                "json",
            ]

            if isinstance(model, str) and model.strip():
                cmd.extend(["--model", model.strip()])

            if runtime_args:
                cmd.extend(runtime_args)

            cmd.append(prompt)

            # Inject OPENCODE_CONFIG into subprocess env
            subprocess_env = dict(env) if env else {}
            subprocess_env["OPENCODE_CONFIG"] = str(config_path)

            cmd_for_log = " ".join(cmd[:4]) + " ..."
            logger.debug("Invoking OpenCode CLI: %s", cmd_for_log)

            proc: asyncio.subprocess.Process | None = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=subprocess_env,
                    cwd=str(cwd) if cwd else None,
                )

                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=effective_timeout,
                )

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")

                if stderr:
                    logger.debug("OpenCode stderr: %s", stderr[:500])

                returncode = proc.returncode if proc.returncode is not None else 0

                self._last_process_info = {
                    "pid": proc.pid,
                    "exit_code": returncode,
                    "command": cmd_for_log,
                    "stderr": stderr,
                    "runtime_type": "opencode",
                }

                if returncode != 0:
                    error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
                    logger.error("OpenCode CLI exited with code %d: %s", returncode, error_detail)
                    raise RuntimeError(
                        f"OpenCode CLI exited with code {returncode}: {error_detail}"
                    )

                result_text, tool_calls, usage = _parse_opencode_output(stdout, stderr, returncode)
                return result_text, tool_calls, usage

            except TimeoutError:
                logger.error("OpenCode CLI timed out after %ds", effective_timeout)
                self._last_process_info = {
                    "pid": proc.pid if proc is not None else None,
                    "exit_code": -1,
                    "command": cmd_for_log,
                    "stderr": "(timeout — process killed)",
                    "runtime_type": "opencode",
                }
                if proc is not None:
                    proc.kill()
                    await proc.wait()
                raise TimeoutError(
                    f"OpenCode CLI timed out after {effective_timeout} seconds"
                ) from None


# Register the OpenCode adapter
register_adapter("opencode", OpenCodeAdapter)
