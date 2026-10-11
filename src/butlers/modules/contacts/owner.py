"""Fixed Contacts-to-Relationship MCP boundary; no caller-pool fallback."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any


class ContactsOwnerUnavailable(RuntimeError):
    """A required owning operation did not return a complete usable result."""


class ContactsLookupAmbiguous(RuntimeError):
    """A complete lookup found more than one eligible person."""


class ContactsOwner:
    """Use the daemon's current client, including when wiring follows startup."""

    def __init__(
        self, get_client: Callable[[], Any], source_butler: str | Callable[[], str]
    ) -> None:
        self._get_client = get_client
        self._source_butler = source_butler

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        source_butler = (
            self._source_butler() if callable(self._source_butler) else self._source_butler
        )
        if client is None or not isinstance(source_butler, str) or not source_butler:
            raise ContactsOwnerUnavailable("contacts owner unavailable")
        try:
            response = await asyncio.wait_for(
                client.call_tool(
                    "route",
                    {
                        "target_butler": "relationship",
                        "tool_name": tool,
                        "args": args,
                        "source_butler": source_butler,
                    },
                ),
                timeout=30,
            )
            raw = response.data
            if response.is_error or not isinstance(raw, dict) or raw.get("error"):
                raise ContactsOwnerUnavailable("contacts owner unavailable")
            result = raw.get("result")
            if not isinstance(result, dict) or result.get("error"):
                raise ContactsOwnerUnavailable("contacts owner unavailable")
            return result
        except Exception:
            # Neither a transport error nor a possible-commit timeout proves absence.
            # Retain no exception operands or new automatic retry.
            raise ContactsOwnerUnavailable("contacts owner unavailable") from None

    async def lookup(self, channel_type: str, value: str) -> uuid.UUID | None:
        request_id = str(uuid.uuid4())
        result = await self._call(
            "contacts_resolve_channel",
            {"channel_type": channel_type, "channel_value": value, "request_id": request_id},
        )
        if result.get("request_id") != request_id or "entity_id" not in result:
            raise ContactsOwnerUnavailable("contacts owner lookup unavailable")
        outcome, entity_id = result.get("outcome"), result.get("entity_id")
        if outcome == "unique" and isinstance(entity_id, str):
            try:
                return uuid.UUID(entity_id)
            except ValueError:
                pass
        elif entity_id is None:
            if outcome == "empty":
                return None
            if outcome == "ambiguous":
                raise ContactsLookupAmbiguous("contacts owner lookup ambiguous")
        raise ContactsOwnerUnavailable("contacts owner lookup unavailable")

    async def report(
        self, entity_id: uuid.UUID, channel_type: str, value: str, primary: bool
    ) -> dict[str, Any]:
        result = await self._call(
            "contacts_report_channel",
            {
                "subject": str(entity_id),
                "channel_type": channel_type,
                "channel_value": value,
                "primary": primary,
            },
        )
        outcome = result.get("outcome")
        id_key = "action_id" if outcome == "pending_approval" else "fact_id"
        if outcome not in {"inserted", "unchanged", "superseded", "candidate", "pending_approval"}:
            raise ContactsOwnerUnavailable("contacts owner report unavailable")
        try:
            uuid.UUID(result[id_key])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ContactsOwnerUnavailable("contacts owner report unavailable") from None
        return result
