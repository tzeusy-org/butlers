"""Go bridge sidecar lifecycle manager.

Manages the whatsapp-bridge Go binary as a child subprocess, providing:
- Subprocess startup via asyncio.create_subprocess_exec()
- stdout/stderr capture forwarded to Python logging
- Health polling via the bridge's /status endpoint
- Restart with jittered exponential backoff on unexpected exits
- Exit-code-aware shutdown classification
- Graceful shutdown: POST /disconnect, 5-second wait, SIGTERM fallback

Exit code semantics (defined by the bridge binary):
    0 — clean shutdown (no restart)
    1 — pairing timeout (no restart)
    2 — session invalidated (no restart; re-pair needed)
    other — unexpected crash (restart with backoff)
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exit-code classification
# ---------------------------------------------------------------------------

_EXIT_CLEAN = 0  # graceful /disconnect exit
_EXIT_PAIR_TIMEOUT = 1  # QR-pairing timed out
_EXIT_SESSION_INVALID = 2  # whatsmeow invalidated the session

# ---------------------------------------------------------------------------
# Backoff parameters
# ---------------------------------------------------------------------------

_BACKOFF_INITIAL_S = 5.0
_BACKOFF_MAX_S = 300.0
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_JITTER_FACTOR = 0.25  # ±25 % jitter

# ---------------------------------------------------------------------------
# Shutdown parameters
# ---------------------------------------------------------------------------

_GRACEFUL_SHUTDOWN_TIMEOUT_S = 5.0
_DISCONNECT_ENDPOINT = "/disconnect"
_STATUS_ENDPOINT = "/status"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BridgeConfig:
    """Configuration for the Go bridge subprocess."""

    binary: str = "whatsapp-bridge"
    """Path (or bare name) of the bridge binary."""

    args: list[str] = field(default_factory=list)
    """Additional CLI arguments forwarded verbatim to the binary."""

    env: dict[str, str] = field(default_factory=dict)
    """Extra environment variables injected into the bridge subprocess.

    Use this to pass credentials (e.g. ``WA_BRIDGE_DSN``) without exposing
    them in the process argument list (visible in ``ps``/``/proc``).

    Values here are merged on top of the inherited process environment.
    """

    bridge_socket: str = "/tmp/wa-bridge.sock"
    """Unix domain socket path on which the bridge listens."""

    health_poll_interval_s: float = 30.0
    """Seconds between /status health-poll requests."""

    startup_timeout_s: float = 30.0
    """Maximum seconds to wait for the bridge to report 'connected' on startup."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _jittered_backoff(attempt: int) -> float:
    """Return the next backoff delay (seconds) with jitter.

    Computes ``initial * multiplier^attempt`` clamped to max,
    then applies ±jitter_factor uniform noise.
    """
    base = min(_BACKOFF_INITIAL_S * (_BACKOFF_MULTIPLIER**attempt), _BACKOFF_MAX_S)
    jitter = base * _BACKOFF_JITTER_FACTOR
    return base + random.uniform(-jitter, jitter)  # noqa: S311


