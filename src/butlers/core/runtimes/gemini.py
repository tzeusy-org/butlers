"""GeminiAdapter — RuntimeAdapter implementation for Google Gemini CLI.

Encapsulates all Gemini CLI-specific logic:
- Subprocess invocation of the ``gemini`` binary
- MCP config file generation (JSON format with mcpServers key)
- GEMINI.md / AGENTS.md system prompt reading
- Result parsing: extracts text output and tool call records

The Gemini CLI is invoked headless with ``--prompt``, ``-o stream-json`` (JSONL events
carrying text, tool calls, usage and the terminal result), ``-m`` for the configured model,
``--skip-trust`` (headless runs in an untrusted folder exit 55) and ``--sandbox=false``.
The system prompt (from GEMINI.md or AGENTS.md) is written to a per-invocation file named by
``GEMINI_SYSTEM_MD``, which replaces the CLI's built-in system prompt. MCP server configs
and the turn limit (``model.maxSessionTurns``; the CLI has no turn flag) are written to a
per-invocation temporary settings file that the CLI reads as its system settings.

Runtime authentication is handled by CLI-level OAuth tokens
(device-code flow via the dashboard).

If the Gemini CLI binary is not installed on PATH, invoke() raises
FileNotFoundError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, override

from butlers.core.child_env import without_owner_auth
from butlers.core.runtimes.base import RuntimeAdapter, register_adapter
from butlers.core.runtimes.served_identity import (
    TerminalResultError,
    append_stream,
    observe_invocation,
)

logger = logging.getLogger(__name__)

# Default timeout for Gemini CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300

# gemini-cli substitutes these ``${...}`` tokens in a GEMINI_SYSTEM_MD file (its
# ``applySubstitutions``) and has no escape syntax. A zero-width space after ``$`` keeps a
# butler prompt's literal text from being replaced by the CLI's tool or skill listings.
_SYSTEM_MD_TOKEN = re.compile(
    r"\$(?=\{(?:AgentSkills|SubAgents|AvailableTools|[^{}\s]+_ToolName)\})"
)


def _escape_system_md(system_prompt: str) -> str:
    """Neutralize the CLI's system-prompt substitution tokens in literal prompt text."""
    return _SYSTEM_MD_TOKEN.sub("$\u200b", system_prompt)


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
    """Pass through runtime variables while withholding dashboard owner authority.

    Parameters
    ----------
    env:
        Source environment variables.

    Returns
    -------
    dict[str, str]
        Environment variables for Gemini.
    """
    return without_owner_auth(env)


def _to_gemini_server(server: Mapping[str, Any]) -> dict[str, Any]:
    """Map a butler MCP server entry onto Gemini's settings schema.

    Gemini's ``url`` selects the SSE transport and ``httpUrl`` the streamable-HTTP one; the
    butler endpoint is streamable HTTP. ``headers`` carries the invocation binding header.

    ``trust`` is set because headless ``--prompt`` runs treat a confirmation prompt as a denial and
    Gemini defaults MCP servers to untrusted; this is scoped to the butler's own allow-listed
    server.
    """
    entry = {key: value for key, value in server.items() if key != "url"}
    entry["trust"] = True
    if "url" in server:
        entry["httpUrl"] = server["url"]
    return entry


