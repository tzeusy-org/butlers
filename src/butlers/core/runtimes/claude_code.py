"""ClaudeCodeAdapter — RuntimeAdapter implementation for the Claude CLI binary.

Encapsulates all Claude CLI-specific logic:
- Binary discovery: shutil.which("claude") with FileNotFoundError + install hint
- Subprocess invocation: asyncio.create_subprocess_exec with stream-json output
- MCP config file writing (JSON format with mcpServers key)
- Output parsing: stream-json JSON-lines → result text, tool calls, usage
- CLAUDE.md reading logic

The ``claude`` binary is invoked in print mode (``-p``) with
``--output-format stream-json`` for structured JSON-line event output.
Flags used:

- ``--bare``: skips hooks, LSP, auto-memory, CLAUDE.md auto-discovery
- ``--no-session-persistence``: ephemeral session (not stored in Claude Code history)
- ``--strict-mcp-config``: prevents host MCP servers leaking into butler sessions
- ``--permission-mode bypassPermissions``: full tool access without confirmations
- ``--system-prompt <text>``: butler's system prompt
- ``--mcp-config <path>``: butler's MCP server config
- ``--model <model>``: (optional) model to use
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# Default timeout for Claude CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300


def _find_claude_binary() -> str:
    """Locate the claude binary on PATH.

    Returns
    -------
    str
        Absolute path to the claude binary.

    Raises
    ------
    FileNotFoundError
        If the claude binary is not found on PATH.
    """
    path = shutil.which("claude")
    if path is None:
        raise FileNotFoundError(
            "Claude CLI binary not found on PATH. "
            "Install it with: npm install -g @anthropic-ai/claude-code"
        )
    return path


def _parse_claude_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Parse Claude CLI stream-json output into (result_text, tool_calls, usage).

    The ``--output-format stream-json`` output emits JSON-lines events.
    We look for:
    - ``type: "result"`` — final result with ``result`` text and ``usage`` object
    - ``type: "assistant"`` — assistant messages with tool_use content blocks
    - ``type: "message"`` — message events with text or list content blocks
    - ``type: "tool_use"`` / standalone tool call events

    If the output is not valid JSON-lines, we treat the entire stdout as plain
    text result.

    Non-zero exit codes are surfaced as an error result string
    (e.g. ``"Error: <detail>"``) with no tool calls and ``None`` usage,
    rather than raising an exception.

    Parameters
    ----------
    stdout:
        Raw stdout from the Claude process.
    stderr:
        Raw stderr from the Claude process.
    returncode:
        Exit code from the Claude process.

    Returns
    -------
    tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
        (result_text, tool_calls, usage)
    """
    if returncode != 0:
        error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        logger.error("Claude CLI exited with code %d: %s", returncode, error_detail)
        return (f"Error: {error_detail}", [], None)

    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []
    usage: dict[str, Any] | None = None

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
            if not parsed_any_json:
                fallback_text_parts.append(line)
            continue

        if not isinstance(obj, dict):
            if not parsed_any_json:
                fallback_text_parts.append(line)
            continue

        parsed_any_json = True
        obj_type = obj.get("type", "")

        if obj_type == "result":
            # Final result event — extract result text and usage
            result_content = obj.get("result", "")
            if result_content:
                text_parts.append(str(result_content))
            raw_usage = obj.get("usage")
            if isinstance(raw_usage, dict):
                input_tokens = raw_usage.get("input_tokens")
                output_tokens = raw_usage.get("output_tokens")
                if isinstance(input_tokens, int) or isinstance(output_tokens, int):
                    usage = {
                        "input_tokens": input_tokens if isinstance(input_tokens, int) else 0,
                        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
                    }
                    cache_read = raw_usage.get("cache_read_input_tokens")
                    cache_creation = raw_usage.get("cache_creation_input_tokens")
                    if isinstance(cache_read, int):
                        usage["cache_read_input_tokens"] = cache_read
                    if isinstance(cache_creation, int):
                        usage["cache_creation_input_tokens"] = cache_creation

        elif obj_type in ("assistant", "message"):
            # Assistant or message events — extract text and tool_use from content blocks
            content = obj.get("content", "")
            if isinstance(content, str) and content:
                text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_val = block.get("text", "")
                        if text_val:
                            text_parts.append(text_val)
                    elif block_type == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input", {}),
                            }
                        )

        elif obj_type == "tool_use":
            # Standalone tool_use event
            tool_calls.append(
                {
                    "id": obj.get("id", ""),
                    "name": obj.get("name", ""),
                    "input": obj.get("input", {}),
                }
            )

        else:
            # Unknown type — check for text or content fields (shared contract)
            if "text" in obj:
                text_parts.append(str(obj["text"]))
            elif "content" in obj and isinstance(obj["content"], str):
                text_parts.append(obj["content"])

    # If we couldn't parse any JSON, treat entire stdout as result text
    if not parsed_any_json and not text_parts:
        text_parts = fallback_text_parts or ([stdout.strip()] if stdout.strip() else [])

    result_text = "\n".join(text_parts) if text_parts else None
    return result_text, tool_calls, usage