async def _http_post_unix(socket_path: str, path: str) -> dict[str, Any]:
    """Issue a plain HTTP POST to *path* on the bridge Unix socket.

    Returns the parsed JSON body.  Raises on network or parse errors.
    Note: HTTP-level error status codes (4xx/5xx) are not raised; callers must
    inspect the returned dict for error fields from the bridge.
    """
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        request = (
            f"POST {path} HTTP/1.0\r\n"
            f"Host: localhost\r\n"
            f"Content-Length: 0\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        raw = await reader.read()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return _parse_http_json(raw)


async def _http_post_unix_with_body(
    socket_path: str, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Issue a plain HTTP POST with a JSON body to *path* on the bridge Unix socket.

    Returns the parsed JSON body.  Raises on network or parse errors.
    Note: HTTP-level error status codes (4xx/5xx) are not raised; callers must
    inspect the returned dict for error fields from the bridge.
    """
    import json

    encoded_body = json.dumps(body).encode()
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        request = (
            f"POST {path} HTTP/1.0\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(encoded_body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode())
        writer.write(encoded_body)
        await writer.drain()

        raw = await reader.read()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return _parse_http_json(raw)


async def _http_get_unix(socket_path: str, path: str) -> dict[str, Any]:
    """Issue a plain HTTP GET to *path* on the bridge Unix socket."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        request = f"GET {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()

        raw = await reader.read()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return _parse_http_json(raw)


def _parse_http_json(raw: bytes) -> dict[str, Any]:
    """Extract and parse the JSON body from a raw HTTP/1.x response."""
    import json

    # Split headers from body
    sep = b"\r\n\r\n"
    idx = raw.find(sep)
    if idx == -1:
        raise ValueError("Malformed HTTP response: no header/body separator")
    body_bytes = raw[idx + len(sep) :]
    try:
        return json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bridge returned non-JSON body: {body_bytes!r}") from exc


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class BridgeSubprocessManager:
    """Manages the Go whatsapp-bridge sidecar subprocess lifecycle.

    Usage::

        cfg = BridgeConfig(
            args=["--listen", f"unix://{socket}"],
            env={"WA_BRIDGE_DSN": dsn},
        )
        mgr = BridgeSubprocessManager(cfg)
        await mgr.start()          # raises RuntimeError if binary not found
        # … run forever …
        await mgr.stop()           # graceful shutdown

    The manager exposes :attr:`is_degraded` (True when a no-restart exit code
    was observed) and :attr:`degraded_reason` for the caller's use.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

        # Degraded mode (no-restart exit codes)
        self._degraded = False
        self._degraded_reason: str | None = None

        # Restart backoff state
        self._restart_attempt = 0
        self._last_start_time: float = 0.0

        # Shutdown signal (set by stop())
        self._stopping = False

        # Event that resolves once the bridge is "connected" (for startup wait)
        self._connected_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_degraded(self) -> bool:
        """True if the bridge exited with a no-restart code."""
        return self._degraded

    @property
    def degraded_reason(self) -> str | None:
        """Human-readable reason for degraded mode, or None."""
        return self._degraded_reason

    @property
    def is_running(self) -> bool:
        """True if the subprocess is currently alive."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Start the bridge subprocess and wait until it reports 'connected'.

        Raises:
            RuntimeError: If the binary is not found in $PATH.
            TimeoutError: If the bridge does not reach 'connected' within
                ``config.startup_timeout_s`` seconds.
        """
        self._stopping = False
        self._degraded = False
        self._degraded_reason = None

        await self._spawn()

        # Start the supervisor loop in the background
        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="bridge-monitor")

        # Wait for the bridge to become connected by actively polling /status.
        # The health poll loop only starts after startup; a dedicated startup
        # poller sets _connected_event as soon as the bridge is ready.
        logger.info(
            "Waiting up to %.0fs for bridge to reach 'connected' state …",
            self._config.startup_timeout_s,
        )
        startup_poll_task = asyncio.create_task(
            self._startup_poll_loop(), name="bridge-startup-poll"
        )
        try:
            await asyncio.wait_for(
                self._connected_event.wait(),
                timeout=self._config.startup_timeout_s,
            )
        except TimeoutError:
            logger.error(
                "Bridge did not reach 'connected' within %.0fs",
                self._config.startup_timeout_s,
            )
            raise
        finally:
            startup_poll_task.cancel()
            try:
                await startup_poll_task
            except (asyncio.CancelledError, Exception):
                pass

        # Start background health polling
        self._health_task = asyncio.create_task(self._health_poll_loop(), name="bridge-health-poll")
        logger.info("Bridge startup complete — health polling started")

    async def stop(self) -> None:
        """Gracefully shut down the bridge.

        1. POST /disconnect to the bridge HTTP API.
        2. Wait up to 5 s for the process to exit.
        3. SIGTERM fallback if still alive.
        """
        logger.info("BridgeSubprocessManager: initiating graceful shutdown")
        self._stopping = True

        # Cancel background tasks
        for task in (self._health_task, self._monitor_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._health_task = None
        self._monitor_task = None

        # Attempt graceful HTTP disconnect
        if self._process is not None and self._process.returncode is None:
            await self._graceful_disconnect()

        # Cancel stdout/stderr log drainers
        for task in (self._stdout_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._stdout_task = None
        self._stderr_task = None
        self._process = None
        logger.info("BridgeSubprocessManager: shutdown complete")

    # ------------------------------------------------------------------
    # Private helpers — process management
    # ------------------------------------------------------------------

    async def _spawn(self) -> None:
        """Launch the bridge binary as an asyncio subprocess."""
        import os
        import shutil

        binary_path = shutil.which(self._config.binary)
        if binary_path is None:
            raise RuntimeError(
                "whatsapp-bridge binary not found. Build with EXTRAS=whatsapp or install manually."
            )

        cmd = [binary_path, *self._config.args]
        # Log only the binary name to avoid leaking sensitive flags (e.g. WA_BRIDGE_DSN).
        logger.info("Spawning bridge: %s (%d extra args)", binary_path, len(self._config.args))
        self._last_start_time = time.monotonic()

        # Build subprocess environment: inherit current env, then overlay config.env.
        # Credentials (e.g. WA_BRIDGE_DSN) are passed here rather than in args so
        # they are not visible in ps / /proc/<pid>/cmdline output.
        subprocess_env: dict[str, str] | None = None
        if self._config.env:
            subprocess_env = {**os.environ, **self._config.env}

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=subprocess_env,
        )
        logger.info("Bridge PID %d started", self._process.pid)

        # Start log drainers for stdout and stderr
        assert self._process.stdout is not None
        assert self._process.stderr is not None

        self._stdout_task = asyncio.create_task(
            self._drain_pipe(self._process.stdout, logging.INFO, "bridge[stdout]"),
            name="bridge-stdout",
        )
        self._stderr_task = asyncio.create_task(
            self._drain_pipe(self._process.stderr, logging.WARNING, "bridge[stderr]"),
            name="bridge-stderr",
        )

    async def _drain_pipe(
        self,
        pipe: asyncio.StreamReader,
        level: int,
        prefix: str,
    ) -> None:
        """Read lines from *pipe* and forward them to the Python logger."""
        try:
            async for line in pipe:
                text = line.decode(errors="replace").rstrip()
                if text:
                    logger.log(level, "%s: %s", prefix, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Pipe drainer exited (%s)", prefix)

    async def _graceful_disconnect(self) -> None:
        """POST /disconnect, then wait up to 5 s for the process to exit."""
        assert self._process is not None

        try:
            logger.info("Sending POST /disconnect to bridge …")
            await asyncio.wait_for(
                _http_post_unix(self._config.bridge_socket, _DISCONNECT_ENDPOINT),
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning("POST /disconnect failed (will SIGTERM): %s", exc)

        # Wait for the process to terminate
        try:
            await asyncio.wait_for(self._process.wait(), timeout=_GRACEFUL_SHUTDOWN_TIMEOUT_S)
            logger.info("Bridge exited cleanly (rc=%d)", self._process.returncode)
            return
        except TimeoutError:
            pass

        # SIGTERM fallback
        logger.warning(
            "Bridge PID %d did not exit within %.0fs — sending SIGTERM",
            self._process.pid,
            _GRACEFUL_SHUTDOWN_TIMEOUT_S,
        )
        try:
            self._process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except TimeoutError:
            logger.error(
                "Bridge PID %d did not respond to SIGTERM — sending SIGKILL",
                self._process.pid,
            )
            try:
                self._process.send_signal(signal.SIGKILL)
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                logger.error(
                    "Bridge PID %d did not respond to SIGKILL — process may be orphaned",
                    self._process.pid,
                )
        except ProcessLookupError:
            pass  # Process already exited

    # ------------------------------------------------------------------
    # Private helpers — supervisor loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        """Watch the subprocess and restart on unexpected exits."""
        while not self._stopping:
            if self._process is None:
                break

            rc = await self._process.wait()

            # Cancel stdout/stderr drainers for the old process
            for task in (self._stdout_task, self._stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            if self._stopping:
                logger.debug("Bridge exited during shutdown (rc=%d) — not restarting", rc)
                break

            should_restart = self._classify_exit(rc)
            if not should_restart:
                break

            # Compute and wait for backoff delay
            delay = _jittered_backoff(self._restart_attempt)
            logger.info(
                "Restarting bridge in %.1fs (attempt %d) …",
                delay,
                self._restart_attempt + 1,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

            if self._stopping:
                break

            self._restart_attempt += 1
            self._connected_event.clear()

            try:
                await self._spawn()
            except RuntimeError:
                logger.exception("Failed to respawn bridge binary — giving up")
                self._set_degraded("Bridge binary not found during restart")
                break

            # Wait until the bridge is healthy again before counting this as
            # a successful restart (reset the backoff counter).
            try:
                await asyncio.wait_for(
                    self._connected_event.wait(),
                    timeout=self._config.startup_timeout_s,
                )
                logger.info("Bridge reconnected after restart")
                self._restart_attempt = 0
            except TimeoutError:
                logger.warning(
                    "Bridge did not reconnect within %.0fs after restart (attempt %d)",
                    self._config.startup_timeout_s,
                    self._restart_attempt,
                )
                # The next monitor_loop iteration will trigger again when
                # the process exits (or health-poll degrades it).

        logger.debug("Bridge monitor loop exiting")

    def _classify_exit(self, rc: int) -> bool:
        """Decide whether to restart based on the exit code.

        Returns True if the bridge should be restarted, False otherwise.
        """
        if rc == _EXIT_CLEAN:
            logger.info("Bridge exited cleanly (rc=0) — no restart")
            return False

        if rc == _EXIT_PAIR_TIMEOUT:
            logger.warning(
                "Bridge exited with pairing timeout (rc=1) — no restart; re-pair required"
            )
            self._set_degraded("Pairing timeout — re-pair required")
            return False

        if rc == _EXIT_SESSION_INVALID:
            logger.warning("Bridge session invalidated (rc=2) — no restart; re-pair required")
            self._set_degraded("Session invalidated — re-pair required")
            return False

        logger.error("Bridge exited unexpectedly (rc=%d) — scheduling restart", rc)
        return True

    def _set_degraded(self, reason: str) -> None:
        """Enter degraded mode with the given human-readable reason."""
        self._degraded = True
        self._degraded_reason = reason
        logger.warning("Bridge entering degraded mode: %s", reason)

    # ------------------------------------------------------------------
    # Private helpers — health polling
    # ------------------------------------------------------------------

    _STARTUP_POLL_INTERVAL_S: float = 1.0
    """How often (seconds) to poll /status during the startup wait."""

    async def _startup_poll_loop(self) -> None:
        """Poll /status repeatedly until _connected_event is set or cancelled.

        This loop runs only during ``start()`` — it drives the initial
        handshake that sets ``_connected_event``.  Once the event is set,
        ``start()`` cancels this task and hands over to ``_health_poll_loop``.
        """
        while True:
            try:
                await asyncio.sleep(self._STARTUP_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break

            if self._connected_event.is_set():
                break

            # Use _poll_status but avoid marking transient 'connecting' as
            # degraded — during startup the bridge has not yet finished
            # establishing the WhatsApp session so connecting is expected.
            if self._process is None or self._process.returncode is not None:
                break

            try:
                data = await asyncio.wait_for(
                    _http_get_unix(self._config.bridge_socket, _STATUS_ENDPOINT),
                    timeout=10.0,
                )
                state = data.get("state", "unknown")
                logger.debug("Bridge startup /status: state=%s", state)
                if state == "connected":
                    self._connected_event.set()
                    break
                # 'connecting' is an expected transient state during startup —
                # keep polling rather than entering degraded mode.
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Bridge startup /status poll failed (retrying): %s", exc)

    async def _health_poll_loop(self) -> None:
        """Periodically poll /status and update connected/degraded state."""
        while not self._stopping:
            try:
                await asyncio.sleep(self._config.health_poll_interval_s)
            except asyncio.CancelledError:
                break

            if self._stopping:
                break

            await self._poll_status()

        logger.debug("Bridge health poll loop exiting")

    async def _poll_status(self) -> None:
        """Issue a single /status poll and act on the result."""
        if self._process is None or self._process.returncode is not None:
            # Process is not running; monitor loop handles restart
            return

        try:
            data = await asyncio.wait_for(
                _http_get_unix(self._config.bridge_socket, _STATUS_ENDPOINT),
                timeout=10.0,
            )
        except TimeoutError:
            logger.error("Bridge /status poll timed out — entering degraded mode")
            self._set_degraded("Health poll timed out")
            return
        except Exception as exc:
            logger.error("Bridge /status poll failed: %s — entering degraded mode", exc)
            self._set_degraded(f"Health poll failed: {exc}")
            return

        state = data.get("state", "unknown")
        logger.debug("Bridge /status: state=%s", state)

        if state == "connected":
            # Clear degraded if we were previously in it due to a transient issue
            if self._degraded:
                logger.info("Bridge recovered — clearing degraded mode")
                self._degraded = False
                self._degraded_reason = None
            self._connected_event.set()
        elif state in ("disconnected", "connecting"):
            # Post-startup: 'connecting' means the bridge dropped its session
            # and is attempting to reconnect.  Treat this as degraded until it
            # recovers to 'connected' — the next poll may clear it automatically.
            logger.warning("Bridge /status reports '%s' — entering degraded mode", state)
            self._set_degraded(f"Bridge status: {state}")
        elif state == "pair_required":
            logger.warning("Bridge requires pairing — entering degraded mode")
            self._set_degraded("pair_required")
        else:
            logger.warning("Unrecognised bridge state: %r", state)
