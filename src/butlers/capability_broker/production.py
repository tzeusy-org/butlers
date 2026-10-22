"""Default-OFF daemon boundary and bounded private transport resource ownership.

No admitted production profile or committed A2 producer exists in this slice.
Typed configuration cannot grant that authority. The internal resource owner is
software infrastructure, not a bypass for enabling production operations.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack


class ProductionTransportUnavailable(RuntimeError):
    """Closed refusal before private material, factories or listeners."""


async def start_private_transport(daemon) -> None:
    settings = getattr(daemon.config, "capability_broker_transport", None)
    if settings is None:
        return
    from butlers.config import CapabilityBrokerTransportConfig

    if type(settings) is not CapabilityBrokerTransportConfig:
        raise ProductionTransportUnavailable("invalid_private_transport_configuration")
    if not settings.enabled:
        return
    # Neither config, a callback, KEY_ABSENT_STAGE nor a caller-supplied flag
    # establishes the missing owning producer and isolated startup authority.
    raise ProductionTransportUnavailable("private_transport_prerequisites_unavailable")


class _TransportOwner:
    """One task owns entry and exit of the actual MCP lifespan.

    This internal acquisition seam accepts transport software factories only;
    production startup cannot reach it until its separate prerequisites exist.
    Closing retains one five-second absolute bound and every unfinished task.
    A timeout or failed cleanup is never reported as successful settlement.
    """

    def __init__(self):
        self.client = None
        self.server = None
        self.closed = False
        self._stop = asyncio.Event()
        self._ready = asyncio.get_running_loop().create_future()
        self._deadline = None
        self._task = None
        self._waiter = None
        self._client_close = None

    @classmethod
    async def open(
        cls, *, client_config=None, context_factory=None, server_config=None, server_factory=None
    ):
        owner = cls()
        owner._task = asyncio.create_task(
            owner._run(client_config, context_factory, server_config, server_factory)
        )
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(owner._ready)
        except BaseException as original:
            owner._stop.set()
            if not owner._ready.done():
                owner._task.cancel()
            try:
                await owner.aclose()
            except BaseException as cleanup:
                original.add_note("private_cleanup_failed:" + type(cleanup).__name__)
                # Preserve an unsettled acquisition handle with the original
                # error. Losing the caller cannot turn timeout into closure.
                original._private_transport_resources = owner
            if owner._ready.done() and not owner._ready.cancelled():
                owner._ready.exception()
            raise
        return owner

    async def _run(self, client_config, context_factory, server_config, server_factory):
        stack = AsyncExitStack()
        entry_error = None
        announced = False
        try:
            from .transport.client import build_client
            from .transport.server import build_server

            self.client = build_client(client_config, context_factory)
            self.server = build_server(server_config, server_factory)
            if self.server is not None:
                await stack.enter_async_context(self.server.run())
            self._ready.set_result(None)
            announced = True
            await self._stop.wait()
        except BaseException as exc:
            entry_error = exc
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            if self._deadline is None:
                self._deadline = asyncio.get_running_loop().time() + 5
            deadline = self._deadline
            # Client shutdown is independent of the same-task MCP lifespan.
            # Start both native cleanups together; do not add two fresh graces.
            if self.client is not None:
                self._client_close = asyncio.create_task(self.client.aclose())
            failure = None
            try:
                async with asyncio.timeout_at(deadline):
                    await stack.aclose()
            except BaseException as exc:
                failure = exc
            if self._client_close is not None:
                _, pending = await asyncio.wait(
                    [self._client_close],
                    timeout=max(0, deadline - asyncio.get_running_loop().time()),
                )
                if pending:
                    failure = failure or TimeoutError("private_client_cleanup_incomplete")
                else:
                    try:
                        self._client_close.result()
                    except BaseException as exc:
                        failure = failure or exc
            if failure is not None:
                raise failure
            self.closed = True
            if entry_error is not None and announced and not self._stop.is_set():
                raise entry_error

    async def aclose(self):
        if self._waiter is None:
            self._deadline = asyncio.get_running_loop().time() + 5
            self._stop.set()
            self._waiter = asyncio.create_task(
                asyncio.wait(
                    [self._task], timeout=max(0, self._deadline - asyncio.get_running_loop().time())
                )
            )
        cancellation = None
        while not self._waiter.done():
            try:
                await asyncio.shield(self._waiter)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        _, pending = self._waiter.result()
        if pending:
            raise TimeoutError("private_owner_cleanup_incomplete")
        self._task.result()
        if not self.closed:
            raise RuntimeError("private_owner_not_settled")
        if cancellation is not None:
            raise cancellation


async def close_private_transport(daemon) -> None:
    owner = vars(daemon).get("_capability_transport_owner")
    if owner is None:
        return
    task = vars(daemon).get("_capability_transport_cleanup")
    if task is None:
        task = asyncio.create_task(owner.aclose())
        daemon._capability_transport_cleanup = task
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
    task.result()  # failure remains retained and visible on repeated shutdown
    daemon._capability_transport_owner = None
    if cancellation is not None:
        raise cancellation
