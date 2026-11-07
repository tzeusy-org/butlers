"""Standalone published D4 projection; no transport or recipient authority.

Only delivered core contracts are dependencies. Final descriptors retain every
safety/output/store declaration. Their recipient defaults describe a handler;
they do not prove who receives any particular call.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from butlers.core.catalog_reader import PublishedCatalogProvider
from butlers.core.goal_admission import EvidenceRefused
from butlers.core.tool_catalog import ToolDescriptor, declared_approval_mode


def _wire(value):
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PublishedCapability:
    domain: str
    descriptor: ToolDescriptor

    @property
    def identity(self) -> str:
        return f"{self.domain}__{self.descriptor.canonical_name}"

    @property
    def approval_mode(self) -> str:
        return declared_approval_mode(self.descriptor.approval_policy)

    @property
    def input_schema(self) -> dict:
        # Fresh wire-shaped copy; a consumer cannot mutate the publication.
        return {key: _wire(value) for key, value in self.descriptor.input_schema.items()}


@dataclass(frozen=True, slots=True)
class PublishedBrokerCatalog:
    digest: str
    generation_digest: str
    capabilities: tuple[PublishedCapability, ...]


class BrokerCatalogReader:
    def __init__(self):
        self.provider = PublishedCatalogProvider()

    async def load(self, connection) -> PublishedBrokerCatalog:
        try:
            snapshot = await self.provider.snapshot(connection)
            capabilities = []
            for registration in snapshot.registrations:
                for item in registration.catalog.descriptors:
                    if item.owner_domain != registration.domain:
                        raise ValueError("foreign_descriptor_owner")
                    declared_approval_mode(item.approval_policy)
                    if not item.authorization_complete:
                        raise ValueError("incomplete_authorization")
                    if item.grantable:
                        capabilities.append(PublishedCapability(registration.domain, item))
            return PublishedBrokerCatalog(
                snapshot.digest, snapshot.generation_digest, tuple(capabilities)
            )
        except Exception:
            # No publication, argument, or database content in public errors.
            # Cancellation is a BaseException and propagates.
            raise EvidenceRefused("catalog_unavailable") from None
