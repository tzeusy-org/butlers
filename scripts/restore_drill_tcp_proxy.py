"""Uncredentialed TCP relay for the restore-drill executor's internal network.

The credentialed executor can reach this process only through an internal
Compose network.  This relay has no password or shared application database
settings; it accepts a required resolved IPv4 PostgreSQL endpoint so the
supported launchers' host firewall can constrain its sole external route.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LISTEN_HOST = "0.0.0.0"
_COPY_CHUNK_BYTES = 64 * 1024
_IDLE_CHECK_MAX_S = 1.0
_WRITER_CLOSE_TIMEOUT_S = 1.0
_CANONICAL_PORT = re.compile(r"^[1-9][0-9]{0,4}$")
_NON_REMOTE_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(network)
    for network in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.31.196.0/24",
        "192.52.193.0/24",
        "192.88.99.0/24",
        "192.175.48.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


@dataclass(frozen=True)
class RestoreDrillProxyConfig:
    """The relay's deliberately small, non-secret external route."""

    db_host: str
    db_port: int


@dataclass(frozen=True)
class RestoreDrillProxyLimits:
    """Fixed fail-closed bounds for the relay's intentionally tiny workload.

    The executor keeps one asyncpg pool connection and may run one PostgreSQL
    client-tool connection for its single restore lifecycle, so two concurrent
    relay clients are sufficient. Rejection happens before an upstream dial;
    it is not a queue. The two-hour idle limit spans the executor's hourly
    check cadence, while the six-hour absolute deadline exceeds the bounded
    thirty-minute restore command timeout without permitting an endless stream.
    """

    max_active_clients: int = 2
    connect_timeout_s: float = 10.0
    idle_timeout_s: float = 2 * 60 * 60
    session_timeout_s: float = 6 * 60 * 60

    def __post_init__(self) -> None:
        if self.max_active_clients < 1:
            raise ValueError("max_active_clients must be positive")
        if min(self.connect_timeout_s, self.idle_timeout_s, self.session_timeout_s) <= 0:
            raise ValueError("relay timeouts must be positive")


DEFAULT_RESTORE_DRILL_PROXY_LIMITS = RestoreDrillProxyLimits()


def _required_ipv4(name: str) -> str:
    value = os.environ.get(name, "")
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"{name} must be a remote IPv4 address") from exc
    if any(parsed in network for network in _NON_REMOTE_IPV4_NETWORKS):
        raise ValueError(f"{name} must be a remote IPv4 address")
    return str(parsed)


def _required_port(name: str) -> int:
    value = os.environ.get(name, "")
    if not _CANONICAL_PORT.fullmatch(value):
        raise ValueError(f"{name} must use canonical decimal 1..65535")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must use canonical decimal 1..65535")
    return port


def load_restore_drill_proxy_config() -> RestoreDrillProxyConfig:
    """Read only the resolved endpoint supplied by the protected launcher."""
    return RestoreDrillProxyConfig(
        db_host=_required_ipv4("RESTORE_DRILL_PROXY_DB_HOST"),
        db_port=_required_port("RESTORE_DRILL_PROXY_DB_PORT"),
    )


def _abort_writer(writer: asyncio.StreamWriter) -> None:
    """Force close a peer whose graceful flush cannot complete safely."""
    try:
        writer.transport.abort()
    except (AttributeError, ConnectionError, OSError):
        # ``StreamWriter.transport`` is public on asyncio's implementation,
        # but a concurrently closed transport can reject abort harmlessly.
        return


def _consume_close_waiter(waiter: asyncio.Future[None]) -> None:
    """Consume a detached close result after forced transport shutdown."""
    if waiter.cancelled():
        return
    try:
        waiter.result()
    except (ConnectionError, OSError):
        return
    except Exception:  # pragma: no cover - defensive for asyncio transports
        logger.debug("restore-drill relay forced close raised", exc_info=True)


