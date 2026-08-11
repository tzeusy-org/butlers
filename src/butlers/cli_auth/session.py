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
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Protocol

from butlers.cli_auth.registry import CLIAuthProviderDef
from butlers.cli_auth.sandbox import (
    DashboardCLIAuthSandbox,
    DeviceAuthSandboxHandle,
    SandboxedChildProcess,
    SandboxUnavailableError,
    dashboard_cli_auth_sandbox,
)

logger = logging.getLogger(__name__)


class DeviceAuthSuccessCallback(Protocol):
    """Persist exactly the child bytes validated by the sandbox handle."""

    def __call__(
        self,
        provider: CLIAuthProviderDef,
        *,
        staged_output: bytes,
    ) -> Awaitable[bool | None]: ...


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
    # A Codex callback returns ``False`` only when the device-auth result was
    # not durably saved through the explicit system-global authority.
    on_success: DeviceAuthSuccessCallback | None = field(default=None, repr=False)

    sandbox: DashboardCLIAuthSandbox = field(
        default_factory=dashboard_cli_auth_sandbox,
        repr=False,
    )

    _process: SandboxedChildProcess | None = field(default=None, repr=False)
    _sandbox_handle: DeviceAuthSandboxHandle | None = field(default=None, repr=False)
    _stdout_buffer: str = field(default="", repr=False)
    _success_observed: bool = field(default=False, repr=False)
    _timeout_fenced: bool = field(default=False, repr=False)
    _started_at: float = field(default_factory=time.monotonic, repr=False)
    _reader_task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]
    _timeout_task: asyncio.Task | None = field(default=None, repr=False)  # type: ignore[type-arg]
    _done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def start(self) -> None:
        """Spawn the CLI login subprocess and begin reading stdout."""
        logger.info("CLI auth session %s: starting %s", self.id, self.provider.name)

        if not await self._prepare_persistence_authority_before_launch():
            return

        try:
            self._sandbox_handle = await self.sandbox.launch_device_auth(self.provider)
        except SandboxUnavailableError:
            self.state = "failed"
            self.message = "CLI authentication sandbox is unavailable."
            logger.warning("CLI auth session %s: sandbox unavailable", self.id)
            self._done_event.set()
            return

        self._process = self._sandbox_handle.process

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._timeout_task = asyncio.create_task(self._watch_timeout())

    async def _prepare_persistence_authority_before_launch(self) -> bool:
        """Fail closed before child spawn when its authority fence is unavailable."""
        if self.on_success is None:
            self.state = "failed"
            self.message = "Credential authority unavailable."
            logger.warning("CLI auth session %s: no persistence authority callback", self.id)
            self._done_event.set()
            return False

        callback_type = type(self.on_success)
        if not getattr(callback_type, "requires_prelaunch_prepare", False):
            # Plain callbacks remain useful for isolated session tests and do
            # not represent an application credential authority.  All
            # production factories return the explicit preparable object.
            return True

        prepare = getattr(self.on_success, "prepare_for_device_auth", None)
        if not callable(prepare):
            self.state = "failed"
            self.message = "Credential authority unavailable."
            logger.warning("CLI auth session %s: persistence authority is malformed", self.id)
            self._done_event.set()
            return False

        try:
            prepared = await prepare(self.provider)
        except Exception:
            prepared = False

        if prepared is True:
            return True

        self.state = "failed"
        self.message = "Credential authority unavailable."
        logger.warning("CLI auth session %s: persistence authority is unavailable", self.id)
        self._done_event.set()
        return False

    async def _read_stdout(self) -> None:
        """Own cancellation-safe session completion around all child phases."""
        try:
            await self._read_stdout_until_terminal()
        except asyncio.CancelledError:
            # Cancellation may arrive while stdout is open, during PID1
            # finalization, or while persistence awaits.  In every phase the
            # shared handle owns domain termination before this task yields.
            await self._terminate_sandbox_handle()
            if self.state not in ("expired", "failed"):
                self.state = "failed"
                self.message = "CLI authentication was cancelled."
            self._done_event.set()
            raise
        except ValueError:
            # StreamReader raises ValueError when untrusted stdout exceeds its
            # configured line limit without a separator.  Treat it exactly as
            # a malformed child receipt: do not expose its text, terminate the
            # sandbox handle, and make the session terminal without calling
            # persistence.
            await self._terminate_sandbox_handle()
            self.state = "failed"
            self.message = "CLI authentication output was invalid."
            logger.warning("CLI auth session %s: sandboxed stdout was invalid", self.id)
            self._done_event.set()

    async def _read_stdout_until_terminal(self) -> None:
        """Read sandboxed provider output and finalize its containment handle."""
        assert self._process is not None
        assert isinstance(self._process.stdout, asyncio.StreamReader)

        while True:
            raw = await self._process.stdout.readline()
            if not raw:
                break
            line = _strip_ansi(raw.decode(errors="replace"))
            self._stdout_buffer += line
            self._parse_line(line)

        # A matching provider success line is only a provisional receipt.  The
        # sandbox handle—not this session—must terminate namespace PID1 and
        # wait for its direct Bubblewrap child before staged output can make
        # this session terminal.  Waiting here would reverse that ordering.
        if not self._success_observed and self.state != "expired":
            self.state = "failed"
            self.message = "CLI authentication did not report success."
            logger.warning("CLI auth session %s: no sandboxed success receipt", self.id)

        staged_output: bytes | None = None
        if self._sandbox_handle is None:
            self.state = "failed"
            self.message = "CLI authentication sandbox did not return a containment handle."
        else:
            try:
                staged_output = await self._sandbox_handle.finalize(
                    succeeded=self._success_observed and self.state != "expired"
                )
            except Exception:
                self.state = "failed"
                self.message = "CLI authentication sandbox cleanup failed."
                logger.warning("CLI auth session %s: sandbox cleanup failed safely", self.id)

        # Fire post-success callback only after the sandbox has terminated the
        # complete PID namespace and exposed validated staged bytes.  At this
        # point timeout no longer owns the outcome: its purpose is to bound a
        # live child domain, not to interrupt a fenced credential commit.
        if self._success_observed and self.state != "expired" and staged_output is not None:
            self._fence_timeout_after_containment()

        if self._success_observed and self.state != "expired" and staged_output is None:
            self.state = "failed"
            self.message = "CLI authentication result could not be validated safely."
            logger.warning("CLI auth session %s: sandbox output validation failed", self.id)
        elif self._success_observed and self.state != "expired" and self.on_success is None:
            self.state = "failed"
            self.message = "Authentication was not saved to the credential authority."
            logger.warning("CLI auth session %s: no persistence callback", self.id)
        elif self._success_observed and self.state != "expired" and self.on_success is not None:
            try:
                persisted = await self.on_success(self.provider, staged_output=staged_output)
                if persisted is False:
                    # A parsed provider receipt is provisional.  Every
                    # device-auth provider needs its explicit authority write
                    # to confirm the session; only Codex has a more specific
                    # system-global authority message.
                    self.state = "failed"
                    if self.provider.name == "codex":
                        self.message = "Codex authentication was not saved to the system authority."
                        logger.warning(
                            "CLI auth session %s: Codex authority persistence failed safely",
                            self.id,
                        )
                    else:
                        self.message = "Authentication was not saved to the credential authority."
                        logger.warning(
                            "CLI auth session %s: persistence did not confirm authority safely",
                            self.id,
                        )
            except Exception:
                # Callback exceptions can retain staged provider diagnostics.
                # Never log a traceback or exception text from any provider.
                if self.provider.name == "codex":
                    self.state = "failed"
                    self.message = "Codex authentication was not saved to the system authority."
                    logger.warning(
                        "CLI auth session %s: Codex on_success callback failed safely",
                        self.id,
                    )
                else:
                    self.state = "failed"
                    self.message = "Authentication was not saved to the credential authority."
                    logger.warning(
                        "CLI auth session %s: on_success callback failed safely",
                        self.id,
                    )
            else:
                if self.state != "failed":
                    self.state = "success"
                    self.message = "Authentication successful."
                    logger.info("CLI auth session %s: sandboxed result persisted", self.id)

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
                # Device codes are short-lived credentials. They are returned
                # only through the session API to the initiating dashboard,
                # never copied into a log sink.
                logger.info("CLI auth session %s: parsed device code", self.id)

        if self.provider.success_pattern.search(line):
            self._success_observed = True
            self.message = "Finalizing authentication safely."
            logger.info("CLI auth session %s: provisional success detected in stdout", self.id)

    async def _watch_timeout(self) -> None:
        """Cancel the session if it exceeds the provider timeout."""
        try:
            await asyncio.sleep(self.provider.timeout_seconds)
        except asyncio.CancelledError:
            return

        if not self._done_event.is_set() and not self._timeout_fenced and self.state != "failed":
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
        await self._terminate_sandbox_handle()

        for task in (self._reader_task, self._timeout_task):
            if task is not None and not task.done():
                task.cancel()

        self._done_event.set()

    def _fence_timeout_after_containment(self) -> None:
        """Stop the watchdog before persistence can cross its old deadline."""
        self._timeout_fenced = True
        task = self._timeout_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _terminate_sandbox_handle(self) -> None:
        """Let the handle own cleanup even when this task is being cancelled."""
        if self._sandbox_handle is None:
            return
        cleanup_task = asyncio.create_task(self._sandbox_handle.terminate())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            # A second cancellation does not cancel the cleanup task.  It will
            # retain the UID lease on any failure rather than permit reuse.
            logger.warning(
                "CLI auth session %s: sandbox cleanup continues after cancellation",
                self.id,
            )
            raise
        except Exception:
            logger.warning("CLI auth session %s: sandbox termination failed safely", self.id)

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
