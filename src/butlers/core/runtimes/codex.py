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
import base64
import binascii
import contextlib
import fcntl
import json
import logging
import os
import pwd
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from butlers.core.mcp_urls import prefer_ipv4_loopback_url
from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

if TYPE_CHECKING:
    from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# Default timeout for Codex CLI invocation (5 minutes)
_DEFAULT_TIMEOUT_SECONDS = 300
_SAFE_MCP_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Cross-process lock for serialising Codex CLI spawns during token refresh.
# The lock file lives in ~/.codex/ so it is shared across all butler daemons
# that mount the same home directory (container or host).
_CODEX_REFRESH_LOCK_FILENAME = "butlers.refresh.lock"

# Seconds before token expiry at which we consider a refresh imminent.
# If the on-disk access_token expires within this window (or the expiry is
# unknown), we take the slow path and hold the cross-process lock across the
# entire Codex spawn so only one process refreshes at a time.
_CODEX_TOKEN_EXPIRY_BUFFER_SECONDS = 60

# Maximum time (seconds) to wait for the cross-process lock before giving up
# and proceeding unlocked.  Avoids deadlocking the spawner when a sibling
# process holds the lock unexpectedly long.
_CODEX_REFRESH_LOCK_TIMEOUT_SECONDS = 30

# Emit a structured info message when waiting for the lock takes longer than this.
_CODEX_REFRESH_LOCK_CONTENTION_WARN_SECONDS = 5

# Retry delays (in seconds) when the Codex CLI fails to discover MCP tools.
# Each entry triggers one retry attempt with the given delay beforehand.
# Exponential backoff: 2s, 5s → 3 total attempts (initial + 2 retries).
_MCP_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)
_TRANSIENT_CLI_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0)
# Codex defaults MCP startup/tool-list discovery to 10s. Butler MCP servers can
# expose a large tool surface (calendar, contacts, memory, media modules), so
# make the generated runtime config explicit and less brittle.
_DEFAULT_MCP_STARTUP_TIMEOUT_SECONDS = 30.0
_BENIGN_STDERR_LINES = frozenset(
    {
        "Reading additional input from stdin...",
    }
)
_BENIGN_STDERR_PREFIXES = ("WARNING: proceeding, even though we could not update PATH:",)


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


def _filtered_stderr_lines(stderr: str) -> list[str]:
    """Return stderr lines with known benign Codex notices removed."""
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return [
        line
        for line in stderr_lines
        if line not in _BENIGN_STDERR_LINES
        and not any(line.startswith(prefix) for prefix in _BENIGN_STDERR_PREFIXES)
    ]


def _prefer_ipv4_loopback(url: str) -> str:
    """Rewrite bare ``localhost`` URLs to IPv4 loopback for Codex MCP.

    The daemon currently binds its MCP socket on an IPv4 listener. Some Codex
    CLI builds appear to prefer ``::1`` for ``localhost`` and do not reliably
    fall back to ``127.0.0.1`` for MCP discovery, which manifests as repeated
    "could not connect to it" retries even though the butler is up.

    Restrict the rewrite to exact ``localhost`` hosts so remote endpoints and
    explicit IP literals preserve their original meaning.
    """
    return prefer_ipv4_loopback_url(url)


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


