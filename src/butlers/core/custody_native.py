"""Dependency-light native custody mutation seam for audited operator writers.

This registry carries only the actual pool's constructor-installed publisher.
It grants no source, admission or actor authority; installation/validation stay
in custody_bindings after genuine enrollment. Unallocated legacy writers retain
their existing behavior and make no custody-currentness claim. Standalone
PEP723 operator commands need only their declared asyncpg dependency.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from butlers.core.custody_bindings import CustodyChannelBindings

_installed_publishers: dict[asyncpg.Pool, CustodyChannelBindings] = {}


def owning_binding_publisher(pool: asyncpg.Pool) -> CustodyChannelBindings | None:
    # Legacy connection-scope wrappers (including SimpleNamespace) need not
    # be hashable. They cannot name an enrolled actual pool allocation.
    try:
        return _installed_publishers.get(pool)
    except TypeError:
        return None


@asynccontextmanager
async def native_channel_mutation(
    pool: asyncpg.Pool, connection: asyncpg.Connection, entity_ids: Iterable[uuid.UUID]
) -> AsyncIterator[asyncpg.Connection]:
    """Fixed native owning hook; no request selects its publisher or authority.

    Call before the native transaction/entity/fact locks. A connection already
    inside a genuine bound custody transaction reuses THAT writer. An already
    open unbound transaction cannot establish first-lock order by a savepoint:
    its outer caller must install the same constructor allocation earlier.
    Legacy unallocated pools retain their existing behavior, without any claim
    of custody currentness. The complete native-writer universe is mandatory.
    """
    publisher = owning_binding_publisher(pool)
    if publisher is None:
        yield connection
    else:
        async with publisher.native_mutation(connection, entity_ids) as writer:
            yield writer._connection
