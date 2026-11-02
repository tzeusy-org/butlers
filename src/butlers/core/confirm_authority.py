"""Opt-in authenticated MCP composition for dark signed-confirm services.

Reuses the existing kernel-peer transport, never approval tokens or executor state.
Normal startup does not install this topology: the canonical goal-runtime switch owns
its lifetime and hands producer clients only to trusted daemon code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastmcp import FastMCP

from butlers.core.approval_delivery_authority import (
    ProtectedApprovalMCP,
    protected_approval_principal,
)
from butlers.core.confirm_delivery import terminal_text
from butlers.core.confirm_models import ConfirmPrompt
from butlers.core.confirm_service import ConfirmService, SinkRegistration


@dataclass(frozen=True)
class ConfirmSource:
    """A code-owned source verifier and optional deterministic domain application.

    prepare reads owning-domain state/recipient authority; data is never authority.
    It returns either a registered sink reference or verified goal delivery reference.
    Non-owner delivery authorization remains the source's normal approved-effect check.
    """

    prepare: Callable[[str, dict], Awaitable[tuple[ConfirmPrompt, str]]]
    sink_name: str | None = None
    values: frozenset[str] = frozenset()
    apply: Callable[[UUID, str, str], Awaitable[dict]] | None = None


class ConfirmProducer:
    def __init__(self, endpoint):
        self._endpoint = endpoint

    async def request(self, key: str, data: dict) -> dict:
        return await self._endpoint.call("confirm.request", {"key": key, "data": data})


class ConfirmAuthorityTopology:
    def __init__(
        self,
        service: ConfirmService,
        *,
        sources: Mapping[str, ConfirmSource],
        socket_directory: Path,
        delivery=None,
        api_app=None,
    ):
        self.service = service
        self._sources = dict(sources)
        self._directory = socket_directory
        self._stack = AsyncExitStack()
        self._producers = {}
        self._notify_bindings = []
        self._active = False
        self._used = False
        self._api_attached = False
        self.delivery, self.api_app = delivery, api_app
        self._worker = None
        self._stop = asyncio.Event()

    def producer(self, name: str) -> ConfirmProducer:
        if not self._active:
            raise RuntimeError("confirm_runtime_unavailable")
        return self._producers[name]

    def attach_notify(self, daemon, name: str):
        """Install only inside the trusted canonical runtime composition.

        The source verifier must resolve this session correlation to its actual
        invocation/effect and reload route/authority. No payload is proof.
        """
        from butlers.core.goal_admission import digest

        producer = self.producer(name)
        if getattr(daemon, "_confirm_notify", None) is not None:
            raise ValueError("confirm notify already composed")

        async def request(payload):
            session = payload.get("runtime_session_id")
            if not session:
                return {"status": "refused", "reason": "confirm_invocation_unavailable"}
            normalized = {**payload, "runtime_session_id": str(session)}
            key = digest([str(session), normalized])
            return await producer.request(key, normalized)

        daemon._confirm_notify = request
        self._notify_bindings.append((daemon, request))

    def _path(self):
        return self._directory / f"cf-{uuid4().hex[:12]}.sock"

    async def __aenter__(self):
        if self._used or self.service._sinks:
            raise ValueError("confirm topology requires an unused closed registry")
        self._used = True
        try:
            for name, source in self._sources.items():
                await self._register(name, source)
            if self.delivery is not None:
                await self._register_messenger()
            if self.api_app is not None:
                if getattr(self.api_app.state, "confirm_service", None) is not None:
                    raise ValueError("confirm API already composed")
                self.api_app.state.confirm_service = self.service
                self._api_attached = True
            self._active = True
            if self.delivery is not None:
                self._worker = asyncio.create_task(run_confirm_worker(self.delivery, self._stop))
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def _register(self, name, source):
        if source.sink_name is not None:
            if source.apply is None or not source.values or source.sink_name in self.service._sinks:
                raise ValueError("invalid or duplicate confirm sink registration")
            sink_mcp = FastMCP(f"{name}-confirm-application")
            audience = f"{name}:confirm-application"

            @sink_mcp.tool(name="confirm.apply")
            async def apply(confirm_id: UUID, ref: str, value: str):
                if protected_approval_principal(audience=audience) != "switchboard":
                    raise ValueError("confirm_application_unauthenticated")
                if value not in source.values:
                    raise ValueError("confirm_option_unregistered")
                return await source.apply(confirm_id, ref, value)

            sink_endpoint = await self._stack.enter_async_context(
                ProtectedApprovalMCP(
                    sink_mcp, issuer="switchboard", audience=audience, socket_path=self._path()
                )
            )

            async def dispatch(confirm_id, ref, value):
                return await sink_endpoint.call(
                    "confirm.apply", {"confirm_id": str(confirm_id), "ref": ref, "value": value}
                )

            self.service._sinks[source.sink_name] = SinkRegistration(name, source.values, dispatch)

        request_mcp = FastMCP(f"{name}-confirm-producer")

        @request_mcp.tool(name="confirm.request")
        async def request(key: str, data: dict):
            if protected_approval_principal(audience="switchboard:confirm") != name:
                raise ValueError("confirm_producer_unauthenticated")
            # The owning source reloads its state and recipient, rather than trusting JSON.
            prompt, reference = await source.prepare(key, data)
            row = await self.service.create(
                name,
                key,
                prompt,
                **(
                    {"sink": source.sink_name, "sink_ref": reference}
                    if source.sink_name is not None
                    else {"goal_delivery_ref": reference}
                ),
            )
            return {"status": "accepted", "confirm_id": str(row["id"])}

        endpoint = await self._stack.enter_async_context(
            ProtectedApprovalMCP(
                request_mcp, issuer=name, audience="switchboard:confirm", socket_path=self._path()
            )
        )
        self._producers[name] = ConfirmProducer(endpoint)

    async def _register_messenger(self):
        delivery = self.delivery
        if delivery.service is not self.service:
            raise ValueError("confirm delivery belongs to another service")
        send, edit, allowed = delivery.send, delivery.edit, delivery.allowed
        messenger = FastMCP("messenger-confirms")

        async def stored(confirm_id):
            if protected_approval_principal(audience="messenger:confirm") != "switchboard":
                raise ValueError("confirm_messenger_unauthenticated")
            row = await self.service.get(confirm_id)
            if row is None:
                raise ValueError("confirm_unavailable")
            return row

        @messenger.tool(name="confirm.send")
        async def send_stored(confirm_id: UUID):
            row = await stored(confirm_id)
            return await send(row, self.service.keyboard(row))

        @messenger.tool(name="confirm.edit")
        async def edit_stored(confirm_id: UUID):
            row = await stored(confirm_id)
            return {"edited": await edit(row, terminal_text(row))}

        @messenger.tool(name="confirm.allowed")
        async def allowed_stored(confirm_id: UUID):
            return {"allowed": await allowed(await stored(confirm_id))}

        endpoint = await self._stack.enter_async_context(
            ProtectedApprovalMCP(
                messenger,
                issuer="switchboard",
                audience="messenger:confirm",
                socket_path=self._path(),
            )
        )

        async def protected_send(row, keyboard):
            return await endpoint.call("confirm.send", {"confirm_id": str(row["id"])})

        async def protected_edit(row, text):
            return (await endpoint.call("confirm.edit", {"confirm_id": str(row["id"])}))["edited"]

        async def protected_allowed(row):
            return (await endpoint.call("confirm.allowed", {"confirm_id": str(row["id"])}))[
                "allowed"
            ]

        delivery.send, delivery.edit, delivery.allowed = (
            protected_send,
            protected_edit,
            protected_allowed,
        )
        self._original_delivery = (send, edit, allowed)

    async def __aexit__(self, *exc):
        self._active = False
        self._stop.set()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        if self._api_attached:
            self.api_app.state.confirm_service = None
            self._api_attached = False
        for daemon, callback in self._notify_bindings:
            if getattr(daemon, "_confirm_notify", None) is callback:
                del daemon._confirm_notify
        self._notify_bindings.clear()
        self._producers.clear()
        await self._stack.aclose()
        self.service._sinks.clear()
        if hasattr(self, "_original_delivery"):
            self.delivery.send, self.delivery.edit, self.delivery.allowed = self._original_delivery


async def run_confirm_worker(delivery, stop: asyncio.Event):
    """Finite bounded ticks; only the trusted switch composition starts this worker."""
    while not stop.is_set():
        try:
            await delivery.cycle()
        except Exception:
            # No source content/credential/driver errors go into a generic runtime log.
            import logging

            logging.getLogger(__name__).warning("Confirm worker cycle unavailable")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