def _looks_like_transient_cli_failure(error_detail: str) -> bool:
    """Return True when Codex stderr matches a known transient backend failure."""
    lowered = error_detail.lower()
    markers = (
        "codex_core::compact_remote",
        "compact_remote",
        "remote compaction failed",
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


def _toml_number(value: Any) -> str | None:
    """Render a positive TOML number from trusted numeric config values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return str(int(value)) if float(value).is_integer() else str(float(value))


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

    We only enter the retry/error path when stderr matches a known MCP /
    transport / connection failure marker (see ``_looks_like_transport_failure``).
    Completed turns that only used Codex's built-in ``command_execution`` tool
    are valid shell-only sessions, not connection-failure evidence on their own.
    """
    if not mcp_servers or _has_mcp_tool_calls(tool_calls):
        return False

    stderr = ""
    if isinstance(process_info, dict):
        raw_stderr = process_info.get("stderr")
        if isinstance(raw_stderr, str):
            stderr = raw_stderr
        elif raw_stderr:
            stderr = str(raw_stderr)

    return _looks_like_transport_failure(stderr)


def _should_retry_transient_cli_failure(process_info: dict[str, Any] | None) -> bool:
    """Return whether the most recent non-zero Codex exit looks transient."""
    if not isinstance(process_info, dict):
        return False
    if process_info.get("exit_code") in (None, 0):
        return False

    candidates: list[str] = []
    for key in ("error_detail", "stderr"):
        value = process_info.get(key, "")
        if isinstance(value, str):
            text = value
        else:
            text = str(value)
        if text:
            candidates.append(text)

    return any(_looks_like_transient_cli_failure(text) for text in candidates)


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


def _read_codex_token_expires_at(codex_dir: Path) -> float | None:
    """Read the Codex access-token expiry from ``~/.codex/auth.json``.

    Returns the numeric expiry timestamp, or ``None`` when the file is absent,
    unreadable, or does not contain a parseable expiry.  Older auth files used
    a top-level ``expires_at`` field; current Codex auth files store a JWT at
    ``tokens.access_token`` whose payload contains the ``exp`` claim.  The raw
    file content is never logged to satisfy the security-and-secrets bar.
    """
    auth_path = codex_dir / "auth.json"
    try:
        raw = auth_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    val = data.get("expires_at")
    if isinstance(val, (int, float)):
        return float(val)

    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        access_token = tokens.get("access_token")
    else:
        access_token = None
    if not isinstance(access_token, str) or access_token == "":
        return None
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def _token_needs_refresh(codex_dir: Path) -> bool:
    """Return ``True`` when the on-disk Codex token is absent or near expiry.

    "Near expiry" means the ``expires_at`` timestamp in ``auth.json`` is within
    ``_CODEX_TOKEN_EXPIRY_BUFFER_SECONDS`` of the current wall-clock time.
    Unknown expiry (missing file or field) is treated as needing refresh so the
    first invocation after daemon boot always takes the serialised slow path.
    """
    expires_at = _read_codex_token_expires_at(codex_dir)
    if expires_at is None:
        return True
    return time.time() >= expires_at - _CODEX_TOKEN_EXPIRY_BUFFER_SECONDS


@contextlib.asynccontextmanager
async def _codex_refresh_lock(codex_dir: Path):  # type: ignore[return]
    """Async context manager that acquires a cross-process POSIX flock.

    Tries ``fcntl.flock(LOCK_EX | LOCK_NB)`` in a thread pool executor so the
    event loop is not blocked.  Retries for up to
    ``_CODEX_REFRESH_LOCK_TIMEOUT_SECONDS`` seconds with 0.25s intervals.

    If the lock cannot be acquired within the timeout the manager logs a
    structured info message and yields anyway (the caller proceeds unlocked) so
    the spawner is never deadlocked. This is a designed fallback rather than a
    runtime failure; warning/error level logs are reserved for cases where the
    lock file cannot be opened or the pre-warm command itself fails.

    Usage::

        async with _codex_refresh_lock(codex_dir):
            await _run_codex_subprocess(...)
    """
    lock_path = codex_dir / _CODEX_REFRESH_LOCK_FILENAME
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError:
        logger.warning(
            "codex_refresh_lock: could not open lock file %s — proceeding unlocked",
            lock_path,
        )
        yield
        return

    acquired = False
    try:
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + _CODEX_REFRESH_LOCK_TIMEOUT_SECONDS
        warned_contention = False

        while True:
            try:
                await loop.run_in_executor(
                    None, lambda: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                )
                acquired = True
                break
            except BlockingIOError:
                pass  # EAGAIN / EWOULDBLOCK — another process holds it

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info(
                    "codex_refresh_lock: lock held >%ds by another process — proceeding "
                    "unlocked to avoid deadlock (lock_path=%s)",
                    _CODEX_REFRESH_LOCK_TIMEOUT_SECONDS,
                    lock_path,
                )
                break

            if not warned_contention and (
                _CODEX_REFRESH_LOCK_TIMEOUT_SECONDS - remaining
                >= _CODEX_REFRESH_LOCK_CONTENTION_WARN_SECONDS
            ):
                warned_contention = True
                logger.info(
                    "codex_refresh_lock: waiting >%ds for cross-process refresh lock — "
                    "possible contention (lock_path=%s)",
                    _CODEX_REFRESH_LOCK_CONTENTION_WARN_SECONDS,
                    lock_path,
                )

            await asyncio.sleep(0.25)

        yield

    finally:
        if acquired:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(lock_fd)


async def run_codex_pre_warm(codex_dir: Path, codex_binary: str) -> None:
    """Run ``codex login status`` under the cross-process refresh lock.

    Forces a token refresh if the stored credentials are stale, so that all
    subsequent Codex CLI invocations within the access-token TTL skip the
    refresh entirely.  This should be called:

    - On first ``CodexAdapter.invoke()`` per process (startup pre-warm).
    - After a successful CLI auth flow for the ``codex`` provider.

    The call is best-effort: any exception is logged and swallowed so the
    caller's control flow is never disrupted.
    """
    try:
        async with _codex_refresh_lock(codex_dir):
            proc = await asyncio.create_subprocess_exec(
                codex_binary,
                "login",
                "status",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": str(codex_dir.parent)},
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("codex pre-warm: login status timed out after 30s")
                return
            if proc.returncode != 0:
                stderr_snip = stderr_bytes.decode("utf-8", errors="replace").strip()[:200]
                logger.warning(
                    "codex pre-warm: login status exited %d — token may be invalid (stderr snip)",
                    proc.returncode,
                    extra={"codex_prewarm_stderr_len": len(stderr_snip)},
                )
            else:
                logger.debug("codex pre-warm: login status OK")
    except Exception:
        logger.warning("codex pre-warm: unexpected error", exc_info=True)


def _extract_text_field(value: Any) -> str | None:
    """Recursively extract a human-readable text field from JSON-like payloads."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, dict):
        return None

    for key in ("message", "detail", "text", "result", "error"):
        if key not in value:
            continue
        extracted = _extract_text_field(value.get(key))
        if extracted:
            return extracted
    return None


def _extract_structured_stdout_error(stdout: str) -> str | None:
    """Return the terminal failure message from JSON-line stdout, if present.

    Walks lines in reverse and prefers a ``turn.failed`` event, since that is
    Codex's explicit terminal error signal. Earlier transient ``error`` events
    (e.g. ``"Reconnecting... 5/5"``) only win when no ``turn.failed`` exists.
    Returning the terminal message keeps the surfaced exception focused on the
    actual cause rather than retry chatter.
    """
    fallback_error: str | None = None
    for raw_line in reversed(stdout.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue

        obj_type = str(obj.get("type", ""))
        if obj_type == "turn.failed":
            error_obj = obj.get("error")
            if isinstance(error_obj, dict):
                message = error_obj.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = obj.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        elif obj_type == "error" and fallback_error is None:
            message = obj.get("message")
            if isinstance(message, str) and message.strip():
                fallback_error = message.strip()
    return fallback_error


def _extract_stdout_json_detail(stdout: str) -> tuple[str | None, bool]:
    """Extract a useful detail from structured stdout on non-zero Codex exits.

    Codex can emit JSON progress events on stdout even when it exits non-zero.
    Returning that raw JSON blob as the exception message is noisy and obscures
    the actual failure. Prefer explicit error payloads first, then any
    assistant-authored terminal message, and otherwise report no detail so the
    caller can fall back to the exit code alone.
    """
    explicit_errors: list[str] = []
    assistant_texts: list[str] = []
    result_texts: list[str] = []
    non_json_lines: list[str] = []
    parsed_any_json = False

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            non_json_lines.append(line)
            continue

        if not isinstance(obj, dict):
            continue

        parsed_any_json = True
        obj_type = str(obj.get("type", ""))

        if "error" in obj:
            extracted = _extract_text_field(obj.get("error"))
            if extracted:
                explicit_errors.append(extracted)

        if obj_type in {"error", "turn.failed", "response.failed", "thread.failed"}:
            extracted = (
                _extract_text_field(obj)
                or _extract_text_field(obj.get("message"))
                or _extract_text_field(obj.get("detail"))
            )
            if extracted:
                explicit_errors.append(extracted)
            continue

        if obj_type in (
            "item.completed",
            "item.started",
            "response.output_item.done",
            "response.output_item.added",
        ):
            item = obj.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                extracted = _extract_text_field(item)
                if extracted:
                    assistant_texts.append(extracted)
            continue

        if obj_type == "message":
            extracted = _extract_text_field(obj.get("content")) or _extract_text_field(obj)
            if extracted:
                assistant_texts.append(extracted)
            continue

        if obj_type == "result":
            extracted = _extract_text_field(obj)
            if extracted:
                result_texts.append(extracted)
            continue

        extracted = _extract_text_field(obj.get("text"))
        if extracted:
            result_texts.append(extracted)

    for bucket in (explicit_errors, non_json_lines, assistant_texts, result_texts):
        if bucket:
            return "\n".join(dict.fromkeys(bucket)), parsed_any_json
    return None, parsed_any_json


def _select_error_detail(stderr: str, stdout: str, returncode: int) -> str:
    """Prefer actionable Codex failure details over benign stderr noise.

    A structured ``turn.failed`` (or terminal ``error``) event on stdout is the
    most actionable signal Codex emits and should beat lifecycle / retry chatter
    on stderr (e.g. the ``WARNING: proceeding...`` PATH notice or websocket
    reconnect logs). Falls through to filtered stderr, then other stdout
    extraction strategies, and finally the bare exit code.
    """
    # Prefer per-event structured failure details: ``turn.failed`` events
    # render with redacted unknown payloads and join multi-field errors
    # (e.g. ``message`` + ``code``).
    stdout_detail = _select_stdout_error_detail(stdout)
    if stdout_detail:
        return stdout_detail

    # Defense-in-depth: ``_extract_structured_stdout_error`` covers the same
    # turn.failed surface but via a reverse-walk; keep it as a backstop in
    # case ``_select_stdout_error_detail`` returns ``None`` for an event
    # shape its key list does not yet recognise.
    structured_stdout_error = _extract_structured_stdout_error(stdout)
    if structured_stdout_error:
        return structured_stdout_error

    filtered_stderr_lines = _filtered_stderr_lines(stderr)
    if filtered_stderr_lines:
        return "\n".join(filtered_stderr_lines)
    stderr_clean = stderr.strip()
    if stderr_clean:
        return stderr_clean

    # When no structured failure was extracted, surface assistant-message /
    # result text via the broader extractor so non-failure stdout still
    # produces an actionable headline instead of a bare ``exit code N``.
    extra_detail, parsed_stdout_json = _extract_stdout_json_detail(stdout)
    if extra_detail:
        return extra_detail

    stdout_clean = stdout.strip()
    if parsed_stdout_json and stdout_clean:
        return f"exit code {returncode}"
    if stdout_clean:
        return stdout_clean
    return f"exit code {returncode}"


def _select_stdout_error_detail(stdout: str) -> str | None:
    """Return actionable stdout diagnostics while skipping routine JSON events.

    Priority ordering is intentional: a structured failure event (e.g.
    ``turn.failed``) is always more actionable than free-form plain text on
    stdout, so structured error details win when present. Plain text only
    surfaces if no structured failure event was extracted, and routine
    progress JSON (``thread.started``, ``turn.started``, ``item.completed``)
    is filtered out entirely so it cannot become the headline failure detail.

    When a terminal ``*.failed`` event is present, only that event's detail
    is surfaced — earlier transient ``error`` events (e.g. retry chatter)
    are dropped so the headline reflects the actual cause.
    """
    plain_lines: list[str] = []
    structured_lines: list[str] = []
    terminal_failure_detail: str | None = None
    saw_structured_json = False

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            plain_lines.append(line)
            continue

        if not isinstance(obj, dict):
            plain_lines.append(line)
            continue

        saw_structured_json = True
        detail = _extract_structured_error_detail(obj)
        if not detail:
            continue

        structured_lines.append(detail)
        obj_type = str(obj.get("type", "")).strip().lower()
        if obj_type.endswith(".failed") or obj_type.endswith("_failed"):
            # Walk forward — last terminal failure wins, matching the
            # semantics of ``_extract_structured_stdout_error``.
            terminal_failure_detail = detail

    if terminal_failure_detail:
        return terminal_failure_detail
    if structured_lines:
        return "\n".join(dict.fromkeys(structured_lines))
    if plain_lines:
        return "\n".join(dict.fromkeys(plain_lines))
    if saw_structured_json:
        return None

    stdout_clean = stdout.strip()
    return stdout_clean or None


def _extract_structured_error_detail(obj: dict[str, Any]) -> str | None:
    """Extract explicit failure details from a structured Codex event."""
    candidates: list[str] = []
    obj_type = str(obj.get("type", "")).strip().lower()
    item = obj.get("item")

    if obj_type == "error" or obj_type.endswith(".failed") or obj_type.endswith("_failed"):
        candidates.extend(_structured_error_text_candidates(obj))

    if isinstance(item, dict) and str(item.get("status", "")).strip().lower() == "failed":
        candidates.extend(_structured_error_text_candidates(item))

    unique_candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    if unique_candidates:
        return "\n".join(unique_candidates)
    return None


def _structured_error_text_candidates(obj: dict[str, Any]) -> list[str]:
    """Collect human-readable error strings from a structured event payload."""
    texts: list[str] = []
    for key in ("error", "message", "detail", "details", "stderr", "aggregated_output"):
        value = obj.get(key)
        rendered = _render_structured_error_value(value)
        if rendered:
            texts.append(rendered)
    return texts


_UNRECOGNIZED_STRUCTURED_ERROR = "<unrecognized structured error payload>"


def _render_structured_error_value(value: Any) -> str | None:
    """Render nested structured error values as concise text.

    Returns a categorical placeholder when a dict has no recognized error
    key rather than dumping the full payload to the caller — exposing the
    raw structure to upstream callers risks leaking internal shape and
    creating noisy, fingerprintable error messages. The full payload is
    logged at DEBUG level for diagnostics.
    """
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        preferred_parts: list[str] = []
        for key in ("message", "detail", "details", "stderr", "error", "code"):
            nested = _render_structured_error_value(value.get(key))
            if nested:
                preferred_parts.append(nested)
        if preferred_parts:
            return " | ".join(dict.fromkeys(preferred_parts))
        try:
            logger.debug(
                "codex.adapter.structured_error.unrecognized payload=%s",
                json.dumps(value, sort_keys=True),
            )
        except (TypeError, ValueError):
            logger.debug("codex.adapter.structured_error.unrecognized payload=<unserializable>")
        return _UNRECOGNIZED_STRUCTURED_ERROR
    if isinstance(value, list):
        parts = [_render_structured_error_value(item) for item in value]
        filtered = [part for part in parts if part]
        if filtered:
            return " | ".join(dict.fromkeys(filtered))
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_codex_output_payload(
    stdout: str,
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None, bool]:
    """Parse successful Codex stdout into (result_text, tool_calls, usage, completed).

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
    Returns
    -------
    tuple[str | None, list[dict[str, Any]], dict[str, Any] | None, bool]
        (result_text, tool_calls, usage, completed)
    """

    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = []
    usage: dict[str, Any] | None = None
    completed = False

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
            completed = True
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
            completed = True
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
    return result_text, tool_calls, usage, completed


def _parse_codex_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Parse Codex CLI output into (result_text, tool_calls)."""
    if returncode != 0:
        error_detail = _select_error_detail(stderr, stdout, returncode)
        logger.error("Codex CLI exited with code %d: %s", returncode, error_detail)
        return (f"Error: {error_detail}", [], None)
    result_text, tool_calls, usage, _completed = _parse_codex_output_payload(stdout)
    return result_text, tool_calls, usage


def _recover_completed_nonzero_exit(
    stdout: str,
    stderr: str,
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None] | None:
    """Recover a completed Codex response when the CLI exits non-zero.

    Some Codex CLI builds can emit a full terminal JSON event stream and then
    still return a non-zero process code with no actionable stderr. When that
    happens, prefer the completed stdout payload over treating the session as a
    wrapper failure.
    """
    if _filtered_stderr_lines(stderr):
        return None
    result_text, tool_calls, usage, completed = _parse_codex_output_payload(stdout)
    if not completed:
        return None
    if result_text is None and not tool_calls:
        return None
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


def _cleanup_isolated_home_tempdir(tmp_dir_obj: Any, tmp_dir: Path) -> None:
    """Best-effort cleanup for the per-invocation Codex HOME.

    Codex can create transient plugin-cache directories under the isolated
    HOME while the parent process exits. ``shutil.rmtree`` may observe those
    directories mid-update and raise ``OSError: Directory not empty``. Cleanup
    failures must not turn an otherwise completed runtime session into a
    failed invocation.
    """
    try:
        tmp_dir_obj.cleanup()
    except OSError:
        logger.warning(
            "Codex isolated HOME cleanup failed for %s; attempting best-effort removal",
            tmp_dir,
            exc_info=True,
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)


class CodexAdapter(RuntimeAdapter):
    """Runtime adapter for the OpenAI Codex CLI.

    Invokes the Codex CLI binary via subprocess. The adapter handles:
    - Locating the ``codex`` binary on PATH
    - Running in non-interactive mode via ``codex exec --json``
    - Embedding system prompts into the initial instructions payload
    - Writing MCP config in Codex-compatible JSON format
    - Parsing CLI output into (result_text, tool_calls)

    Cross-process refresh-token serialisation:
        On the first ``invoke()`` per process and any time the on-disk
        ``access_token`` is near expiry, the adapter acquires a POSIX flock on
        ``~/.codex/butlers.refresh.lock`` before spawning the Codex CLI.  This
        ensures only one butler daemon triggers an OAuth token refresh at a time,
        eliminating ``refresh_token_reused`` errors during the first burst after
        a daemon restart.

    Parameters
    ----------
    codex_binary:
        Path to the codex binary. If None, will be auto-detected on PATH
        at invocation time.
    """

    # Process-wide flag: set to True after the first successful pre-warm so
    # subsequent invocations on a fresh token skip the lock acquisition.
    # Using a mutable class-level set so all instances in the same OS process
    # share state without a global variable at module scope.
    _prewarm_done: set[str] = set()  # keyed by canonical codex_dir path

    def __init__(
        self,
        codex_binary: str | None = None,
        *,
        credential_store: CredentialStore | None = None,
        butler_name: str = "",
    ) -> None:
        self._codex_binary = codex_binary
        self._credential_store = credential_store
        self._butler_name = butler_name
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
        return CodexAdapter(
            codex_binary=self._codex_binary,
            credential_store=self._credential_store,
            butler_name=self._butler_name,
        )

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
            # Butler runtime sessions are intentionally one-shot. Persisting
            # Codex thread/session state across invocations is unnecessary and
            # can push fresh runs onto stale remote-compaction paths.
            "--ephemeral",
        ]

        if isinstance(model, str) and model.strip():
            cmd.extend(["--model", model.strip()])

        if runtime_args:
            cmd.extend(runtime_args)

        # Resolve the canonical home directory early so we can check token
        # expiry and run the pre-warm before creating the isolated tempdir.
        real_home = _resolve_canonical_home(os.environ.get("HOME", ""))

        # --- Cross-process refresh-token serialisation -----------------------
        # When the on-disk auth.json exists but the access_token is near expiry
        # (slow path), we must ensure only one butler process refreshes the token
        # at a time.
        #
        # Strategy:
        #   1. First invoke() per process with a stale token: run a cheap
        #      ``codex login status`` pre-warm *under the lock* so that subsequent
        #      fast-path invocations find a fresh token on disk and skip the lock.
        #   2. Subsequent slow-path invocations (token still near expiry after
        #      pre-warm): acquire the lock and hold it across the Codex spawn so
        #      only one process triggers the server-side refresh at a time.
        #   3. If auth.json does not exist yet (user not authenticated), skip
        #      the slow path entirely — there is nothing to refresh.
        real_codex_dir = real_home / ".codex" if real_home else None
        # _auth_json_present: True only when the file exists; missing file →
        # unauthenticated state, not a stale-token state.
        _auth_json_present = real_codex_dir is not None and (real_codex_dir / "auth.json").exists()
        _needs_refresh = _auth_json_present and _token_needs_refresh(real_codex_dir)
        _prewarm_key = str(real_codex_dir) if real_codex_dir else ""

        if _needs_refresh and _prewarm_key and _prewarm_key not in CodexAdapter._prewarm_done:
            # First invoke() for this process on a token that looks stale:
            # run a pre-warm to refresh the token before the actual spawn.
            logger.debug("codex invoke: running startup pre-warm (prewarm_key=%s)", _prewarm_key)
            await run_codex_pre_warm(real_codex_dir, binary)
            CodexAdapter._prewarm_done.add(_prewarm_key)
            # Re-evaluate — the pre-warm may have refreshed the token.
            _needs_refresh = _auth_json_present and _token_needs_refresh(real_codex_dir)
        # ---------------------------------------------------------------------

        # Write MCP config to a per-invocation config file under an isolated
        # HOME directory.  The Codex CLI reads MCP servers from
        # ``~/.codex/config.toml`` at startup — *before* ``-c`` overrides are
        # applied.  Passing MCP servers only via ``-c`` flags is unreliable
        # because (a) the spawner's ``_build_env`` does not include HOME, so
        # ``~/.codex/`` is unresolvable, and (b) ``-c`` overrides may be
        # applied after the MCP client has already initialised with an empty
        # server list.  Writing a config file and pointing HOME at its parent
        # ensures the CLI discovers MCP servers during its earliest init phase.
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

        # Resolve the canonical auth.json path for rotation detection.
        # This is the symlink *target* — the real file that the Codex CLI writes to.
        auth_token_path: Path | None = (real_home / ".codex" / "auth.json") if real_home else None

        subprocess_attempt_count = 0

        async def _run_once() -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
            nonlocal subprocess_attempt_count
            subprocess_attempt_count += 1
            return await self._run_codex_subprocess(
                cmd,
                env,
                cwd,
                effective_timeout,
                cmd_for_log,
                mcp_servers,
                prompt_input,
                token_path=auth_token_path,
            )

        # Slow-path serialisation: when the token is near expiry (or unknown),
        # hold the cross-process flock for the entire Codex spawn so only one
        # butler process posts the refresh_token to OpenAI.  Fast-path spawns
        # (token has ample lifetime) skip the lock entirely.
        _slow_path = _needs_refresh and real_codex_dir is not None

        async def _run_once_with_lock() -> tuple[
            str | None, list[dict[str, Any]], dict[str, Any] | None
        ]:
            """Invoke Codex once, holding the cross-process lock on slow path."""
            if _slow_path:
                async with _codex_refresh_lock(real_codex_dir):  # type: ignore[arg-type]
                    result = await _run_once()
                # Token is now fresh; mark pre-warm done for this process.
                if _prewarm_key:
                    CodexAdapter._prewarm_done.add(_prewarm_key)
                return result
            return await _run_once()

        try:
            try:
                result_text, tool_calls, usage = await _run_once_with_lock()
            except RuntimeError as exc:
                if not _should_retry_transient_cli_failure(self._last_process_info):
                    raise

                first_info = dict(self._last_process_info) if self._last_process_info else None
                first_exc = exc
                retry_succeeded = False

                for delay in _TRANSIENT_CLI_RETRY_DELAYS:
                    diag = (self._last_process_info or {}).get("stderr", "")
                    diag_short = diag.strip()[:500] if diag else "(no stderr)"
                    logger.warning(
                        "Codex CLI hit transient remote-compaction failure — retrying "
                        "after %.1fs (attempt %d/%d). stderr: %s",
                        delay,
                        subprocess_attempt_count + 1,
                        1 + len(_TRANSIENT_CLI_RETRY_DELAYS),
                        diag_short,
                    )
                    await asyncio.sleep(delay)
                    try:
                        result_text, tool_calls, usage = await _run_once_with_lock()
                    except RuntimeError:
                        if not _should_retry_transient_cli_failure(self._last_process_info):
                            if self._last_process_info:
                                self._last_process_info["retry_attempted"] = True
                                self._last_process_info["retry_succeeded"] = False
                                self._last_process_info["attempt_count"] = subprocess_attempt_count
                            raise
                        continue

                    retry_succeeded = True
                    break

                if not retry_succeeded:
                    if self._last_process_info and first_info:
                        self._last_process_info.update(first_info)
                        self._last_process_info["retry_attempted"] = True
                        self._last_process_info["retry_succeeded"] = False
                        self._last_process_info["attempt_count"] = subprocess_attempt_count
                        self._last_process_info["result_source"] = "first"
                    logger.error(
                        "Codex CLI transient remote-compaction failure persisted after "
                        "%d attempts: %s",
                        subprocess_attempt_count,
                        first_exc,
                    )
                    raise first_exc

                if self._last_process_info:
                    self._last_process_info["retry_attempted"] = True
                    self._last_process_info["retry_succeeded"] = True
                    self._last_process_info["attempt_count"] = subprocess_attempt_count
                    self._last_process_info["result_source"] = "retry"

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
                accepted_retry_result = False

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
                    retry_text, retry_calls, retry_usage = await _run_once_with_lock()
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
                    if not _should_retry_mcp_discovery(
                        mcp_servers=mcp_servers,
                        tool_calls=retry_calls,
                        process_info=self._last_process_info,
                    ):
                        logger.info(
                            "MCP retry path stopped on attempt %d — latest result no longer "
                            "looks like an MCP discovery failure",
                            attempt_count,
                        )
                        accepted_retry_result = True
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
                    if not retry_succeeded and not accepted_retry_result and first_info:
                        self._last_process_info.update(first_info)

                    self._last_process_info["retry_attempted"] = True
                    self._last_process_info["attempt_count"] = subprocess_attempt_count
                    if retry_succeeded:
                        self._last_process_info["mcp_connection_failed"] = False
                        self._last_process_info["retry_succeeded"] = True
                        self._last_process_info["result_source"] = "retry"
                    elif accepted_retry_result:
                        self._last_process_info["mcp_connection_failed"] = False
                        self._last_process_info["retry_succeeded"] = None
                        self._last_process_info["result_source"] = "retry"
                    else:
                        self._last_process_info["mcp_connection_failed"] = True
                        self._last_process_info["retry_succeeded"] = False
                        self._last_process_info["result_source"] = "first"

                if not retry_succeeded and not accepted_retry_result:
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
                    self._last_process_info["attempt_count"] = subprocess_attempt_count

            return result_text, tool_calls, usage
        finally:
            _cleanup_isolated_home_tempdir(tmp_dir_obj, tmp_dir)

    async def _run_codex_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path | None,
        timeout: int,
        cmd_for_log: str,
        mcp_servers: dict[str, Any],
        prompt_input: str,
        *,
        token_path: Path | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Run the Codex CLI subprocess and parse its output.

        When *token_path* is provided and the adapter was constructed with a
        *credential_store*, a fire-and-forget task is scheduled after the
        subprocess exits (success or non-zero) to detect auth.json rotation
        and persist updated tokens to the credential store.  The task is not
        scheduled on ``TimeoutError`` (process was killed mid-flight).
        """
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

            # Schedule auth sync regardless of exit code — the Codex CLI may
            # rotate auth.json before a failure manifests.
            self._schedule_auth_sync(token_path)

            if returncode != 0:
                recovered = _recover_completed_nonzero_exit(stdout, stderr)
                if recovered is not None:
                    logger.warning(
                        "Codex CLI exited with code %d after emitting a completed JSON "
                        "response; using parsed stdout payload",
                        returncode,
                    )
                    self._last_process_info["nonzero_exit_recovered"] = True
                    self._last_process_info["result_source"] = "nonzero_exit_stdout"
                    return recovered
                error_detail = _select_error_detail(stderr, stdout, returncode)
                self._last_process_info["error_detail"] = error_detail
                error_detail = _augment_transport_error_detail(error_detail, mcp_servers)
                log = (
                    logger.warning
                    if _looks_like_transient_cli_failure(error_detail)
                    else logger.error
                )
                log("Codex CLI exited with code %d: %s", returncode, error_detail)
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

    def _schedule_auth_sync(self, token_path: Path | None) -> None:
        """Fire-and-forget: schedule auth.json rotation detection and persist.

        No-op when *token_path* is ``None`` or no credential store is wired.
        Exceptions from the background task are logged with context; they never
        propagate back to the caller.
        """
        if token_path is None or self._credential_store is None:
            return

        from butlers.core.runtimes._codex_auth_sync import check_and_persist_rotation

        asyncio.create_task(
            check_and_persist_rotation(
                token_path,
                self._credential_store,
                butler_name=self._butler_name,
            )
        )

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

            rewritten_url = _prefer_ipv4_loopback(url.strip())
            escaped_url = rewritten_url.replace("\\", "\\\\").replace('"', '\\"')
            toml_lines.append(f"[mcp_servers.{server_name}]")
            toml_lines.append(f'url = "{escaped_url}"')

            normalized_transport, inferred_transport = _resolve_transport_details(
                server_cfg, rewritten_url
            )
            if normalized_transport == "streamable_http" or (
                normalized_transport is None and inferred_transport == "streamable_http"
            ):
                toml_lines.append('transport = "streamable_http"')

            required = server_cfg.get("required", True)
            if isinstance(required, bool):
                toml_lines.append(f"required = {str(required).lower()}")

            startup_timeout = _toml_number(
                server_cfg.get("startup_timeout_sec", _DEFAULT_MCP_STARTUP_TIMEOUT_SECONDS)
            )
            if startup_timeout is not None:
                toml_lines.append(f"startup_timeout_sec = {startup_timeout}")

            tool_timeout = _toml_number(server_cfg.get("tool_timeout_sec"))
            if tool_timeout is not None:
                toml_lines.append(f"tool_timeout_sec = {tool_timeout}")
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
