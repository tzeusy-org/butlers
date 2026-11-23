"""Submit connector ingest envelopes through the shared Switchboard reply contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from butlers.connectors.mcp_client import (
    TRANSIENT_ERROR_STATUS,
    CachedMCPClient,
    TransientToolError,
)

_SUCCESS_STATUSES = frozenset({"accepted", "queued", "duplicate"})


class IngestRejected(RuntimeError):
    """Switchboard did not acknowledge an ingest submission."""

    def __init__(self, message: str, *, is_refusal: bool = False) -> None:
        super().__init__(message)
        self.is_refusal = is_refusal


@dataclass(frozen=True)
class IngestSubmitResult:
    duplicate: bool
    request_id: str | None


async def submit_ingest(client: CachedMCPClient, envelope: dict[str, Any]) -> IngestSubmitResult:
    """Validate one reply while leaving retries and checkpoint decisions to the caller."""
    result = await client.call_tool("ingest", envelope)
    if not isinstance(result, dict):
        raise IngestRejected("Switchboard returned a non-object ingest reply")

    status = result.get("status")
    if status == "error":
        # Server diagnostics can quote envelope data. Callers record this exception.
        raise IngestRejected("Switchboard refused the ingest envelope", is_refusal=True)
    if status == TRANSIENT_ERROR_STATUS:
        # CachedMCPClient already raises this before returning. Preserve the same distinction
        # when another client presents the reply directly.
        raise TransientToolError("Switchboard could not persist the ingest envelope")
    if not isinstance(status, str) or status not in _SUCCESS_STATUSES:
        raise IngestRejected("Switchboard ingest reply has no recognized success status")

    request_id = result.get("request_id")
    return IngestSubmitResult(
        duplicate=status == "duplicate" or result.get("duplicate") is True,
        request_id=request_id if isinstance(request_id, str) else None,
    )