def _detach_close_waiter(waiter: asyncio.Future[None]) -> None:
    """Avoid an unobserved task after an abort while never delaying cleanup."""
    if waiter.done():
        _consume_close_waiter(waiter)
    else:
        waiter.add_done_callback(_consume_close_waiter)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Bound graceful close, then abort so a non-reading peer cannot pin cleanup."""
    writer.close()
    # Shield the protocol's shared close waiter. ``wait_for`` otherwise
    # cancels it on grace timeout, so a subsequent transport abort cannot
    # deliver its normal close notification.
    close_waiter = asyncio.ensure_future(writer.wait_closed())
    try:
        await asyncio.wait_for(asyncio.shield(close_waiter), timeout=_WRITER_CLOSE_TIMEOUT_S)
        return
    except asyncio.CancelledError:
        _abort_writer(writer)
        _detach_close_waiter(close_waiter)
        raise
    except (TimeoutError, ConnectionError, OSError):
        _abort_writer(writer)
        # The transport has already been abortively closed; do not retain this
        # relay handler waiting on an implementation-specific close callback.
        _detach_close_waiter(close_waiter)
        return


async def _close_writers(*writers: asyncio.StreamWriter | None) -> None:
    """Close every side without allowing a buffered peer to pin relay cleanup."""
    unique_writers = tuple(
        {id(writer): writer for writer in writers if writer is not None}.values()
    )
    if unique_writers:
        await asyncio.gather(
            *(_close_writer(writer) for writer in unique_writers), return_exceptions=True
        )


async def _copy_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    last_activity: list[float],
) -> None:
    """Copy one raw TLS direction while recording any completed byte movement."""
    while chunk := await reader.read(_COPY_CHUNK_BYTES):
        writer.write(chunk)
        await writer.drain()
        last_activity[0] = asyncio.get_running_loop().time()


async def _relay_streams(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
    *,
    idle_timeout_s: float,
) -> None:
    """Relay both directions until EOF, error, or a shared idle deadline."""
    loop = asyncio.get_running_loop()
    last_activity = [loop.time()]
    copy_tasks = {
        asyncio.create_task(
            _copy_stream(client_reader, remote_writer, last_activity=last_activity)
        ),
        asyncio.create_task(
            _copy_stream(remote_reader, client_writer, last_activity=last_activity)
        ),
    }
    try:
        while copy_tasks:
            remaining_idle_s = idle_timeout_s - (loop.time() - last_activity[0])
            if remaining_idle_s <= 0:
                raise TimeoutError("restore-drill relay session was idle")
            done, _pending = await asyncio.wait(
                copy_tasks,
                timeout=min(_IDLE_CHECK_MAX_S, remaining_idle_s),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                for task in done:
                    task.result()
                return
    finally:
        for task in copy_tasks:
            task.cancel()
        await asyncio.gather(*copy_tasks, return_exceptions=True)


async def relay_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    config: RestoreDrillProxyConfig,
    limits: RestoreDrillProxyLimits = DEFAULT_RESTORE_DRILL_PROXY_LIMITS,
) -> None:
    """Pass one raw PostgreSQL/TLS stream to the fixed remote endpoint."""
    remote_writer: asyncio.StreamWriter | None = None
    try:
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(config.db_host, config.db_port),
                timeout=limits.connect_timeout_s,
            )
        except TimeoutError:
            logger.warning("restore-drill relay connect deadline expired")
            return
        except OSError:
            logger.warning(
                "restore-drill relay could not connect to its configured PostgreSQL endpoint"
            )
            return

        try:
            await asyncio.wait_for(
                _relay_streams(
                    client_reader,
                    client_writer,
                    remote_reader,
                    remote_writer,
                    idle_timeout_s=limits.idle_timeout_s,
                ),
                timeout=limits.session_timeout_s,
            )
        except TimeoutError:
            logger.warning("restore-drill relay session deadline expired")
        except (ConnectionError, OSError):
            logger.warning("restore-drill relay stream closed with an I/O error")
    finally:
        await _close_writers(client_writer, remote_writer)


class RestoreDrillRelay:
    """Admission and shutdown controller around bounded raw relay sessions."""

    def __init__(self, *, config: RestoreDrillProxyConfig, limits: RestoreDrillProxyLimits) -> None:
        self._config = config
        self._limits = limits
        self._admission = asyncio.BoundedSemaphore(limits.max_active_clients)
        self._active_handlers: set[asyncio.Task[None]] = set()
        self._closing = False

    @property
    def active_client_count(self) -> int:
        """Expose active-session count for shutdown assertions and diagnostics."""
        return len(self._active_handlers)

    async def handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        """Reject overload before it can queue a socket or create an outbound dial."""
        if self._closing or self._admission.locked():
            await _close_writers(client_writer)
            return

        await self._admission.acquire()
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio server callbacks always run in a task
            self._admission.release()
            await _close_writers(client_writer)
            raise RuntimeError("restore-drill relay handler has no task")
        self._active_handlers.add(task)
        try:
            await relay_connection(
                client_reader,
                client_writer,
                config=self._config,
                limits=self._limits,
            )
        finally:
            self._active_handlers.discard(task)
            self._admission.release()

    async def shutdown(self) -> None:
        """Stop future dials and await cancellation-safe cleanup of active handlers."""
        self._closing = True
        active_handlers = tuple(self._active_handlers)
        for task in active_handlers:
            task.cancel()
        if active_handlers:
            await asyncio.gather(*active_handlers, return_exceptions=True)


async def run_restore_drill_proxy(
    config: RestoreDrillProxyConfig,
    *,
    limits: RestoreDrillProxyLimits = DEFAULT_RESTORE_DRILL_PROXY_LIMITS,
) -> None:
    """Serve the internal executor at its configured PostgreSQL port only."""
    relay = RestoreDrillRelay(config=config, limits=limits)
    server = await asyncio.start_server(
        relay.handle_client,
        host=_LISTEN_HOST,
        port=config.db_port,
        backlog=limits.max_active_clients,
    )
    try:
        # Do not await Server.serve_forever() here. On cancellation it waits
        # for the server transport to close before it returns, which can leave
        # an idle accepted client holding the listener open until its relay
        # deadline. Owning the cancellation wait lets us cancel handlers
        # before awaiting listener shutdown.
        await asyncio.Event().wait()
    finally:
        # Stop accepts before cancelling handlers, so no queued callback can
        # race shutdown into an outbound connection attempt.
        server.close()
        await relay.shutdown()
        await server.wait_closed()


def main() -> None:
    """Fail closed before opening a listener when the relay route is invalid."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = load_restore_drill_proxy_config()
    asyncio.run(run_restore_drill_proxy(config))


if __name__ == "__main__":  # pragma: no cover - exercised by the container entrypoint
    main()
