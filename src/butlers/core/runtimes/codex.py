"""CodexAdapter — RuntimeAdapter implementation for OpenAI Codex CLI.

Encapsulates all Codex CLI-specific logic:
- Subprocess invocation of the ``codex`` binary
- MCP config file generation (JSON format with mcpServers key)
- AGENTS.md system prompt reading (Codex convention)
- Result parsing: extracts text output and tool call records

The Codex CLI is invoked via ``codex exec --json
--dangerously-bypass-approvals-and-sandbox``. Since
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
import os
import pwd
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default timeout for Codex CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300
_SAFE_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Retry delays (in seconds) when the Codex CLI fails to discover MCP tools.
# Each entry triggers one retry attempt with the given delay beforehand.
# Exponential backoff: 2s, 5s → 3 total attempts (initial + 2 retries).
_MCP_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)
_BENIGN_STDERR_LINES = frozenset(
    {
        "Reading additional input from stdin...",
    }
)


class MCPToolDiscoveryError(RuntimeError):
    """Codex exhausted MCP-discovery retries but still has partial session output.

    The Codex adapter can only infer MCP success from its parsed JSON stream.
    The spawner may later reconcile this with daemon-side runtime-session tool
    capture, so preserve the partial result on the exception.

    ``last_attempt_process_info`` is a snapshot of the process metadata for the
    attempt that actually produced ``result_text``/``usage``. The adapter
    rewrites its own ``last_process_info`` to first-attempt values to keep the
    failure-path log shape stable; when the spawner recovers via runtime
    capture it should swap in this snapshot so PID/stderr/exit_code align with
    the result source.
    """

    def __init__(
        self,
        message: str,
        *,
        result_text: str | None,
        tool_calls: list[dict[str, Any]],
        usage: dict[str, Any] | None,
        last_attempt_process_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.result_text = result_text
        self.tool_calls = list(tool_calls)
        self.usage = dict(usage) if isinstance(usage, dict) else None
        self.last_attempt_process_info = (
            dict(last_attempt_process_info) if isinstance(last_attempt_process_info, dict) else None
        )


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
    """Best-effort detection for MCP transport / discovery / connection failures.

    Used both to enrich post-hoc transport-mismatch error messages
    (``_augment_transport_error_detail``) and to decide whether a zero-tool-call
    Codex session should be retried as a suspected MCP discovery failure
    (``_should_retry_mcp_discovery``).

    Markers are deliberately specific failure-state phrases — bare tokens like
    ``"mcp"`` or ``"connect"`` would match benign progress diagnostics
    (e.g. ``"MCP connection established"``) and re-introduce false positives.
    """
    lowered = error_detail.lower()
    markers = (
        # Codex CLI MCP discovery / startup failures
        "mcp tool discovery failed",
        "mcp discovery failed",
        "mcp connection failed",
        "failed to start mcp",
        "rmcp",
        # Generic transport / connection failures
        "failed to connect",
        "connection refused",
        "connection reset",
        "timed out",
        "transport",
        # HTTP-level transport mismatch hints
        "streamable_http",
        "text/event-stream",
        "method not allowed",
        "unsupported media type",
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


def _has_mcp_tool_calls(tool_calls: list[dict[str, Any]]) -> bool:
    """Return True when at least one non-bash MCP tool call is present."""
    return any(tc.get("name") != "command_execution" for tc in tool_calls)


def _should_retry_mcp_discovery(
    *,
    mcp_servers: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    process_info: dict[str, Any] | None,
) -> bool:
    """Return whether zero-tool output likely reflects MCP discovery failure.

    A successful Codex session with configured MCP servers is still allowed to
    produce a plain text answer with no tool calls. Treating every such session
    as a transport failure manufactures false ``MCPToolDiscoveryError``
    failures for legitimate no-tool replies.

    We only enter the retry/error path when there is affirmative evidence that
    Codex expected tool access but likely missed the MCP registration:
    - bash-only ``command_execution`` output (the common degraded fallback), or
    - stderr that matches a known MCP / transport / connection failure marker
      (see ``_looks_like_transport_failure``).
    """
    if not mcp_servers or _has_mcp_tool_calls(tool_calls):
        return False

    if any(tc.get("name") == "command_execution" for tc in tool_calls):
        return True

    stderr = ""
    if isinstance(process_info, dict):
        raw_stderr = process_info.get("stderr")
        if isinstance(raw_stderr, str):
            stderr = raw_stderr
        elif raw_stderr:
            stderr = str(raw_stderr)

    return _looks_like_transport_failure(stderr)


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


def _pwd_home() -> Path | None:
    """Return the passwd-backed home dir for the current uid when available."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return None


