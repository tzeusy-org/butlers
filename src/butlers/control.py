"""Trusted key-absent Switchboard process, separate from domain orchestration.

This stage preserves the existing service daemon. The second listener has no
MCP server, signer, broker, credential resolver, or goal admission path.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn
from starlette.responses import JSONResponse

KEY_ABSENT_STAGE = "KEY_ABSENT_STAGE"
CONTROL_SESSION_PORT = 41199
_PRIVATE_KEY_NAME = "GOAL_BROKER_SIGNING_KEY"


class StageConfigurationError(RuntimeError):
    """The selected topology cannot prove absence of private configuration."""


def check_private_key_absent(profile: str, *, environment: Mapping[str, str] | None = None) -> None:
    """Check configuration and mount names only; never obtain their values.

    Empty environment assignments and broken/mismatched mounted files are
    forbidden too. A directory entry is sufficient: no file is opened, even
    to determine whether it contains a key. Host provisioning remains trusted.
    """
    if profile != KEY_ABSENT_STAGE:
        raise StageConfigurationError("unsupported control profile")
    environment = os.environ if environment is None else environment
    if any(name.upper().startswith(_PRIVATE_KEY_NAME) for name in environment):
        raise StageConfigurationError("KEY_ABSENT_STAGE: private-key configuration is forbidden")
    try:
        names = Path("/run/secrets").iterdir()
        if any(path.name.upper().startswith(_PRIVATE_KEY_NAME) for path in names):
            raise StageConfigurationError(
                "KEY_ABSENT_STAGE: private-key configuration is forbidden"
            )
    except FileNotFoundError:
        pass
    except OSError:
        raise StageConfigurationError(
            "KEY_ABSENT_STAGE: cannot verify private-key absence"
        ) from None


# Literal per-store queries keep both relations visible to the schema-reach contract.
# Only the metadata column ``secret_key`` is read; no secret value is selected.
_STORED_PRIVATE_KEY_PROBES = (
    (
        "switchboard.butler_secrets",
        "SELECT EXISTS (SELECT 1 FROM switchboard.butler_secrets "
        "WHERE starts_with(UPPER(secret_key), $1::text))",
    ),
    (
        "public.butler_secrets",
        "SELECT EXISTS (SELECT 1 FROM public.butler_secrets "
        "WHERE starts_with(UPPER(secret_key), $1::text))",
    ),
)


async def check_stored_private_key_absent(pool: Any) -> None:
    """Reject forbidden credential metadata without selecting secret values.

    Inspect both possible stores so a local table cannot hide a public one.
    A metadata failure refuses startup; it never tries a credential fallback.
    Tables can be absent on first boot before migrations have created them.
    """
    try:
        for table, query in _STORED_PRIVATE_KEY_PROBES:
            if await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table):
                if await pool.fetchval(query, _PRIVATE_KEY_NAME):
                    raise StageConfigurationError(
                        "KEY_ABSENT_STAGE: private-key configuration is forbidden"
                    )
    except StageConfigurationError:
        raise
    except Exception:
        raise StageConfigurationError(
            "KEY_ABSENT_STAGE: cannot verify private-key absence"
        ) from None


async def session_refusal_app(scope: Any, receive: Any, send: Any) -> None:
    """Serve the one stage health route; refuse every other session request."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    if scope["type"] != "http":
        return
    health = scope["method"] == "GET" and scope["path"] == "/health"
    response = JSONResponse(
        {"status": "unavailable" if health else "refused"}, status_code=503 if health else 404
    )
    await response(scope, receive, send)


class SessionRefusalListener:
    """Own a separate socket and bounded lifecycle for the stage listener."""

    def __init__(self, *, host: str, port: int = CONTROL_SESSION_PORT) -> None:
        self.host = host
        self.port = port
        self.task: asyncio.Task | None = None
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            self.port = sock.getsockname()[1]
            sock.listen(128)
            self._server = uvicorn.Server(
                uvicorn.Config(
                    session_refusal_app,
                    lifespan="off",
                    access_log=False,
                    log_level="warning",
                    timeout_graceful_shutdown=5,
                )
            )
            self.task = asyncio.create_task(self._server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not self._server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("control session listener exited during startup")
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        try:
            if self._server is not None:
                self._server.should_exit = True
            if self.task is not None:
                try:
                    async with asyncio.timeout(6):
                        await asyncio.shield(self.task)
                except TimeoutError:
                    self.task.cancel()
                    try:
                        await self.task
                    except asyncio.CancelledError:
                        pass
        finally:
            if self._socket is not None:
                self._socket.close()


async def run_control(config_dir: Path, *, profile: str, session_host: str) -> None:
    """Run today's service and the refusal listener under one trusted profile.

    Profile selection is a CLI/deployment setting. No request can change it.
    Either listener exiting unexpectedly stops this process; shutdown and any
    partial startup release both sockets and the existing daemon's resources.
    """
    check_private_key_absent(profile)
    from butlers.daemon import ButlerDaemon

    daemon = ButlerDaemon(config_dir, control_profile=profile)
    listener = SessionRefusalListener(host=session_host)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals_installed = False
    waiter = None
    try:
        await daemon.start()
        await listener.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown.set)
        signals_installed = True
        waiter = asyncio.create_task(shutdown.wait())
        completed, _ = await asyncio.wait(
            (waiter, listener.task, daemon._server_task), return_when=asyncio.FIRST_COMPLETED
        )
        if waiter not in completed:
            raise RuntimeError("control listener stopped unexpectedly")
    finally:
        if signals_installed:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
        if waiter is not None:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        try:
            await listener.close()
        finally:
            await daemon.shutdown()