def _parse_gemini_output(stdout: str, stderr: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse Gemini CLI output into (result_text, tool_calls).

    The adapter requests ``-o stream-json``, whose 0.46.0 JSONL events are parsed here
    (recorded shapes in ``tests/fixtures/gemini_stream_json``):
    - ``type: "message"`` — ``role`` plus string ``content``. The ``user`` echo of the
      prompt is skipped; consecutive ``assistant`` chunks marked ``delta: true`` are
      joined into one text segment, and segments split by other events join with newlines
    - ``type: "tool_use"`` (``tool_name``/``tool_id``/``parameters``) or the older
      ``functionCall`` shape — tool invocations
    - ``type: "result"`` — terminal status and stats (no text in 0.46.0)

    If the output is not valid JSON, we treat the entire stdout as
    plain text result.

    Parameters
    ----------
    stdout:
        Raw stdout from the Gemini process.
    stderr:
        Raw stderr from the Gemini process.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]]]
        (result_text, tool_calls)
    """

    result_text: str | None = None
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []

    # Try to parse as JSON-lines (one JSON object per line)
    lines = stdout.strip().splitlines()
    parsed_any_json = False
    delta_open = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
        continuing, delta_open = delta_open, False

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
            if obj.get("role", "assistant") != "assistant":
                # stream-json echoes the prompt as a ``user`` message; it is not output.
                continue
            content = obj.get("content", "")
            if isinstance(content, str) and content:
                if obj.get("delta") is True:
                    if continuing:
                        text_parts[-1] += content
                    else:
                        text_parts.append(content)
                    delta_open = True
                else:
                    text_parts.append(content)
            elif isinstance(content, str):
                delta_open = continuing
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

    # Stream-json ``tool_use`` event: {"tool_name": ..., "tool_id": ..., "parameters": ...}
    if "tool_name" in obj:
        return {
            "id": obj.get("tool_id", ""),
            "name": obj.get("tool_name", ""),
            "input": obj.get("parameters", {}),
        }

    # Standard tool_use format
    return {
        "id": obj.get("id", ""),
        "name": obj.get("name", ""),
        "input": obj.get("input", obj.get("args", obj.get("arguments", {}))),
    }


def _gemini_tools_ran(stdout: str, tool_calls: list[dict[str, Any]]) -> bool:
    """Return whether the stream proves a tool ran, for ``is_pre_tool_call``.

    Evidence is any parsed tool call or a ``result`` event whose
    ``stats.tool_calls`` is positive. That counter is the CLI's telemetry total
    (``metrics.tools.totalCalls``), so it also counts tool calls that emit no
    top-level ``tool_use`` event, such as a subagent's.
    """
    if tool_calls:
        return True
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "result":
            continue
        stats = obj.get("stats")
        count = stats.get("tool_calls") if isinstance(stats, dict) else None
        if type(count) is int and count > 0:
            return True
    return False


class GeminiAdapter(RuntimeAdapter):
    """Runtime adapter for the Google Gemini CLI.

    Invokes the Gemini CLI binary via subprocess. The adapter handles:
    - Locating the ``gemini`` binary on PATH
    - Passing system prompts via a ``GEMINI_SYSTEM_MD`` file
    - Writing MCP config in Gemini-compatible JSON format
    - Passing through env vars
    - Parsing CLI output into (result_text, tool_calls)

    Parameters
    ----------
    gemini_binary:
        Path to the gemini binary. If None, will be auto-detected on PATH
        at invocation time.
    """

    def __init__(self, gemini_binary: str | None = None) -> None:
        self._gemini_binary = gemini_binary
        self._last_process_info: dict[str, Any] | None = None

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        """Process-level metadata from the most recent invoke() call."""
        return self._last_process_info

    def create_worker(self) -> RuntimeAdapter:
        """Create an independent adapter for a pooled spawner worker."""
        return GeminiAdapter(gemini_binary=self._gemini_binary)

    #: The CLI receives the butler's MCP server config and executes its tools.
    declared_capabilities: ClassVar[Mapping[str, bool]] = {"tool_use": True}

    @property
    def binary_name(self) -> str:
        return "gemini"

    def _get_binary(self) -> str:
        """Get the gemini binary path, auto-detecting if needed."""
        if self._gemini_binary is not None:
            return self._gemini_binary
        return _find_gemini_binary()

    @override
    @observe_invocation("gemini")
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
        """Invoke the Gemini CLI with the given prompt and configuration.

        Builds the command line, stages the system prompt as the
        ``GEMINI_SYSTEM_MD`` file in a per-invocation temporary directory, and
        parses the stream-json output. ``mcp_servers`` and the ``max_turns`` limit
        are written through :meth:`build_config_file` into that directory, which the
        CLI is pointed at as its system settings file
        (``GEMINI_CLI_SYSTEM_SETTINGS_PATH``, which Gemini ranks above user and
        project settings). When ``mcp_servers`` is non-empty,
        ``--allowed-mcp-server-names`` limits the session to the configured servers
        so user- or project-level MCP servers are not reachable. That confinement
        covers MCP servers only: Gemini's built-in shell, file and web tools are not confined
        (``--sandbox=false``). The user home is left alone so the CLI's
        OAuth credentials keep working.

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
        max_turns:
            Turn limit, staged as ``model.maxSessionTurns``.
        model:
            Model id passed as ``-m``; the CLI default applies when omitted.
        cwd:
            Working directory for the Gemini process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Valid terminal
            stream counters supply normalized uncached/cache usage; absent
            or contradictory counters remain unknown.

        Raises
        ------
        FileNotFoundError
            If the gemini binary is not found on PATH.
        TimeoutError
            If the Gemini process exceeds the timeout.
        RuntimeError
            If the Gemini process exits non-zero with no failed terminal result.
        TerminalResultError
            If the stream carries a failed terminal result (``status: "error"``),
            at any exit code; it keeps the attempt's usage and tool calls.
        """
        binary = self._get_binary()
        effective_timeout = timeout or _DEFAULT_TIMEOUT_SECONDS

        # Filter environment variables
        filtered_env = None if env is None else _filter_env(env)

        # Build command. ``--skip-trust``: headless runs in an untrusted folder exit 55.
        # ``-o stream-json``: the default text output carries no usage or terminal result.
        cmd = [binary, "--sandbox=false", "--skip-trust", "-o", "stream-json"]
        if model:
            cmd.extend(["-m", model])
        cmd.extend(["--prompt", prompt])

        cmd_for_log = " ".join(cmd[:3]) + " ..."
        proc = None
        config_dir: tempfile.TemporaryDirectory[str] | None = None

        try:
            config_dir = tempfile.TemporaryDirectory(prefix="butlers-gemini-")
            staged_env: dict[str, str] = {}
            if system_prompt:
                # The CLI has no system-prompt flag and its strict parser rejects unknown
                # ones; GEMINI_SYSTEM_MD names a file that replaces the built-in prompt.
                system_md = Path(config_dir.name) / "system.md"
                system_md.write_text(_escape_system_md(system_prompt), encoding="utf-8")
                staged_env["GEMINI_SYSTEM_MD"] = str(system_md)
            # Gemini's ``url`` key is SSE; the butler endpoint is streamable HTTP, which
            # Gemini names ``httpUrl``.
            gemini_servers = {name: _to_gemini_server(cfg) for name, cfg in mcp_servers.items()}
            config_path = self.build_config_file(
                gemini_servers, Path(config_dir.name), max_session_turns=max_turns
            )
            staged_env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(config_path)
            if gemini_servers:
                cmd.extend(["--allowed-mcp-server-names", ",".join(gemini_servers)])
            child_env = {
                **(filtered_env if filtered_env is not None else os.environ),
                **staged_env,
            }

            logger.debug("Invoking Gemini CLI: %s", cmd_for_log)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
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

            returncode = proc.returncode or 0
            served = append_stream(
                self,
                "gemini",
                stdout,
                configured=model,
                completion="error" if returncode else "success",
            )
            self._last_process_info = {
                "pid": proc.pid,
                "exit_code": returncode,
                "command": cmd_for_log,
                "stderr": stderr,
                "runtime_type": "gemini",
            }

            aggregate = served["executions"][0]["aggregate_usage"] if served["executions"] else {}
            usage = aggregate if aggregate.get("input_tokens") is not None else None
            terminal_error = any(item["error"]["is_error"] is True for item in served["executions"])
            if returncode != 0:
                error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
                logger.error("Gemini CLI exited with code %d: %s", returncode, error_detail)
                self._last_process_info["error_detail"] = error_detail
                message = f"Gemini CLI exited with code {returncode}: {error_detail}"
                if not terminal_error:
                    # No result envelope: nothing proves work started, so this is
                    # pre-tool-call. The spawner layers in daemon-side capture.
                    self._last_process_info["is_pre_tool_call"] = True
                    raise RuntimeError(message)
                # Stream-json emits ``result``/``status: error`` and then exits with
                # the error's code (turn limit is 53); keep its usage and tool evidence.
                _, tool_calls = _parse_gemini_output(stdout, stderr)
                self._last_process_info["is_pre_tool_call"] = not _gemini_tools_ran(
                    stdout, tool_calls
                )
                raise TerminalResultError(served, usage, tool_calls, message)

            result_text, tool_calls = _parse_gemini_output(stdout, stderr)
            if terminal_error:
                self._last_process_info["is_pre_tool_call"] = not _gemini_tools_ran(
                    stdout, tool_calls
                )
                raise TerminalResultError(served, usage, tool_calls)
            return result_text, tool_calls, usage

        except TimeoutError:
            logger.error("Gemini CLI timed out after %ds", effective_timeout)
            self._last_process_info = {
                "pid": proc.pid if proc else None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": "(timeout — process killed)",
                "runtime_type": "gemini",
                # Timeout fired before the adapter could confirm any tool calls —
                # the failover classifier treats this as pre-tool-call eligible.
                # The spawner will layer in daemon-side tool-call capture.
                "is_pre_tool_call": True,
            }
            if proc:
                proc.kill()
                await proc.wait()
            raise TimeoutError(f"Gemini CLI timed out after {effective_timeout} seconds") from None

        except asyncio.CancelledError:
            # Owner-initiated Stop (Spawner.cancel_session) cancels the task
            # running this coroutine. Without an explicit kill here the CLI
            # subprocess would be orphaned and keep running/spending after the
            # Python-level task unwinds — this is the actual "Stop actually
            # stops" mechanism.
            logger.warning("Gemini CLI invocation cancelled; killing subprocess")
            self._last_process_info = {
                "pid": proc.pid if proc else None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": "(cancelled — process killed)",
                "runtime_type": "gemini",
                "is_pre_tool_call": True,
            }
            if proc:
                proc.kill()
                await proc.wait()
            raise

        finally:
            if config_dir is not None:
                config_dir.cleanup()

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
        *,
        max_session_turns: int | None = None,
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
        max_session_turns:
            When positive, written as ``model.maxSessionTurns``, the CLI's only turn
            limit; reaching it ends the run with a ``FatalTurnLimitedError`` result
            and exit code 53.

        Returns
        -------
        Path
            Path to the generated gemini_mcp.json file.
        """
        config: dict[str, Any] = {"mcpServers": mcp_servers}
        if max_session_turns is not None and max_session_turns > 0:
            config["model"] = {"maxSessionTurns": max_session_turns}
        config_path = tmp_dir / "gemini_mcp.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
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
