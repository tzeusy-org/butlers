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
* a conditional call (D13 replan exception) whose result is that condition's
  declined status: ``declined``, nothing written, and the broker parks it;
* anything after the call was sent (timeout, broken stream) propagates, and
  the broker records the effect ``unknown``; it is never retried here.

A committed receipt carries the domain's D6 stamp account
(``stamped_owner_untainted``, ``stamped_writers``) only when the result's
``_meta`` holds a report naming this exact call: the reservation's capability,
``call_id`` and ``args_digest`` (``core.stamp_account.parse_report``). A
missing, malformed or mismatched report is no account, and a stamped read then
joins the unknown label (D6, S1). Tool content is never read for a stamp.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from fastmcp.client.transports import StreamableHttpTransport

from butlers.core.stamp_account import parse_report
from butlers.mcp_liveness import LivenessClient as Client

from .assertions import ASSERTION_HEADER, DECLINED_STATUS
from .contracts import BrokerRefused, Reason, Reservation
from .domains import DeliveryReport, DomainReceipt

IDEMPOTENCY_HEADER = "X-Butlers-Idempotency-Key"


_REPORT_FIELDS = frozenset(
    {"transport", "endpoint", "conversation", "locator", "prompt_expires_at"}
)


def parse_delivery_report(value: Any) -> DeliveryReport | None:
    """A committed result's additive ``delivery`` block, if it is exactly well formed.

    The block is the domain's claim about where its message landed. Anything else (a missing
    key, an extra key, a non-string, an unparsable or naive expiry) is no report, so a domain
    that does not report behaves exactly as before. The broker, not this parser, decides which
    owner identity the report may bind to.
    """
    if (
        not isinstance(value, Mapping)
        or not {"transport", "endpoint", "conversation", "locator"} <= set(value)
        or set(value) - _REPORT_FIELDS
    ):
        return None
    fields = [value[k] for k in ("transport", "endpoint", "conversation", "locator")]
    if not all(isinstance(f, str) and f for f in fields):
        return None
    expires = value.get("prompt_expires_at")
    if expires is not None:
        try:
            expires = datetime.fromisoformat(expires) if isinstance(expires, str) else None
        except ValueError:
            expires = None
        if expires is None or expires.tzinfo is None:
            return DeliveryReport(*fields)
    return DeliveryReport(*fields, prompt_expires_at=expires)


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
        owner_untainted, writers = parse_report(
            result.meta,
            capability=capability.name,
            call_id=reservation.call_id,
            args_digest=reservation.args_digest,
        )
        content = result.structuredContent
        conditional = reservation.decision.conditional
        if (
            conditional is not None
            and isinstance(content, Mapping)
            and content.get("status") == DECLINED_STATUS.get(conditional)
        ):
            # The domain found the condition unmet and wrote nothing (D13).
            return DomainReceipt("declined")
        return DomainReceipt(
            "committed",
            value=content,
            stamped_owner_untainted=owner_untainted,
            stamped_writers=writers,
            report=parse_delivery_report(content.get("delivery"))
            if isinstance(content, Mapping)
            else None,
        )
