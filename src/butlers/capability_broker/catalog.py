"""The broker's per-call view of the published D4 catalog.

Read on the call's own locked connection every time: a republish is seen by
the next call, and an unreadable publication refuses rather than falling back
to a cached copy.
"""

from __future__ import annotations

from typing import Protocol

from butlers.core.capability_catalog import BrokerCatalogReader

from .contracts import BrokerRefused, Capability, Catalog, Reason


class CatalogSource(Protocol):
    async def load(self, connection) -> Catalog: ...


class PublishedCatalogSource:
    """Adapts the verified fleet publication to broker capabilities."""

    def __init__(self) -> None:
        self._reader = BrokerCatalogReader()

    async def load(self, connection) -> Catalog:
        try:
            published = await self._reader.load(connection)
            return Catalog(
                published.digest,
                tuple(
                    Capability.from_descriptor(item.domain, item.descriptor, item.input_schema)
                    for item in published.capabilities
                ),
            )
        except Exception:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
