"""The broker's HTTP forwarder to owning domains (RFC 0038 D7, D12).

``HttpForwarder.dispatch`` sends one ``tools/call`` to the owning domain's MCP
endpoint with exactly one ``X-Butlers-Assertion`` header, which the domain's
verification middleware checks before coercion (``verification``). The domain
sees the unnamespaced tool name and the exact arguments the assertion digests.

Receipt mapping (D12):

* the session could not be opened, so the call was provably never sent:
  ``not_received`` (the broker's one transport retry);
* the domain returned a result: ``committed``;
* the domain returned an error result, including a refused assertion:
  ``failed``;
* anything after the call was sent (timeout, broken stream) propagates, and
  the broker records the effect ``unknown``; it is never retried here.

Stamped output is never trusted here: no domain reports its stamp account yet
(``stamped_owner_untainted``, ``stamped_writers``), so every stamped read joins
the unknown label (D6, S1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from .assertions import ASSERTION_HEADER
from .contracts import BrokerRefused, Reason, Reservation
from .domains import DomainReceipt

IDEMPOTENCY_HEADER = "X-Butlers-Idempotency-Key"


class HttpForwarder:
    """Forward broker calls to each domain's MCP endpoint; a trusted constructor input."""

    def __init__(
        self,
        endpoints: Mapping[str, str],
        *,
        httpx_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._endpoints = dict(endpoints)
        self._factory = httpx_client_factory

    async def dispatch(
        self,
        reservation: Reservation,
        args: dict[str, Any],
        assertion: str,
        *,
        idempotency_key: str | None,
    ) -> DomainReceipt:
        capability = reservation.capability
        url = self._endpoints.get(capability.domain)
        if url is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        headers = {ASSERTION_HEADER: assertion}
        if idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        transport = StreamableHttpTransport(
            url, headers=headers, httpx_client_factory=self._factory
        )
        client = Client(transport)
        try:
            await client.__aenter__()
        except Exception:
            # Initialization failed before any tools/call was sent.
            return DomainReceipt("not_received")
        try:
            result = await client.call_tool_mcp(capability.name, args)
        finally:
            await client.__aexit__(None, None, None)
        if result.isError:
            return DomainReceipt("failed")
        return DomainReceipt("committed", value=result.structuredContent)
