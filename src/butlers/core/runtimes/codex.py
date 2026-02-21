"""CodexAdapter — RuntimeAdapter implementation for OpenAI Codex CLI.

Encapsulates all Codex CLI-specific logic:
- Subprocess invocation of the ``codex`` binary
- MCP config file generation (JSON format with mcpServers key)
- AGENTS.md system prompt reading (Codex convention)
- Result parsing: extracts text output and tool call records

The Codex CLI is invoked via ``codex exec --json --full-auto``. Since
current Codex CLI releases do not support a dedicated system prompt
flag, the butler ``system_prompt`` is prefixed into the initial
instructions payload sent to ``exec``.

If the Codex CLI binary is not installed on PATH, invoke() raises
FileNotFoundError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default timeout for Codex CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300
_SAFE_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _infer_mcp_transport_from_url(url: str) -> str | None:
    """Infer MCP transport from URL path conventions.

    Returns ``"streamable_http"`` for ``.../mcp`` URLs, ``"sse"`` for
    ``.../sse`` URLs, and ``None`` when no convention can be inferred.
    """
    parsed = urlparse(url)
    normalized_path = parsed.path.rstrip("/").lower()
    if normalized_path.endswith("/mcp"):
        return "streamable_http"
    if normalized_path.endswith("/sse"):
        return "sse"
    return None


def _looks_like_transport_failure(error_detail: str) -> bool:
    """Best-effort detection for MCP transport mismatch failures."""
    lowered = error_detail.lower()
    markers = (
        "rmcp startup failed",
        "streamable_http",
        "text/event-stream",
        "method not allowed",
        "unsupported media type",
        "transport",
    )
    return any(marker in lowered for marker in markers)


def _resolve_transport_details(
    server_cfg: dict[str, Any], url: str
) -> tuple[str | None, str | None]:
    """Return (explicit_transport, inferred_transport) for an MCP server."""
    explicit_transport = server_cfg.get("transport")
    normalized_transport = (
        explicit_transport.strip().lower()
        if isinstance(explicit_transport, str) and explicit_transport.strip()
        else None
    )
    inferred_transport = _infer_mcp_transport_from_url(url.strip())
    return normalized_transport, inferred_transport


def _is_safe_mcp_server_name(server_name: str) -> bool:
    """Accept only server names that are safe TOML bare keys."""
    return bool(_SAFE_MCP_SERVER_NAME_RE.fullmatch(server_name))


def _augment_transport_error_detail(error_detail: str, mcp_servers: dict[str, Any]) -> str:
    """Append actionable MCP transport diagnostics when mismatch is likely."""
    if not _looks_like_transport_failure(error_detail):
        return error_detail

    hints: list[str] = []
    for server_name, server_cfg in mcp_servers.items():
        if not isinstance(server_cfg, dict):
            continue
        url = server_cfg.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        normalized_transport, inferred_transport = _resolve_transport_details(server_cfg, url)

        if (
            normalized_transport
            and inferred_transport
            and normalized_transport != inferred_transport
        ):
            hints.append(
                f"{server_name} has transport={normalized_transport!r} but URL looks like "
                f"{inferred_transport!r} ({url.strip()!r})"
            )

        if inferred_transport == "sse" or normalized_transport == "sse":
            hints.append(
                f"{server_name} uses SSE endpoint {url.strip()!r}; Codex MCP expects streamable "
                "HTTP (for example .../mcp)"
            )

    if not hints:
        return error_detail

    unique_hints = list(dict.fromkeys(hints))
    return f"{error_detail} | MCP transport diagnostics: {'; '.join(unique_hints)}"


def _looks_like_tool_call_event(obj: dict[str, Any]) -> bool:
    """Return True when an event object appears to encode a tool call."""
    obj_type = str(obj.get("type", ""))
    if obj_type in {
        "command_execution",
        "tool_use",
        "function_call",
        "tool_call",
        "mcp_tool_call",
        "mcp_tool_use",
        "custom_tool_call",
    }:
        return True

    # Some Codex event variants omit specific type names or nest call payloads
    # under fields like "call" / "tool_call".
    nested_containers = [
        container
        for container in (
            obj.get("function"),
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
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Parse Codex CLI output into (result_text, tool_calls).

    The Codex CLI output may include JSON-lines on stdout. We support
    both legacy event objects and current ``codex exec --json`` events.
    We look for:
    - ``type: "message"`` or ``type: "result"`` (legacy text payloads)
    - ``type: "item.completed"`` + ``item.type: "agent_message"``
    - ``type: "tool_use"`` / ``type: "function_call"`` / command items
    - response/item wrapper events carrying nested tool-call items
    - ``type: "turn.completed"`` usage token counts

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
    tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
        (result_text, tool_calls, usage)
    """
    if returncode != 0:
        error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
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
            # Not JSON — only accumulate for pure plain-text output mode.
            # codex exec --json can emit occasional non-JSON diagnostics.
            if not parsed_any_json:
                fallback_text_parts.append(line)
            continue

        if not isinstance(obj, dict):
            if not parsed_any_json:
                fallback_text_parts.append(line)
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

        elif _looks_like_tool_call_event(obj):
            tool_calls.append(_extract_tool_call(obj))

        elif obj_type == "result":
            # Final result object
            result_content = obj.get("result", obj.get("text", ""))
            if result_content:
                text_parts.append(str(result_content))

        elif obj_type in (
            "item.completed",
            "item.started",
            "response.output_item.done",
            "response.output_item.added",
        ):
            item = obj.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            elif _looks_like_tool_call_event(item):
                tool_calls.append(_extract_tool_call(item))

        elif obj_type in ("turn.completed", "response.completed"):
            raw_usage = obj.get("usage")
            if not isinstance(raw_usage, dict):
                response_obj = obj.get("response")
                if isinstance(response_obj, dict):
                    usage_obj = response_obj.get("usage")
                    if isinstance(usage_obj, dict):
                        raw_usage = usage_obj
            if isinstance(raw_usage, dict):
                input_tokens = raw_usage.get("input_tokens")
                output_tokens = raw_usage.get("output_tokens")
                usage = {
                    "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
                    "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
                }

        else:
            # Unknown type — check for text or content fields
            if "text" in obj:
                text_parts.append(str(obj["text"]))
            elif "content" in obj and isinstance(obj["content"], str):
                text_parts.append(obj["content"])

    # If we couldn't parse any JSON, treat entire stdout as result text
    if not parsed_any_json and not text_parts:
        text_parts = fallback_text_parts or ([stdout.strip()] if stdout.strip() else [])

    result_text = "\n".join(text_parts) if text_parts else None
    return result_text, tool_calls, usage


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
    obj_type = obj.get("type", "")
    if obj_type == "command_execution":
        return {
            "id": obj.get("id", ""),
            "name": "command_execution",
            "input": {
                "command": obj.get("command", ""),
                "status": obj.get("status"),
                "exit_code": obj.get("exit_code"),
                "aggregated_output": obj.get("aggregated_output", ""),
            },
        }

    nested_containers = [
        container
        for container in (
            obj.get("function"),
            obj.get("call"),
            obj.get("tool_call"),
            obj.get("toolCall"),
        )
        if isinstance(container, dict)
    ]

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
                    input_payload = container.get(key)
                    break
            if input_payload is not None:
                break
    if input_payload is None:
        input_payload = {}

    if isinstance(input_payload, str):
        try:
            parsed_input = json.loads(input_payload)
            input_payload = parsed_input if isinstance(parsed_input, dict) else input_payload
        except (json.JSONDecodeError, ValueError):
            pass

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

    tool_id = obj.get("id")
    if not isinstance(tool_id, str) or not tool_id:
        tool_id = obj.get("call_id")
    if (not isinstance(tool_id, str) or not tool_id) and nested_containers:
        nested_id = next(
            (
                container.get("id") or container.get("call_id")
                for container in nested_containers
                if container.get("id") or container.get("call_id")
            ),
            "",
        )
        tool_id = nested_id

    return {
        "id": tool_id if isinstance(tool_id, str) else "",
        "name": tool_name if isinstance(tool_name, str) else "",
        "input": input_payload,
    }