def _resolve_canonical_home(real_home: str | None) -> Path | None:
    """Resolve a stable home dir for Codex auth/temp state.

    Some parent processes run with ``HOME`` already pointing at a transient
    session directory like ``~/.codex/.tmp/<session>``. Treating that nested
    temp dir as canonical breaks auth lookup once the parent session is
    cleaned up and can also cause Codex to create temp dirs inside temp dirs.
    """
    env_home = (
        Path(real_home).expanduser() if isinstance(real_home, str) and real_home.strip() else None
    )
    if env_home and env_home.parent.name == ".tmp" and env_home.parent.parent.name == ".codex":
        return _pwd_home() or env_home.parent.parent.parent
    return env_home or _pwd_home()


def _select_error_detail(stderr: str, stdout: str, returncode: int) -> str:
    """Prefer actionable Codex failure details over benign stderr noise."""
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    filtered_stderr_lines = [line for line in stderr_lines if line not in _BENIGN_STDERR_LINES]
    if filtered_stderr_lines:
        return "\n".join(filtered_stderr_lines)
    stderr_clean = stderr.strip()
    if stderr_clean:
        return stderr_clean
    stdout_clean = stdout.strip()
    if stdout_clean:
        return stdout_clean
    return f"exit code {returncode}"


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
        error_detail = _select_error_detail(stderr, stdout, returncode)
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
                # Skip in-progress / started events — they carry no useful
                # data (empty output, null exit_code) and would duplicate the
                # completed record for the same tool call id.
                if obj_type in ("item.started", "response.output_item.added"):
                    continue
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
                # Token reporting contract: return ints when available, or None
                # for usage entirely when token counts cannot be determined.
                if isinstance(input_tokens, int) or isinstance(output_tokens, int):
                    usage = {
                        "input_tokens": input_tokens if isinstance(input_tokens, int) else 0,
                        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
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

    # Codex emits MCP tool calls as:
    #   {"type":"mcp_tool_call","server":"<name>","tool":"<tool>","arguments":{}}
    # Construct the canonical mcp__<server>__<tool> name from those fields.
    if obj_type in ("mcp_tool_call", "mcp_tool_use"):
        server = obj.get("server", "")
        tool = obj.get("tool", "")
        if isinstance(server, str) and isinstance(tool, str) and server and tool:
            return {
                "id": obj.get("id", ""),
                "name": f"mcp__{server}__{tool}",
                "input": obj.get("arguments") or obj.get("input") or {},
            }

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


def _create_isolated_home_tempdir(real_home: str | None):
    """Create a per-invocation home dir, preferring a Codex-owned temp root.

    Current Codex CLI builds warn when ``codex_home`` lives under ``/tmp`` and
    refuse to create helper binaries there. When a real HOME exists, place
    ephemeral session homes under ``~/.codex/.tmp`` instead, and fall back to
    the platform default temp dir only if that root cannot be created.
    """
    import tempfile as _tempfile  # noqa: PLC0415

    if isinstance(real_home, str) and real_home.strip():
        preferred_root = Path(real_home) / ".codex" / ".tmp"
        try:
            preferred_root.mkdir(parents=True, exist_ok=True)
            return _tempfile.TemporaryDirectory(dir=str(preferred_root))
        except OSError:
            logger.warning(
                "Could not create Codex temp root at %s; falling back to default tempdir",
                preferred_root,
                exc_info=True,
            )

    return _tempfile.TemporaryDirectory()


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
        self._last_process_info: dict[str, Any] | None = None

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        """Process-level metadata from the most recent invoke() call.

        Returns a dict with keys: pid, exit_code, command, stderr, runtime_type.
        Available after invoke() completes (success or failure). None before
        first invocation.
        """
        return self._last_process_info

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
        runtime_args: list[str] | None = None,
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
        _spawn_start = time.monotonic()

        # Build command
        cmd = [
            binary,
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        if runtime_args:
            cmd.extend(runtime_args)

        # Write MCP config to a per-invocation config file under an isolated
        # HOME directory.  The Codex CLI reads MCP servers from
        # ``~/.codex/config.toml`` at startup — *before* ``-c`` overrides are
        # applied.  Passing MCP servers only via ``-c`` flags is unreliable
        # because (a) the spawner's ``_build_env`` does not include HOME, so
        # ``~/.codex/`` is unresolvable, and (b) ``-c`` overrides may be
        # applied after the MCP client has already initialised with an empty
        # server list.  Writing a config file and pointing HOME at its parent
        # ensures the CLI discovers MCP servers during its earliest init phase.
        real_home = _resolve_canonical_home(os.environ.get("HOME", ""))
        tmp_dir_obj = _create_isolated_home_tempdir(str(real_home) if real_home else None)
        tmp_dir = Path(tmp_dir_obj.name)

        codex_config_dir = tmp_dir / ".codex"
        codex_config_dir.mkdir()
        config_toml = self._write_mcp_config_toml(mcp_servers, codex_config_dir)
        if config_toml:
            logger.debug("Wrote Codex MCP config to %s", config_toml)

        # Symlink auth.json from the real ~/.codex/ into the temp directory so
        # the CLI can still authenticate after we override HOME.  A symlink
        # (rather than a copy) ensures that token refreshes performed by the
        # CLI are written back to the canonical location and survive temp-dir
        # cleanup.  This also prevents the "refresh_token_reused" error that
        # occurred when concurrent invocations each copied a stale token.
        if real_home:
            real_auth = real_home / ".codex" / "auth.json"
            tmp_auth = codex_config_dir / "auth.json"
            if real_auth.is_file():
                os.symlink(real_auth, tmp_auth)
                logger.debug("Symlinked Codex auth.json: %s → %s", tmp_auth, real_auth)
            else:
                logger.warning(
                    "No Codex auth.json found at %s — CLI may fail to authenticate",
                    real_auth,
                )

        # Point HOME at the temp directory so the CLI finds ~/.codex/config.toml.
        env["HOME"] = str(tmp_dir)

        # Pass the composed prompt via stdin with the explicit "-" sentinel.
        # Recent Codex CLI builds treat any positional prompt plus non-tty stdin
        # (including /dev/null) as "prompt + additional stdin", which emits a
        # noisy stderr warning even on successful runs. "-" keeps prompt
        # parsing on stdin and avoids that warning path.
        cmd.append("-")
        prompt_input = self._compose_exec_prompt(prompt=prompt, system_prompt=system_prompt)

        logger.debug("Invoking Codex CLI: %s", " ".join(cmd[:4]) + " ...")

        # Sanitise command for logging — drop the final prompt arg which can be huge
        cmd_for_log = " ".join(cmd[:-1]) + " --  ..."

        try:
            result_text, tool_calls, usage = await self._run_codex_subprocess(
                cmd,
                env,
                cwd,
                effective_timeout,
                cmd_for_log,
                mcp_servers,
                prompt_input,
            )
            # Record total spawn-to-completion latency for instrumentation.
            if self._last_process_info is not None:
                spawn_latency_ms = int((time.monotonic() - _spawn_start) * 1000)
                self._last_process_info["spawn_latency_ms"] = spawn_latency_ms
                self._last_process_info["mcp_server_count"] = len(mcp_servers)
                logger.debug(
                    "Codex spawn latency=%dms mcp_servers=%d",
                    spawn_latency_ms,
                    len(mcp_servers),
                )

            # Retry with exponential backoff when MCP tools were configured
            # but the CLI failed to discover them (intermittent MCP connection
            # failure).  Codex CLI v0.121.0 has a race where it can start
            # processing the prompt before MCP tools are fully registered.
            mcp_failed = _should_retry_mcp_discovery(
                mcp_servers=mcp_servers,
                tool_calls=tool_calls,
                process_info=self._last_process_info,
            )
            if mcp_failed:
                # Snapshot first-attempt process info before retries overwrite it.
                first_info = dict(self._last_process_info) if self._last_process_info else None
                attempt_count = 1
                retry_succeeded = False

                for delay in _MCP_RETRY_DELAYS:
                    diag = (self._last_process_info or {}).get("stderr", "")
                    diag_short = diag.strip()[:500] if diag else "(no stderr)"
                    logger.warning(
                        "Codex CLI returned 0 MCP tool calls (%d command_execution "
                        "events) despite %d MCP server(s) configured — retrying "
                        "after %.1fs (attempt %d/%d). stderr: %s",
                        len(tool_calls),
                        len(mcp_servers),
                        delay,
                        attempt_count + 1,
                        1 + len(_MCP_RETRY_DELAYS),
                        diag_short,
                    )
                    await asyncio.sleep(delay)
                    attempt_count += 1
                    retry_text, retry_calls, retry_usage = await self._run_codex_subprocess(
                        cmd,
                        env,
                        cwd,
                        effective_timeout,
                        cmd_for_log,
                        mcp_servers,
                        prompt_input,
                    )
                    # Preserve the most recent partial result so higher layers
                    # can reconcile parser output with daemon-side tool-call capture.
                    result_text, tool_calls, usage = retry_text, retry_calls, retry_usage
                    if _has_mcp_tool_calls(retry_calls):
                        logger.info(
                            "MCP retry succeeded — tools discovered on attempt %d",
                            attempt_count,
                        )
                        retry_succeeded = True
                        break

                # Snapshot the most-recent attempt's process info before we
                # potentially rewrite ``self._last_process_info`` to first-attempt
                # values for the failure-path log. The snapshot lets the spawner
                # restore consistent diagnostics if it later recovers via
                # daemon-side runtime tool capture.
                last_attempt_info = (
                    dict(self._last_process_info) if self._last_process_info else None
                )

                # Record diagnostics for session monitoring
                if self._last_process_info:
                    if not retry_succeeded and first_info:
                        self._last_process_info.update(first_info)

                    self._last_process_info["mcp_connection_failed"] = True
                    self._last_process_info["retry_attempted"] = True
                    self._last_process_info["retry_succeeded"] = retry_succeeded
                    self._last_process_info["attempt_count"] = attempt_count
                    self._last_process_info["result_source"] = (
                        "retry" if retry_succeeded else "first"
                    )

                if not retry_succeeded:
                    raise MCPToolDiscoveryError(
                        (
                            f"MCP tool discovery failed after {attempt_count} attempts. "
                            "The butler's MCP server was configured but the Codex CLI "
                            "could not connect to it. This session cannot proceed "
                            "without MCP tools."
                        ),
                        result_text=result_text,
                        tool_calls=tool_calls,
                        usage=usage,
                        last_attempt_process_info=last_attempt_info,
                    )
            else:
                if self._last_process_info:
                    self._last_process_info["mcp_connection_failed"] = not mcp_servers
                    self._last_process_info["attempt_count"] = 1

            return result_text, tool_calls, usage
        finally:
            tmp_dir_obj.cleanup()

    async def _run_codex_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path | None,
        timeout: int,
        cmd_for_log: str,
        mcp_servers: dict[str, Any],
        prompt_input: str,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Run the Codex CLI subprocess and parse its output."""
        proc = None
        try:
            # Feed the prompt through stdin using the "-" sentinel so Codex
            # does not treat inherited daemon pipes as "additional input".
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env if env else None,
                cwd=str(cwd) if cwd else None,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(prompt_input.encode("utf-8")),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if stderr:
                logger.debug("Codex stderr: %s", stderr[:500])

            returncode = proc.returncode or 0

            self._last_process_info = {
                "pid": proc.pid,
                "exit_code": returncode,
                "command": cmd_for_log,
                "stderr": stderr,
                "runtime_type": "codex",
            }

            if returncode != 0:
                error_detail = _select_error_detail(stderr, stdout, returncode)
                error_detail = _augment_transport_error_detail(error_detail, mcp_servers)
                logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
                raise RuntimeError(f"Codex CLI exited with code {returncode}: {error_detail}")

            return _parse_codex_output(stdout, stderr, returncode)

        except TimeoutError:
            logger.error("Codex CLI timed out after %ds", timeout)
            self._last_process_info = {
                "pid": proc.pid if proc else None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": "(timeout — process killed)",
                "runtime_type": "codex",
            }
            if proc:
                proc.kill()
                await proc.wait()
            raise TimeoutError(f"Codex CLI timed out after {timeout} seconds") from None

    @staticmethod
    def _write_mcp_config_toml(
        mcp_servers: dict[str, Any],
        codex_config_dir: Path,
    ) -> Path | None:
        """Write MCP servers to ``config.toml`` inside *codex_config_dir*.

        The Codex CLI reads ``~/.codex/config.toml`` at startup.  Writing
        MCP server entries here ensures they are available before the MCP
        client initialises — unlike ``-c`` overrides which may arrive too
        late.

        Returns the path to the written file, or ``None`` if there were no
        valid MCP servers to write.
        """
        toml_lines: list[str] = []
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

            escaped_url = url.strip().replace("\\", "\\\\").replace('"', '\\"')
            toml_lines.append(f"[mcp_servers.{server_name}]")
            toml_lines.append(f'url = "{escaped_url}"')

            normalized_transport, inferred_transport = _resolve_transport_details(server_cfg, url)
            if normalized_transport == "streamable_http" or (
                normalized_transport is None and inferred_transport == "streamable_http"
            ):
                toml_lines.append('transport = "streamable_http"')
            toml_lines.append("")

        if not toml_lines:
            return None

        config_path = codex_config_dir / "config.toml"
        config_path.write_text("\n".join(toml_lines))
        return config_path

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write MCP config as TOML inside a ``.codex`` subdirectory.

        Creates ``<tmp_dir>/.codex/config.toml`` so that pointing ``HOME``
        at *tmp_dir* makes the Codex CLI discover MCP servers during its
        earliest initialisation phase.

        Returns the path to the generated ``config.toml``.
        """
        codex_dir = tmp_dir / ".codex"
        codex_dir.mkdir(exist_ok=True)
        result = self._write_mcp_config_toml(mcp_servers, codex_dir)
        return result or (codex_dir / "config.toml")

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
