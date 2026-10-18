"""The one place a connector submits an ``ingest.v1`` envelope to Switchboard (bu-ly3lv5.14).

Switchboard's ``ingest`` MCP tool does not raise on a rejected envelope: it returns a normal
``{"status": "error", "error": ...}`` result. A connector that only checks that ``call_tool``
returned would count that rejection as success, and a replay submitter that ignores the result
would mark the buffered row ``replay_complete`` although nothing was persisted.

``submit_ingest`` calls the tool once and raises :class:`IngestRejected` for anything that is not
an accepted outcome. It adds no retry, no dedup and no metrics: callers keep their own
``except`` branches, which already treat an exception as a failed submission (live path metrics
error, replay row ``replay_failed``). ``tests/contracts/test_connector_ingest_submit_guard.py``
fails when a connector calls the ``ingest`` tool anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Statuses Switchboard's ``ingest`` tool returns for an envelope it took responsibility for.
#: A duplicate is reported as ``status="accepted"`` with ``duplicate=True`` today; a literal
#: ``status="duplicate"`` is accepted too.
SUCCESS_STATUSES = frozenset({"accepted", "queued", "duplicate"})


class IngestRejected(RuntimeError):
    """Switchboard did not accept the envelope.

    A ``RuntimeError`` subclass, matching what connectors already raised for a ``status: error``
    reply. ``response`` carries the raw result (``None`` unless it was a dict) for callers that
    must not log it.
    """

    def __init__(self, message: str, *, response: Any = None) -> None:
        super().__init__(message)
        self.response = response if isinstance(response, dict) else None


@dataclass(frozen=True)
class IngestSubmitResult:
    """An accepted submission."""

    status: str
    duplicate: bool
    response: dict[str, Any]

    @property
    def request_id(self) -> Any:
        return self.response.get("request_id")


async def submit_ingest(mcp_client: Any, envelope: dict[str, Any]) -> IngestSubmitResult:
    """Submit ``envelope`` via the ``ingest`` tool; raise :class:`IngestRejected` unless accepted.

    ``accepted``, ``queued`` and ``duplicate`` succeed (``duplicate`` is True for ``status ==
    "duplicate"`` or ``duplicate: true``). An ``error`` status, a missing or unknown status, or a
    non-dict result raises. An exception from ``call_tool`` itself propagates unchanged.
    """
    result = await mcp_client.call_tool("ingest", envelope)
    if not isinstance(result, dict):
        raise IngestRejected(
            f"Switchboard returned a non-dict ingest result ({type(result).__name__})"
        )
    status = result.get("status")
    if status == "error":
        raise IngestRejected(
            f"Ingest tool error: {result.get('error', 'Unknown ingest error')}", response=result
        )
    if not isinstance(status, str) or not status:
        raise IngestRejected("Switchboard ingest result has no status", response=result)
    if status not in SUCCESS_STATUSES:
        raise IngestRejected(
            f"Switchboard ingest result has unexpected status {status!r}", response=result
        )
    return IngestSubmitResult(
        status=status,
        duplicate=status == "duplicate" or result.get("duplicate") is True,
        response=result,
    )
