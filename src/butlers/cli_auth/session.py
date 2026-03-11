"""CLI auth session manager.

Manages subprocess lifecycle for device-code auth flows. Each session
spawns a CLI login command, parses stdout for the device URL and code,
and tracks state transitions until success, failure, or timeout.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from butlers.cli_auth.registry import CLIAuthProviderDef

logger = logging.getLogger(__name__)


@dataclass
class CLIAuthSession:
    """Tracks one in-flight CLI auth flow."""

    id: str
    provider: CLIAuthProviderDef
    state: str = "starting"  # starting | awaiting_auth | success | failed | expired
    auth_url: str | None = None
    device_code: str | None = None
    message: str | None = None

    # Optional callback invoked after successful auth (e.g. persist token to DB).
    on_success: Callable[[CLIAuthProviderDef], Awaitable[None]] | None = field(
        default=None, repr=False
    )

    _process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _stdout_buffer: str = field(default="", repr=False)
    _started_at: float = field(default_factory=time.monotonic, repr=False)
    _reader_task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]
    _timeout_task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]
    _done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def start(self) -> None:
        """Spawn the CLI login subprocess and begin reading stdout."""
        logger.info("CLI auth session %s: starting %s", self.id, self.provider.name)

        self._process = await asyncio.create_subprocess_exec(
            *self.provider.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._timeout_task = asyncio.create_task(self._watch_timeout())

    async def _read_stdout(self) -> None:
        """Read subprocess stdout line-by-line, parsing for patterns."""
        assert self._process is not None
        assert self._process.stdout is not None

        try:
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                line = _strip_ansi(raw.decode(errors="replace"))
                self._stdout_buffer += line
                self._parse_line(line)
        except asyncio.CancelledError:
            return

        # Process exited — determine final state
        returncode = await self._process.wait()

        if self.state == "success":
            pass  # Already set by _parse_line
        elif returncode == 0 and self.provider.is_authenticated():
            self.state = "success"
            self.message = "Authentication successful."
            logger.info("CLI auth session %s: success (exit 0 + token exists)", self.id)
        elif self.state != "expired":
            self.state = "failed"
            self.message = f"Process exited with code {returncode}."
            logger.warning("CLI auth session %s: failed (exit %d)", self.id, returncode)

        # Fire post-success callback (e.g. persist token to DB)
        if self.state == "success" and self.on_success is not None:
            try:
                await self.on_success(self.provider)
            except Exception:
                logger.exception("CLI auth session %s: on_success callback failed", self.id)

        self._done_event.set()

    def _parse_line(self, line: str) -> None:
        """Check a stdout line against provider patterns."""
        if self.auth_url is None:
            m = self.provider.url_pattern.search(line)
            if m:
                self.auth_url = m.group(1)
                logger.info("CLI auth session %s: parsed auth URL", self.id)

        if self.device_code is None:
            m = self.provider.code_pattern.search(line)
            if m:
                self.device_code = m.group(1)
                self.state = "awaiting_auth"
                self.message = "Waiting for authorization."
                logger.info(
                    "CLI auth session %s: parsed device code %s",
                    self.id,
                    self.device_code,
                )

        if self.provider.success_pattern.search(line):
            self.state = "success"
            self.message = "Authentication successful."
            logger.info("CLI auth session %s: success detected in stdout", self.id)

    async def _watch_timeout(self) -> None:
        """Cancel the session if it exceeds the provider timeout."""
        try:
            await asyncio.sleep(self.provider.timeout_seconds)
        except asyncio.CancelledError:
            return

        if self.state not in ("success", "failed"):
            self.state = "expired"
            self.message = "Authorization timed out."
            logger.warning(
                "CLI auth session %s: timed out after %ds",
                self.id,
                self.provider.timeout_seconds,
            )
            await self.kill()

    async def kill(self) -> None:
        """Terminate the subprocess and cancel background tasks."""
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except TimeoutError:
                    self._process.kill()
            except ProcessLookupError:
                pass

        for task in (self._reader_task, self._timeout_task):
            if task is not None and not task.done():
                task.cancel()

        self._done_event.set()

    async def wait(self, timeout: float = 5.0) -> None:
        """Wait for the session to reach a terminal state."""
        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
        except TimeoutError:
            pass

    @property
    def is_terminal(self) -> bool:
        return self.state in ("success", "failed", "expired")


# ---------------------------------------------------------------------------
# Session store (process-local, like the OAuth CSRF state store)
# ---------------------------------------------------------------------------

_sessions: dict[str, CLIAuthSession] = {}

# Limit concurrent + retained sessions to prevent resource leaks.
_MAX_SESSIONS = 20


def get_session(session_id: str) -> CLIAuthSession | None:
    return _sessions.get(session_id)


def store_session(session: CLIAuthSession) -> None:
    _evict_old_sessions()
    _sessions[session.id] = session


def list_sessions() -> list[CLIAuthSession]:
    return list(_sessions.values())


def _evict_old_sessions() -> None:
    """Remove terminal sessions beyond the cap, oldest first."""
    if len(_sessions) < _MAX_SESSIONS:
        return

    terminal = [(sid, s) for sid, s in _sessions.items() if s.is_terminal]
    terminal.sort(key=lambda pair: pair[1]._started_at)

    while len(_sessions) >= _MAX_SESSIONS and terminal:
        sid, _ = terminal.pop(0)
        del _sessions[sid]


def clear_sessions() -> None:
    """Clear all sessions. Used in tests."""
    _sessions.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z]|\[0-9]+D|\[0-9]+K|\[J")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return _ANSI_RE.sub("", text)