class ClaudeCodeAdapter(RuntimeAdapter):
    """Runtime adapter for the Claude CLI binary (subprocess-based).

    Invokes the ``claude`` binary via subprocess in print mode (``-p``) with
    ``--output-format stream-json``. The adapter handles:

    - Locating the ``claude`` binary on PATH
    - Running in non-interactive print mode with structured JSON-line output
    - Passing system prompts via ``--system-prompt``
    - Writing MCP config in JSON format and passing via ``--mcp-config``
    - Parsing stream-json output into (result_text, tool_calls, usage)
    - Capturing stderr to a per-butler log file

    Parameters
    ----------
    claude_binary:
        Path to the claude binary. If None, will be auto-detected on PATH
        at invocation time.
    butler_name:
        Name of the butler this adapter serves. Used to construct per-butler
        stderr log paths. Optional; when omitted stderr is not logged to file.
    log_root:
        Root directory for log files. When set, stderr from the Claude CLI
        subprocess is written to
        ``{log_root}/butlers/{butler_name}_cc_stderr.log``. When ``None``,
        stderr capture is disabled.

    Notes
    -----
    The ``max_turns`` parameter in ``invoke()`` is accepted but not enforced
    by the CLI — there is no equivalent ``--max-turns`` flag. Timeout is the
    primary safety mechanism.
    """

    def __init__(
        self,
        claude_binary: str | None = None,
        butler_name: str | None = None,
        log_root: Path | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._claude_binary = claude_binary
        self._butler_name = butler_name
        self._log_root = log_root
        self._credential_store = credential_store
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
        return ClaudeCodeAdapter(
            claude_binary=self._claude_binary,
            butler_name=self._butler_name,
            log_root=self._log_root,
            credential_store=self._credential_store,
        )

    @property
    def binary_name(self) -> str:
        return "claude"

    def _get_binary(self) -> str:
        """Get the claude binary path, auto-detecting if needed."""
        if self._claude_binary is not None:
            return self._claude_binary
        return _find_claude_binary()

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
        """Invoke the Claude CLI binary with the given prompt and configuration.

        Builds the command line for ``claude -p --output-format stream-json``,
        passes system prompt and MCP config, and parses JSON-line output events.

        Parameters
        ----------
        prompt:
            The user prompt to send to Claude.
        system_prompt:
            System-level instructions (from CLAUDE.md).
        mcp_servers:
            MCP server configurations for the butler.
        env:
            Filtered environment variables for the subprocess.
        max_turns:
            Accepted but not enforced — the Claude CLI has no ``--max-turns``
            flag. Timeout is the primary safety mechanism.
        model:
            Optional model to use (passed via ``--model``).
        runtime_args:
            Additional CLI arguments inserted after fixed flags (e.g.
            ``["--effort", "high"]``).
        cwd:
            Working directory for the Claude process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Usage contains
            input_tokens, output_tokens, and optionally cache token counts.

        Raises
        ------
        FileNotFoundError
            If the claude binary is not found on PATH.
        TimeoutError
            If the Claude process exceeds the timeout.
        RuntimeError
            If the Claude process exits with a non-zero exit code.
        """
        import tempfile

        binary = self._get_binary()
        effective_timeout = _DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout

        # Inject ANTHROPIC_API_KEY from credential store when available.
        # The key is stored under 'cli-auth/claude' by the CLI auth dashboard flow.
        # Only inject when not already present in the caller-provided env dict so
        # that callers who manage the key themselves retain full control.
        _ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
        if _ANTHROPIC_KEY not in env:
            api_key: str | None = None
            if self._credential_store is not None:
                try:
                    api_key = await self._credential_store.load("cli-auth/claude")
                except Exception:
                    logger.debug(
                        "ClaudeCodeAdapter: failed to resolve %s from credential store",
                        _ANTHROPIC_KEY,
                        exc_info=True,
                    )
            if api_key is None:
                # Fall back to env (dev/testing convenience, not production path)
                api_key = os.environ.get(_ANTHROPIC_KEY)
            if api_key:
                env = {**env, _ANTHROPIC_KEY: api_key}

        # Open per-butler stderr log file for Claude CLI diagnostics
        stderr_log_file = None
        if self._butler_name and self._log_root is not None:
            try:
                stderr_dir = self._log_root / "butlers"
                stderr_dir.mkdir(parents=True, exist_ok=True)
                stderr_path = stderr_dir / f"{self._butler_name}_cc_stderr.log"
                stderr_log_file = open(stderr_path, "a", buffering=1)  # noqa: SIM115
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                stderr_log_file.write(f"\n--- runtime session start: {ts} ---\n")
                stderr_log_file.flush()
            except OSError:
                logger.warning(
                    "Could not open Claude CC stderr log for %s",
                    self._butler_name,
                    exc_info=True,
                )
                stderr_log_file = None

        # Write MCP config file into a temporary directory.
        # tempdir is created here so that cleanup in the finally block is guaranteed
        # even if build_config_file raises (e.g., serialization or filesystem error).
        tmp_dir_obj = tempfile.TemporaryDirectory()
        proc = None

        try:
            tmp_dir = Path(tmp_dir_obj.name)
            mcp_config_path = self.build_config_file(mcp_servers=mcp_servers, tmp_dir=tmp_dir)

            # Build command
            cmd = [
                binary,
                "-p",
                "--output-format",
                "stream-json",
                "--bare",
                "--no-session-persistence",
                "--permission-mode",
                "bypassPermissions",
                "--strict-mcp-config",
                "--mcp-config",
                str(mcp_config_path),
            ]

            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])

            if isinstance(model, str) and model.strip():
                cmd.extend(["--model", model.strip()])

            if runtime_args:
                cmd.extend(runtime_args)

            # Prompt is the final positional argument
            cmd.append(prompt)

            logger.debug("Invoking Claude CLI: %s", " ".join(cmd[:6]) + " ...")

            # Sanitise command for logging:
            # - redact the value of --system-prompt (may contain sensitive data)
            # - drop the final prompt arg which can be huge
            sanitized_cmd: list[str] = []
            skip_next = False
            for token in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if token == "--system-prompt":
                    sanitized_cmd.extend(["--system-prompt", "<redacted>"])
                    skip_next = True
                else:
                    sanitized_cmd.append(token)
            cmd_for_log = " ".join(sanitized_cmd[:-1]) + " ..."

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(cwd) if cwd else None,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if stderr:
                logger.debug("Claude CLI stderr: %s", stderr[:500])
                if stderr_log_file is not None:
                    try:
                        stderr_log_file.write(stderr)
                        stderr_log_file.flush()
                    except OSError:
                        pass

            returncode = proc.returncode or 0

            # Capture process info for session diagnostics
            self._last_process_info = {
                "pid": proc.pid,
                "exit_code": returncode,
                "command": cmd_for_log,
                "stderr": stderr,
                "runtime_type": "claude",
            }

            if returncode != 0:
                error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
                logger.error("Claude CLI exited with code %d: %s", returncode, error_detail)
                raise RuntimeError(f"Claude CLI exited with code {returncode}: {error_detail}")

            result_text, tool_calls, usage = _parse_claude_output(stdout, stderr, returncode)
            return result_text, tool_calls, usage

        except TimeoutError:
            logger.error("Claude CLI timed out after %ds", effective_timeout)
            self._last_process_info = {
                "pid": proc.pid if proc else None,
                "exit_code": -1,
                "command": cmd_for_log,
                "stderr": "(timeout — process killed)",
                "runtime_type": "claude",
            }
            if proc:
                proc.kill()
                await proc.wait()
            raise TimeoutError(f"Claude CLI timed out after {effective_timeout} seconds") from None

        finally:
            tmp_dir_obj.cleanup()
            if stderr_log_file is not None:
                try:
                    stderr_log_file.close()
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


register_adapter("claude", ClaudeCodeAdapter)