class CodexAdapter(RuntimeAdapter):
    """Runtime adapter for the OpenAI Codex CLI.

    Invokes the Codex CLI binary via subprocess. The adapter handles:
    - Locating the ``codex`` binary on PATH
    - Running in non-interactive mode via ``codex exec --json``
    - Embedding system prompts into the initial instructions payload
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

    @staticmethod
    def _compose_exec_prompt(prompt: str, system_prompt: str) -> str:
        """Compose the initial prompt payload for ``codex exec``.

        Codex CLI no longer supports a dedicated system-prompt flag. We pass
        butler instructions as a prefixed section in the initial prompt.
        """
        if not system_prompt:
            return prompt
        return (
            "<system_instructions>\n"
            f"{system_prompt}\n"
            "</system_instructions>\n\n"
            "<user_prompt>\n"
            f"{prompt}\n"
            "</user_prompt>"
        )

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

        Builds the command line for ``codex exec --json --full-auto``,
        injects system instructions into the initial prompt payload, and
        parses JSON-line output events.

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
            A tuple of (result_text, tool_calls, usage). Usage is extracted
            from ``turn.completed`` events when available.

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
            "exec",
            "--json",
            "--full-auto",
        ]

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        for server_name, server_cfg in mcp_servers.items():
            if not isinstance(server_name, str) or not _is_safe_mcp_server_name(server_name):
                logger.warning(
                    "Skipping Codex MCP server with unsupported name %r; "
                    "allowed pattern is [A-Za-z0-9_-]+",
                    server_name,
                )
                continue
            if not isinstance(server_cfg, dict):
                continue
            url = server_cfg.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            # -c value must be TOML-parseable; quote and escape URL.
            escaped_url = url.strip().replace("\\", "\\\\").replace('"', '\\"')
            cmd.extend(["-c", f'mcp_servers.{server_name}.url="{escaped_url}"'])

            # When streamable HTTP is configured/inferred, pass an explicit
            # transport to avoid runtime defaults drifting across Codex versions.
            normalized_transport, inferred_transport = _resolve_transport_details(server_cfg, url)
            if normalized_transport == "streamable_http" or (
                normalized_transport is None and inferred_transport == "streamable_http"
            ):
                cmd.extend(["-c", f'mcp_servers.{server_name}.transport="streamable_http"'])

        # Delimit options from positional prompt so prompts that start with
        # '-'/'--' are never parsed as CLI flags by codex exec.
        cmd.append("--")
        cmd.append(self._compose_exec_prompt(prompt=prompt, system_prompt=system_prompt))

        logger.debug("Invoking Codex CLI: %s", " ".join(cmd[:4]) + " ...")

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
                error_detail = _augment_transport_error_detail(error_detail, mcp_servers)
                logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
                raise RuntimeError(f"Codex CLI exited with code {returncode}: {error_detail}")

            result_text, tool_calls, usage = _parse_codex_output(stdout, stderr, returncode)
            return result_text, tool_calls, usage

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
