"""Approval-only MCP authority for the Linux cohosted daemon process.

Kernel peer admission and owning-source presentation admission are independent.
This companion transport never authenticates public TCP MCP, independent
process deployments, or model children. Construction does not start workers.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import inspect
import json
import os
import secrets
import socket
import struct
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.function_tool import FunctionTool
from uvicorn.protocols.http.h11_impl import H11Protocol

from butlers.core.approval_delivery_transport import (
    ApprovalRecoveryRuntime,
    RecoveryAuthorityError,
    TrustedRecoveryContext,
)
from butlers.core.approval_delivery_worker import DeliveryClaim

_PROOF_HEADER = "x-butlers-approval-proof"
_PRINCIPAL_SCOPE = "butlers.approval_peer"
_SWITCHBOARD_AUDIENCE = "switchboard:approval-recovery"
_MESSENGER_AUDIENCE = "messenger:approval-recovery"
_REFUSAL = "Approval recovery authority rejected."


def guard_registered_approval_tool(
    mcp: Any,
    preauthorize: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]],
) -> Callable:
    """Put approval admission outside the actual registration proxy.

    The owned source registration applies this after the generic proxy has
    constructed its FunctionTool. The public registry and companion retain
    that real proxy, but call it only after privileged preauthorization.
    Ordinary traffic reaches the existing instrumentation unchanged.
    """

    def decorate(registered: Any) -> Any:
        # Minimal registration stubs expose only the business function. That
        # function retains its own fail-closed checks; it has no outer proxy.
        if not callable(getattr(mcp, "add_tool", None)) or (
            not isinstance(registered, FunctionTool)
            and getattr(registered, "__fastmcp__", None) is None
        ):
            return registered
        original = registered.fn if isinstance(registered, FunctionTool) else registered
        signature = inspect.signature(original)

        @functools.wraps(original)
        async def admitted(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            refusal = await preauthorize(dict(bound.arguments))
            if refusal is not None:
                return refusal
            result = original(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        if isinstance(registered, FunctionTool):
            tool = registered.model_copy(update={"fn": admitted})
        else:
            tool = FunctionTool.from_function(
                admitted, metadata=getattr(registered, "__fastmcp__", None)
            )
        mcp.add_tool(tool)
        return registered

    return decorate


@dataclass(frozen=True, slots=True)
class _PeerPrincipal:
    issuer: str
    audience: str
    epoch: str
    live: Callable[[], bool] = field(repr=False)


def protected_approval_principal(*, audience: str) -> str | None:
    """Read only server-installed peer provenance, never HTTP identity headers."""
    try:
        principal = get_http_request().scope.get(_PRINCIPAL_SCOPE)
    except RuntimeError:
        return None
    if (
        not isinstance(principal, _PeerPrincipal)
        or principal.audience != audience
        or not principal.live()
    ):
        return None
    return principal.issuer


class ProtectedApprovalMCP:
    """One limited MCP companion, authenticated before HTTP parsing by peer PID.

    The trusted host creates separate issuer/audience bindings. There is no
    caller-selected issuer, allowed-PID override, persistent key, or TCP port.
    A shared UID or socket permission is not an isolation claim.
    """

    def __init__(self, mcp: FastMCP, *, issuer: str, audience: str, socket_path: Path):
        self._mcp = mcp
        self._issuer = issuer
        self._audience = audience
        self._socket_path = socket_path
        self._epoch = str(uuid.uuid4())
        self._active = False
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._socket: socket.socket | None = None
        self._inode: int | None = None

    async def __aenter__(self) -> ProtectedApprovalMCP:
        if not hasattr(socket, "SO_PEERCRED") or self._server is not None:
            raise RecoveryAuthorityError(_REFUSAL)
        if len(os.fsencode(self._socket_path)) >= 104:
            raise RecoveryAuthorityError(_REFUSAL)
        expected_pid = os.getpid()
        principal = _PeerPrincipal(self._issuer, self._audience, self._epoch, lambda: self._active)
        application = self._mcp.http_app(path="/mcp", stateless_http=True)

        async def admitted_app(scope: dict, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                scope = dict(scope)
                scope[_PRINCIPAL_SCOPE] = principal
            await application(scope, receive, send)

        class PeerPIDProtocol(H11Protocol):
            admitted = False

            def connection_made(self, transport: asyncio.Transport) -> None:
                peer_socket = transport.get_extra_info("socket")
                try:
                    if peer_socket.family != socket.AF_UNIX:
                        raise ValueError
                    pid, _uid, _gid = struct.unpack(
                        "3i", peer_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    self.admitted = pid == expected_pid and principal.live()
                except (AttributeError, OSError, ValueError, struct.error):
                    self.admitted = False
                super().connection_made(transport)
                if not self.admitted:
                    transport.close()

            def data_received(self, data: bytes) -> None:
                if self.admitted and principal.live():
                    super().data_received(data)
                else:
                    self.transport.close()

        bound_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # bind refuses an existing path; never unlink another listener.
            bound_socket.bind(str(self._socket_path))
            self._inode = self._socket_path.stat().st_ino
            bound_socket.listen(16)
            self._socket = bound_socket
            self._active = True
            self._server = uvicorn.Server(
                uvicorn.Config(
                    admitted_app,
                    http=PeerPIDProtocol,
                    ws="none",
                    access_log=False,
                    log_level="warning",
                    timeout_graceful_shutdown=2,
                )
            )
            self._task = asyncio.create_task(self._server.serve(sockets=[bound_socket]))
            deadline = time.monotonic() + 5
            while not self._server.started:
                if self._task.done():
                    await self._task
                    raise RecoveryAuthorityError(_REFUSAL)
                if time.monotonic() >= deadline:
                    raise RecoveryAuthorityError(_REFUSAL)
                await asyncio.sleep(0.01)
            return self
        except BaseException:
            bound_socket.close()
            await self.close()
            raise

    async def close(self) -> None:
        """Fence existing connections before closing the owned listener/path."""
        self._active = False
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        if self._socket is not None:
            self._socket.close()
        try:
            if self._inode is not None and self._socket_path.stat().st_ino == self._inode:
                self._socket_path.unlink()
        except FileNotFoundError:
            pass

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def call(
        self, tool: str, arguments: dict[str, Any], *, proof: str | None = None
    ) -> dict[str, Any]:
        """Call only this bootstrap-bound companion over its Unix socket."""
        if not self._active:
            raise RecoveryAuthorityError(_REFUSAL)

        def client_factory(**kwargs: Any) -> httpx.AsyncClient:
            # Never permit proxy/redirect or environment routing to replace the
            # bootstrap endpoint. Authority material is only an HTTP header.
            kwargs["follow_redirects"] = False
            return httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=str(self._socket_path)),
                trust_env=False,
                **kwargs,
            )

        transport = StreamableHttpTransport(
            "http://approval.local/mcp",
            headers={_PROOF_HEADER: proof} if proof is not None else {},
            httpx_client_factory=client_factory,
        )
        async with Client(transport) as client:
            result = await client.call_tool(tool, arguments)
        if not isinstance(result.data, dict):
            raise RecoveryAuthorityError(_REFUSAL)
        return result.data


def _notification_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _Admission:
    context: TrustedRecoveryContext
    claim: DeliveryClaim
    digest: str
    expires_at: float


class SourceApprovalAdmission:
    """Ephemeral proof minted only beside the source's own durable repository.

    No mint operation is registered as a tool. Verification is protected by a
    separate kernel-admitted Switchboard ingress and rechecks the source row.
    """

    def __init__(self, repository: Any, *, owning_schema: str):
        self._repository = repository
        self._schema = owning_schema
        self.epoch = str(uuid.uuid4())
        self._admissions: dict[str, _Admission] = {}
        self._retired = False

    def retire(self) -> None:
        self._retired = True
        self._admissions.clear()

    def revoke(self, proof: str) -> None:
        self._admissions.pop(hashlib.sha256(proof.encode()).hexdigest(), None)

    async def mint(self, claim: DeliveryClaim, payload: dict[str, Any]) -> str:
        from butlers.tools.switchboard.routing.contracts import parse_notify_request

        if self._retired:
            raise RecoveryAuthorityError(_REFUSAL)
        request = parse_notify_request(payload)
        recovery = request.recovery
        if recovery is None or request.origin_butler != self._schema:
            raise RecoveryAuthorityError(_REFUSAL)
        context = TrustedRecoveryContext(
            issuer=self._schema,
            owning_schema=self._schema,
            operation="reconcile" if claim.reconcile_only else "handoff",
            subject_kind=claim.subject_kind,
            subject_key=claim.subject_key,
            presentation_key=claim.presentation_key,
            presentation_generation=claim.presentation_generation,
            presentation_mode=claim.presentation_mode,
        )
        context.validate()
        if request.delivery.intent != "approval_request" or any(
            getattr(recovery, key) != value
            for key, value in context.as_internal_dict().items()
            if key not in {"issuer", "owning_schema"}
        ):
            raise RecoveryAuthorityError(_REFUSAL)
        # Start the monotonic deadline before the database await so query and
        # transport latency cannot extend the durable lease's remaining time.
        started_at = time.monotonic()
        remaining = await self._repository.authorize_transport(claim, owning_schema=self._schema)
        if self._retired or remaining is None or remaining <= 0:
            raise RecoveryAuthorityError(_REFUSAL)
        now = time.monotonic()
        expires_at = started_at + min(10, remaining)
        if expires_at <= now:
            raise RecoveryAuthorityError(_REFUSAL)
        self._admissions = {
            key: value for key, value in self._admissions.items() if value.expires_at > now
        }
        if len(self._admissions) >= 4096:
            raise RecoveryAuthorityError(_REFUSAL)
        proof = secrets.token_urlsafe(32)
        self._admissions[hashlib.sha256(proof.encode()).hexdigest()] = _Admission(
            context, claim, _notification_digest(payload), expires_at
        )
        return proof

    async def verify(self, payload: dict[str, Any], proof: str | None) -> dict[str, Any]:
        """Return a safe attestation only for the exact live source admission."""
        if self._retired or not isinstance(proof, str) or len(proof) > 128:
            return {"allowed": False}
        admission = self._admissions.get(hashlib.sha256(proof.encode()).hexdigest())
        if admission is None or admission.expires_at <= time.monotonic():
            return {"allowed": False}
        try:
            if not hmac.compare_digest(admission.digest, _notification_digest(payload)):
                return {"allowed": False}
            remaining = await self._repository.authorize_transport(
                admission.claim, owning_schema=self._schema
            )
            if (
                remaining is None
                or remaining <= 0
                or self._retired
                or admission.expires_at <= time.monotonic()
                or self._admissions.get(hashlib.sha256(proof.encode()).hexdigest()) is not admission
            ):
                return {"allowed": False}
        except Exception:
            return {"allowed": False}
        return {
            "allowed": True,
            "source_epoch": self.epoch,
            "context": admission.context.as_internal_dict(),
        }

    def verifier_mcp(self) -> FastMCP:
        mcp = FastMCP("approval-source-verifier")

        @mcp.tool()
        async def verify_approval_admission(notify_request: dict[str, Any]) -> dict[str, Any]:
            if protected_approval_principal(audience=f"{self._schema}:approval-verifier") != (
                "switchboard"
            ):
                return {"allowed": False}
            proof = get_http_request().headers.get(_PROOF_HEADER)
            return await self.verify(notify_request, proof)

        return mcp


@dataclass(frozen=True, slots=True)
class SourceApprovalVerifier:
    """Bootstrap-bound verifier endpoint, never a caller-selected source URL."""

    endpoint: ProtectedApprovalMCP
    source_epoch: str

    async def verify(self, payload: dict[str, Any], proof: str | None) -> TrustedRecoveryContext:
        response = await self.endpoint.call(
            "verify_approval_admission", {"notify_request": payload}, proof=proof
        )
        if response.get("allowed") is not True or response.get("source_epoch") != self.source_epoch:
            raise RecoveryAuthorityError(_REFUSAL)
        return TrustedRecoveryContext.from_internal_dict(response["context"])


async def verify_switchboard_recovery(
    module: Any, payload: dict[str, Any]
) -> TrustedRecoveryContext | None:
    issuer = protected_approval_principal(audience=_SWITCHBOARD_AUDIENCE)
    verifiers = getattr(module, "_approval_recovery_verifiers", {})
    verifier = verifiers.get(issuer)
    if issuer is None or not isinstance(verifier, SourceApprovalVerifier):
        return None
    try:
        context = await verifier.verify(payload, get_http_request().headers.get(_PROOF_HEADER))
        if (
            context.issuer != issuer
            or context.owning_schema != issuer
            or protected_approval_principal(audience=_SWITCHBOARD_AUDIENCE) != issuer
            or getattr(module, "_approval_recovery_verifiers", {}).get(issuer) is not verifier
        ):
            return None
        return context
    except Exception:
        return None


class SourceApprovalDispatch:
    """The private source runtime's claim-bound authenticated notify client."""

    def __init__(self, admission: SourceApprovalAdmission, endpoint: ProtectedApprovalMCP):
        self._admission = admission
        self._endpoint = endpoint

    async def __call__(self, claim: DeliveryClaim, payload: dict[str, Any]) -> dict[str, Any]:
        proof = await self._admission.mint(claim, payload)
        return await self._endpoint.call(
            "deliver",
            {"source_butler": payload["origin_butler"], "notify_request": payload},
            proof=proof,
        )


