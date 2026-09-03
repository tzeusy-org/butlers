"""Server-attested Finance producer provenance for RFC 0029 adoption."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from butlers.core.tool_call_capture import get_current_runtime_session_routing_context

EXPECTED_SIGNAL_SOURCE_KEY = "expected_signal_source"


@dataclass(frozen=True, slots=True)
class FinanceSignalSource:
    producer: str
    producer_endpoint_identity: str | None = None

    def as_metadata(self) -> dict[str, str | None]:
        return {
            "producer": self.producer,
            "producer_endpoint_identity": self.producer_endpoint_identity,
        }


def sanitized_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy public metadata while dropping the reserved authority field."""
    result = dict(metadata or {})
    result.pop(EXPECTED_SIGNAL_SOURCE_KEY, None)
    return result


def metadata_with_signal_source(
    metadata: Mapping[str, Any] | None,
    source: FinanceSignalSource | None,
) -> dict[str, Any]:
    result = sanitized_metadata(metadata)
    if source is not None:
        result[EXPECTED_SIGNAL_SOURCE_KEY] = source.as_metadata()
    return result


def signal_source_from_metadata(
    metadata: Mapping[str, Any] | str | None,
) -> FinanceSignalSource | None:
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except (TypeError, ValueError):
            return None
        metadata = parsed if isinstance(parsed, Mapping) else None
    raw = (metadata or {}).get(EXPECTED_SIGNAL_SOURCE_KEY)
    if not isinstance(raw, Mapping):
        return None
    producer = raw.get("producer")
    endpoint = raw.get("producer_endpoint_identity")
    if producer == "owner" and endpoint is None:
        return FinanceSignalSource("owner")
    if producer == "connector:gmail" and isinstance(endpoint, str) and endpoint.strip():
        normalized = endpoint.strip()
        if normalized.startswith("gmail:"):
            return FinanceSignalSource("connector:gmail", normalized)
    return None


def resolve_complete_signal_source(
    metadata_rows: list[Mapping[str, Any] | str | None],
) -> FinanceSignalSource | None:
    """Resolve exactly one source across every contributor, or fail closed."""
    if not metadata_rows:
        return None
    sources = [signal_source_from_metadata(metadata) for metadata in metadata_rows]
    if any(source is None for source in sources):
        return None
    distinct = set(sources)
    return next(iter(distinct)) if len(distinct) == 1 else None


async def runtime_signal_source(pool: Any) -> FinanceSignalSource | None:
    """Derive authority from server-held routing context, never tool arguments."""
    context = get_current_runtime_session_routing_context()
    if not isinstance(context, dict):
        return None
    request_context = context.get("request_context")
    if not isinstance(request_context, dict):
        request_context = {}
    endpoint = request_context.get("source_endpoint_identity")
    channel = request_context.get("source_channel")
    if channel in {"email", "gmail"}:
        if isinstance(endpoint, str) and endpoint.strip().startswith("gmail:"):
            return FinanceSignalSource("connector:gmail", endpoint.strip())
        return None

    entity_id = context.get("source_entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        return None
    try:
        is_owner = await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.entities "
            "WHERE id = $1::uuid AND 'owner' = ANY(roles))",
            entity_id,
        )
    except Exception:  # noqa: BLE001 -- unavailable attestation fails closed
        return None
    return FinanceSignalSource("owner") if is_owner else None


__all__ = [
    "EXPECTED_SIGNAL_SOURCE_KEY",
    "FinanceSignalSource",
    "metadata_with_signal_source",
    "resolve_complete_signal_source",
    "runtime_signal_source",
    "sanitized_metadata",
    "signal_source_from_metadata",
]
