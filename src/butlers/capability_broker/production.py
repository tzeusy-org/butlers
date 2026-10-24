"""Default-OFF daemon boundary and bounded private transport resource ownership.

Enabled owning startup binds only the schema-local019 repository, actual final
registration/originals and operator verification keys. Configuration and mTLS
alone never authenticate a Grant; every PARK/replay purpose is verified again.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from .transport.material import ProductionTransportUnavailable, material_context


def _has_approvals(daemon):
    # The actual daemon filters failed modules here, after final registration.
    return any(module.name == "approvals" for module in daemon._active_modules)


async def start_private_transport(daemon) -> None:
    await _start_calendar_prepare(daemon)
    settings = getattr(daemon.config, "capability_broker_transport", None)
    if settings is None:
        return
    from butlers.config import CapabilityBrokerTransportConfig

    if type(settings) is not CapabilityBrokerTransportConfig:
        raise ProductionTransportUnavailable("invalid_private_transport_configuration")
    if not settings.enabled:
        return
    # This fixed owning assembly accepts no supplied operation or model policy.
    # SQL role/schema and final registration are checked before material IO.
    from butlers.modules.approvals.goal_dispatch import GoalDispatch

    from .runtime import decode_verification_keys
    from .transport.config import PeerPolicy
    from .transport.server import PrivateApp, PrivateServer

    if (
        settings.role != "owning-service"
        or settings.domain != daemon.config.name
        or not settings.verification_keys
        or not _has_approvals(daemon)
    ):
        raise ProductionTransportUnavailable("private_transport_prerequisites_unavailable")
    keys = decode_verification_keys(settings.verification_keys)
    catalog = daemon.tool_catalog
    handlers = await daemon._broker_owning_originals()
    preparers = {}
    for module in daemon._active_modules:
        from butlers.modules.calendar import CalendarModule

        if type(module) is CalendarModule:
            preparers.update(module.canonical_approval_preparers())
    operation = GoalDispatch(
        daemon.db, settings.domain, catalog, handlers, keys, preparers=preparers
    )
    # Verify the actual configured runtime role and installed019 before binding.
    async with operation.owner._transaction():
        pass
    peer = PeerPolicy(
        settings.peer.role,
        settings.peer.domain,
        frozenset(settings.peer.sans),
        settings.peer.client_certificate,
    )
    if peer.domain != settings.domain:
        raise ProductionTransportUnavailable("private_transport_peer_domain_mismatch")

    def factory(_):
        context = material_context(settings.material, server=True)
        return PrivateServer(
            PrivateApp(settings.domain, operation),
            context,
            peer,
            host=settings.listen_host,
            port=settings.listen_port,
        )

    daemon._capability_transport_owner = await _TransportOwner.open(
        server_config=peer, server_factory=factory
    )


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
    calendar_owner = vars(daemon).get("_calendar_prepare_owner")
    if owner is None and calendar_owner is None:
        return
    task = vars(daemon).get("_capability_transport_cleanup")
    if task is None:

        async def release():
            async with asyncio.timeout(5):
                results = await asyncio.gather(
                    *(item.aclose() for item in (owner, calendar_owner) if item is not None),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

        task = asyncio.create_task(release())
        daemon._capability_transport_cleanup = task
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
    try:
        task.result()  # failure remains retained and visible on repeated shutdown
    except BaseException as failure:
        if cancellation is not None:
            raise cancellation from failure
        raise
    daemon._capability_transport_owner = None
    daemon._calendar_prepare_owner = None
    if cancellation is not None:
        raise cancellation


async def _start_calendar_prepare(daemon):
    settings = getattr(daemon.config, "calendar_owner_prepare", None)
    if settings is None or not settings.enabled:
        return
    from butlers.config import CalendarOwnerPrepareConfig
    from butlers.modules.calendar import CalendarModule

    from .transport.calendar_prepare import CalendarPrepareApp
    from .transport.config import PeerPolicy
    from .transport.server import PrivateServer

    if type(settings) is not CalendarOwnerPrepareConfig or settings.domain != daemon.config.name:
        raise ProductionTransportUnavailable("invalid_calendar_prepare_configuration")
    preparers = [
        module.canonical_approval_preparers().get("calendar_respond")
        for module in daemon._active_modules
        if type(module) is CalendarModule
    ]
    preparers = [item for item in preparers if item is not None]
    if len(preparers) != 1 or not _has_approvals(daemon):
        raise ProductionTransportUnavailable("calendar_prepare_unavailable")
    peer_config = settings.dashboard_peer
    peer = PeerPolicy(peer_config.role, settings.domain, frozenset(peer_config.sans), True)

    def factory(_):
        return PrivateServer(
            CalendarPrepareApp(settings.domain, preparers[0]),
            material_context(settings.service_material, server=True),
            peer,
            host=settings.listen_host,
            port=settings.listen_port,
        )

    daemon._calendar_prepare_owner = await _TransportOwner.open(
        server_config=peer, server_factory=factory
    )