async def approval_companion_mcp(registered: FastMCP, tool_name: str) -> FastMCP:
    """Reuse the actual registered wrapper while exposing only its operation."""
    companion = FastMCP("approval-only")
    companion.add_tool(await registered.get_tool(tool_name))
    return companion


class ApprovalAuthorityTopology:
    """Opt-in trusted-host composition; never called by normal daemon startup.

    Sources keep their admission objects and owning pools. Switchboard receives
    only protected verifier clients, Messenger only its authenticated ingress.
    No worker/admission flag is changed and no runtime is attached to a daemon.
    Callers must close this object before retiring/replacing the owning process.
    """

    def __init__(
        self,
        *,
        sources: Mapping[str, SourceApprovalAdmission],
        switchboard_registered: FastMCP,
        switchboard_module: Any,
        messenger_registered: FastMCP,
        messenger_registry_url: str,
        socket_directory: Path,
    ):
        self._sources = dict(sources)
        if not self._sources or any(name != source._schema for name, source in sources.items()):
            raise RecoveryAuthorityError(_REFUSAL)
        self._switchboard_registered = switchboard_registered
        self._module = switchboard_module
        self._messenger_registered = messenger_registered
        self._messenger_registry_url = messenger_registry_url
        self._socket_directory = socket_directory
        self._stack = AsyncExitStack()
        self._dispatches: dict[str, SourceApprovalDispatch] = {}
        self._verifiers: dict[str, SourceApprovalVerifier] = {}
        self._call: Callable[..., Awaitable[dict[str, Any]]] | None = None

    def _path(self) -> Path:
        return self._socket_directory / f"ap-{uuid.uuid4().hex[:12]}.sock"

    async def __aenter__(self) -> ApprovalAuthorityTopology:
        if getattr(self._module, "_approval_recovery_call", None) is not None or getattr(
            self._module, "_approval_recovery_verifiers", None
        ):
            raise RecoveryAuthorityError(_REFUSAL)
        try:
            messenger = await self._stack.enter_async_context(
                ProtectedApprovalMCP(
                    await approval_companion_mcp(self._messenger_registered, "route.execute"),
                    issuer="switchboard",
                    audience=_MESSENGER_AUDIENCE,
                    socket_path=self._path(),
                )
            )

            async def call_messenger(endpoint: str, tool: str, arguments: dict) -> dict:
                # The registry remains the routing owner. Its resolved target
                # must equal the bootstrap-bound Messenger, never a caller URL.
                if endpoint != self._messenger_registry_url or tool != "route.execute":
                    raise RecoveryAuthorityError(_REFUSAL)
                return await messenger.call(tool, arguments)

            self._call = call_messenger
            for issuer, source in self._sources.items():
                verifier_endpoint = await self._stack.enter_async_context(
                    ProtectedApprovalMCP(
                        source.verifier_mcp(),
                        issuer="switchboard",
                        audience=f"{issuer}:approval-verifier",
                        socket_path=self._path(),
                    )
                )
                self._verifiers[issuer] = SourceApprovalVerifier(verifier_endpoint, source.epoch)
                switchboard = await self._stack.enter_async_context(
                    ProtectedApprovalMCP(
                        await approval_companion_mcp(self._switchboard_registered, "deliver"),
                        issuer=issuer,
                        audience=_SWITCHBOARD_AUDIENCE,
                        socket_path=self._path(),
                    )
                )
                self._dispatches[issuer] = SourceApprovalDispatch(source, switchboard)
            self._module._approval_recovery_verifiers = self._verifiers
            self._module._approval_recovery_call = self._call
            return self
        except BaseException:
            await self.close()
            raise

    def runtime(
        self,
        issuer: str,
        *,
        resolve_owner_recipient: Callable[[], Awaitable[str | None]],
        resolve_callback_secret: Callable[[], Awaitable[str | None]],
    ) -> ApprovalRecoveryRuntime:
        """Return a narrow source runtime without enabling or starting it."""
        dispatch = self._dispatches.get(issuer)
        if dispatch is None:
            raise RecoveryAuthorityError(_REFUSAL)
        return ApprovalRecoveryRuntime(
            source_butler=issuer,
            owning_schema=issuer,
            dispatch=dispatch,
            resolve_owner_recipient=resolve_owner_recipient,
            resolve_callback_secret=resolve_callback_secret,
        )

    async def close(self) -> None:
        for source in self._sources.values():
            source.retire()
        self._dispatches.clear()
        if getattr(self._module, "_approval_recovery_call", None) is self._call:
            self._module._approval_recovery_call = None
        if getattr(self._module, "_approval_recovery_verifiers", None) is self._verifiers:
            self._module._approval_recovery_verifiers = {}
        await self._stack.aclose()

    async def __aexit__(self, *_args: object) -> None:
        await self.close()
