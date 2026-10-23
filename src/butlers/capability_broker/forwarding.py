"""One signed MCP call to a fixed owner; never a committed-effect oracle.

The mapping comes from trusted control configuration, never a tool argument or
remote redirect. Construction starts no listener and resolves no key. D7 receiver
verification and owning durable receipts remain separate production obligations.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import CallToolResult, InitializeResult

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments

from .contracts import BrokerRefused, Disposition, Reason, Reservation
from .domains import DomainReceipt

_REQUEST_LIMIT = 262144
_REPLY_LIMIT = 1048576
_OPERATION_SECONDS = 20
_CLOSE_SECONDS = 5


def _endpoint(value: str) -> str:
    if type(value) is not str:
        raise ValueError("fixed_domain_endpoint_required")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"/mcp", "/mcp/"}
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1"})
    ):
        raise ValueError("fixed_domain_endpoint_required")
    return value


def _message(raw: bytes, request_id: str) -> dict:
    message = parse_arguments(raw)
    if (
        message.get("jsonrpc") != "2.0"
        or message.get("id") != request_id
        or set(message) != {"jsonrpc", "id", "result"}
        or type(message["result"]) is not dict
    ):
        raise ValueError("invalid_domain_rpc")
    return message["result"]


async def _result(response: httpx.Response, request_id: str) -> dict:
    """Bound raw bytes before JSON/SSE decoding. Never reconnect/resume SSE."""
    if response.status_code != 200:
        raise ValueError("domain_http_refused")
    media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if response.headers.get("content-encoding", "identity") != "identity":
        raise ValueError("domain_encoding_refused")
    if media not in {"application/json", "text/event-stream"}:
        raise ValueError("domain_media_refused")
    data = bytearray()
    async for chunk in response.aiter_raw():
        data.extend(chunk)
        if len(data) > _REPLY_LIMIT:
            raise ValueError("domain_reply_limit")
        if media == "text/event-stream":
            # CRLF and LF are both normal SSE line endings. The whole stream
            # is bounded, including comments/pings/IDs; IDs never cause replay.
            normalized = bytes(data).replace(b"\r\n", b"\n")
            while b"\n\n" in normalized:
                event, normalized = normalized.split(b"\n\n", 1)
                fields = [
                    line[5:].lstrip(b" ")
                    for line in event.split(b"\n")
                    if line.startswith(b"data:")
                ]
                if fields:
                    return _message(b"\n".join(fields), request_id)
    if media == "application/json":
        return _message(bytes(data), request_id)
    raise ValueError("domain_stream_incomplete")


class SignedMCPDomains:
    """Actual Broker Domains.dispatch consumer, with no approval fallback.

    Per-call clients isolate assertions/session IDs across concurrent calls.
    Native MCP names are sent to the exact configured owner. Idempotency-Key
    carries the signed effect_id without modifying the signed raw arguments.
    Only successful reads return content. Effects always remain UNKNOWN until
    an actual owning durable-receipt producer is integrated; model/tool text,
    isError, JSON fields and transport success cannot establish commitment.
    """

    def __init__(self, endpoints: Mapping[str, str]):
        if not isinstance(endpoints, Mapping) or any(
            type(domain) is not str
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+", domain)
            or "__" in domain
            for domain in endpoints
        ):
            raise ValueError("fixed_domain_endpoints_required")
        self.endpoints = MappingProxyType({k: _endpoint(v) for k, v in endpoints.items()})

    async def dispatch(
        self, reservation: Reservation, args: dict, assertion: str, *, idempotency_key: str | None
    ) -> DomainReceipt:
        snapshot = parse_arguments(canonical_bytes(args))
        if (
            type(reservation) is not Reservation
            or reservation.decision.disposition is not Disposition.DISPATCH
            or arguments_digest(snapshot) != reservation.args_digest
            or type(assertion) is not str
            or not 1 <= len(assertion) <= 8192
            or not assertion.isascii()
            or "\r" in assertion
            or "\n" in assertion
            or idempotency_key
            != (
                str(reservation.effect_id)
                if reservation.effect_id is not None
                and reservation.capability.idempotency == "key_required"
                else None
            )
        ):
            raise BrokerRefused(Reason.AUTHENTICATION)
        endpoint = self.endpoints.get(reservation.capability.domain)
        if endpoint is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        operation = asyncio.create_task(
            self._exchange(endpoint, reservation, snapshot, assertion, idempotency_key)
        )
        try:
            result = await asyncio.shield(operation)
        except asyncio.CancelledError as original:
            operation.cancel()  # only this task owns HTTP/session cleanup
            waiter = asyncio.create_task(asyncio.wait({operation}, timeout=_CLOSE_SECONDS))
            while not waiter.done():
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    continue
            _, pending = waiter.result()
            if pending:
                original.add_note("domain_cleanup_incomplete")
                original._broker_forward_task = operation
            elif not operation.cancelled():
                operation.exception()
            raise original
        if reservation.effect_id is not None:
            return DomainReceipt("unknown")
        if result.isError:
            return DomainReceipt("failed")
        return DomainReceipt("committed", value=result.model_dump(mode="json"))

    async def _exchange(self, endpoint, reservation, args, assertion, idempotency_key):
        client = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0, http1=True, http2=False),
            timeout=httpx.Timeout(10, connect=3, write=3, pool=1),
        )
        headers = {
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
            "X-Butlers-Assertion": assertion,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        session_id = None
        deadline = asyncio.get_running_loop().time() + _OPERATION_SECONDS

        async def request(method, params):
            nonlocal session_id
            rpc_id = str(uuid4())
            encoded = canonical_bytes(
                {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
            )
            if len(encoded) > _REQUEST_LIMIT:
                raise ValueError("domain_request_limit")
            async with client.stream(
                "POST",
                endpoint,
                content=encoded,
                headers={**headers, "Content-Type": "application/json"},
            ) as r:
                result = await _result(r, rpc_id)
                if method == "initialize":
                    session_id = r.headers.get("Mcp-Session-Id")
                    if session_id is not None:
                        if not re.fullmatch(r"[\x21-\x7e]{1,256}", session_id):
                            raise ValueError("invalid_domain_session")
                        headers["Mcp-Session-Id"] = session_id
                return result

        try:
            async with asyncio.timeout_at(deadline):
                initialized = InitializeResult.model_validate(
                    await request(
                        "initialize",
                        {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "butlers-capability-broker", "version": "1"},
                        },
                    ),
                    strict=True,
                )
                if initialized.protocolVersion not in SUPPORTED_PROTOCOL_VERSIONS:
                    raise ValueError("domain_protocol_refused")
                headers["MCP-Protocol-Version"] = initialized.protocolVersion
                async with client.stream(
                    "POST",
                    endpoint,
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                ) as r:
                    if r.status_code != 202:
                        raise ValueError("domain_notification_refused")
                result = CallToolResult.model_validate(
                    await request(
                        "tools/call",
                        {
                            "name": reservation.capability.name,
                            "arguments": args,
                        },
                    ),
                    strict=True,
                )
                # A stateful session is scoped to this one call, not a reusable
                # invocation/auth channel. DELETE never retransmits tools/call.
                if session_id is not None:
                    async with client.stream("DELETE", endpoint, headers=headers) as r:
                        if r.status_code not in {200, 204, 405}:
                            raise ValueError("domain_session_close_refused")
                return result
        finally:
            cleanup = asyncio.create_task(client.aclose())
            waiter = asyncio.create_task(asyncio.wait({cleanup}, timeout=_CLOSE_SECONDS))
            while not waiter.done():
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    continue
            _, pending = waiter.result()
            if pending:
                error = TimeoutError("domain_client_cleanup_incomplete")
                error._broker_cleanup_task = cleanup
                raise error
            cleanup.result()

    async def park(self, *args, **kwargs):
        # There is no ordinary MCP or generic receipt escape from private A2.
        raise BrokerRefused(Reason.UNAVAILABLE)
