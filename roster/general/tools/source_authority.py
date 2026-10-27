"""Ordinary-source authority: the one predicate, lock order and version seam.

RFC 0037 "Passive classification is not custody enrollment" and
REQ-general-capture-003/-005.  Every generic General reader and writer --
MCP tools, the dashboard API, and the future capture service -- goes through
this module so the ordinary boundary is one owned definition:

- :data:`ORDINARY_PARENT` is the SQL predicate for a parent collection whose
  contents generic callers may see or change.  A collection classified
  ``custody_private`` is excluded, and so is an ordinary-flagged collection
  holding a reserved ``possession_profile`` (an inconsistent parent refuses
  generic exposure rather than falling back to ordinary).  Queries against a
  schema without the classification column fail, which callers map to
  :class:`GeneralSourceUnavailable` -- a missing schema is not read authority.
- :func:`require_ordinary_namespace` gates every name-based admission.  If the
  schema contract is missing or malformed, or ANY private collection exists
  while the legacy global ``UNIQUE (name)`` is still in force, ALL names refuse
  with the same fixed outcome before any target lookup, so no name-specific
  collision can reveal a private collection.
- Mutations lock the parent collection first, then the item
  (:func:`lock_ordinary_parent`, :func:`lock_ordinary_item`), and record an
  immutable :class:`SourceVersion` in the same transaction
  (:func:`record_source_version`).  :func:`read_source_version` re-checks the
  item's *current* eligibility, so stored history never outlives privacy.

Nothing here classifies a collection private, writes a custody profile, or
exposes a private record; private enrollment is separately released work.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import asyncpg

RESERVED_ITEM_KEYS = frozenset({"possession_profile"})

# The single ordinary-parent predicate, over a ``collections`` row aliased ``c``.
ORDINARY_PARENT = (
    "(c.custody_private = false AND NOT EXISTS ("
    "SELECT 1 FROM collection_items AS reserved "
    "WHERE reserved.collection_id = c.id AND reserved.data ? 'possession_profile'))"
)

_UNAVAILABLE_SQLSTATES = frozenset(
    {
        "42703",  # undefined_column: classification column absent
        "42P01",  # undefined_table: vocabulary/source tables absent
        "42883",  # undefined_function: vocabulary key function absent
    }
)

_NAMESPACE_CONTRACT = """
SELECT
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = to_regclass('collections')
          AND attname = 'custody_private'
          AND atttypid = 'boolean'::regtype
          AND attnotnull
          AND NOT attisdropped
    ) AS has_flag,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index AS ix
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = ix.indrelid AND attribute.attnum = ix.indkey[0]
        WHERE ix.indrelid = to_regclass('collections')
          AND ix.indisunique AND ix.indnkeyatts = 1 AND ix.indexprs IS NULL
          AND attribute.attname = 'name'
          AND pg_catalog.pg_get_expr(ix.indpred, ix.indrelid) = '(custody_private = false)'
    ) AS has_ordinary_index,
    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index AS ix
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = ix.indrelid AND attribute.attnum = ix.indkey[0]
        WHERE ix.indrelid = to_regclass('collections')
          AND ix.indisunique AND ix.indnkeyatts = 1 AND ix.indexprs IS NULL
          AND ix.indpred IS NULL
          AND attribute.attname = 'name'
    ) AS has_global_unique
"""


class GeneralSourceUnavailable(RuntimeError):
    """Fixed, content-blind refusal for an unavailable ordinary source.

    Raised identically for every name while the namespace is incompatible, for
    a missing or malformed schema contract, and for a parent that cannot be
    classified.  It never names the target or distinguishes why.
    """

    MESSAGE = "General ordinary collections are unavailable"

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


def is_unavailable_error(exc: BaseException) -> bool:
    """True for database errors that mean the ordinary contract is absent."""
    return isinstance(exc, asyncpg.PostgresError) and exc.sqlstate in _UNAVAILABLE_SQLSTATES


@asynccontextmanager
async def ordinary_transaction(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection inside a transaction (a savepoint when nested).

    Accepts a pool or an already-acquired connection so a caller such as the
    capture service can compose a mutation and its receipt in one transaction.
    Schema-contract database errors surface as :class:`GeneralSourceUnavailable`.
    """
    try:
        if isinstance(pool_or_conn, asyncpg.Connection) or not hasattr(pool_or_conn, "acquire"):
            async with pool_or_conn.transaction():
                yield pool_or_conn
        else:
            async with pool_or_conn.acquire() as conn, conn.transaction():
                yield conn
    except asyncpg.PostgresError as exc:
        if is_unavailable_error(exc):
            raise GeneralSourceUnavailable() from None
        raise


async def require_ordinary_namespace(conn: asyncpg.Connection) -> None:
    """Refuse ALL name-based admission unless the ordinary namespace is sound.

    Must run before any name is resolved.  The incompatible state -- any
    private collection while legacy global name uniqueness still holds -- is
    detected schema-wide, never per name.
    """
    contract = await conn.fetchrow(_NAMESPACE_CONTRACT)
    if not (contract["has_flag"] and contract["has_ordinary_index"]):
        raise GeneralSourceUnavailable()
    if contract["has_global_unique"] and await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM collections WHERE custody_private)"
    ):
        raise GeneralSourceUnavailable()


def reject_reserved_fields(data: Mapping[str, Any]) -> None:
    """Generic input can never introduce, replace or remove custody metadata."""
    if RESERVED_ITEM_KEYS.intersection(data):
        raise ValueError("Item data uses a reserved General field.")


async def lock_ordinary_parent(
    conn: asyncpg.Connection, collection_id: uuid.UUID, *, exclusive: bool = False
) -> asyncpg.Record | None:
    """Lock an ordinary parent (share by default) and return it, else None.

    The predicate is re-evaluated after any wait, so a parent classified
    private while we waited is not returned.
    """
    strength = "UPDATE" if exclusive else "SHARE"
    return await conn.fetchrow(
        f"""
        SELECT c.id, c.name, c.eligibility_generation
        FROM collections AS c
        WHERE c.id = $1 AND {ORDINARY_PARENT}
        FOR {strength} OF c
        """,
        collection_id,
    )


async def lock_ordinary_item(
    conn: asyncpg.Connection, item_id: uuid.UUID
) -> tuple[asyncpg.Record, asyncpg.Record] | None:
    """Lock an ordinary item's parent, then the item; None if not ordinary.

    Returns ``(parent, item)``.  A missing item and an item under a
    non-ordinary parent are indistinguishable to the caller.
    """
    collection_id = await conn.fetchval(
        "SELECT collection_id FROM collection_items WHERE id = $1", item_id
    )
    if collection_id is None:
        return None
    parent = await lock_ordinary_parent(conn, collection_id)
    if parent is None:
        return None
    item = await conn.fetchrow(
        """
        SELECT id, collection_id, data, tags
        FROM collection_items
        WHERE id = $1 AND collection_id = $2
        FOR UPDATE
        """,
        item_id,
        collection_id,
    )
    if item is None:
        return None
    return parent, item


@dataclass(frozen=True)
class SourceVersion:
    """One immutable General source version of an item."""

    item_id: uuid.UUID
    version: int
    collection_id: uuid.UUID
    eligibility_generation: int
    operation: str
    digest: str
    content: dict[str, Any] | None
    current: bool = True


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def source_digest(item_id: uuid.UUID, operation: str, content: Mapping[str, Any] | None) -> str:
    """sha256 over the canonical item projection (or a delete tombstone)."""
    body = {"item_id": str(item_id), "operation": operation, "content": content}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_source_version(
    conn: asyncpg.Connection,
    parent: asyncpg.Record,
    item_id: uuid.UUID,
    operation: str,
) -> SourceVersion:
    """Append the next immutable version of *item_id* in the caller's transaction.

    The caller must already hold the parent lock and the item row lock (or
    have just inserted the item), so the projection, digest and version number
    are those of exactly this mutation.  A delete records a content-free
    tombstone and must run before the row is removed.
    """
    if operation == "delete":
        content: dict[str, Any] | None = None
    else:
        row = await conn.fetchrow(
            "SELECT collection_id, data, tags FROM collection_items WHERE id = $1", item_id
        )
        content = {
            "collection_id": str(row["collection_id"]),
            "data": _decode(row["data"]),
            "tags": _decode(row["tags"]),
        }
    digest = source_digest(item_id, operation, content)
    version = await conn.fetchval(
        """
        INSERT INTO source_versions
            (item_id, version, collection_id, eligibility_generation, operation, digest, content)
        SELECT $1, COALESCE(max(version), 0) + 1, $2, $3, $4, $5, $6
        FROM source_versions
        WHERE item_id = $1
        RETURNING version
        """,
        item_id,
        parent["id"],
        parent["eligibility_generation"],
        operation,
        digest,
        content,
    )
    return SourceVersion(
        item_id=item_id,
        version=version,
        collection_id=parent["id"],
        eligibility_generation=parent["eligibility_generation"],
        operation=operation,
        digest=digest,
        content=content,
    )


async def read_source_version(
    pool_or_conn: asyncpg.Pool | asyncpg.Connection,
    item_id: uuid.UUID,
    version: int | None = None,
) -> SourceVersion | None:
    """Read a stored version only while the item is eligible *now*.

    Returns None -- identical for a missing item, an unknown version, a
    deleted item, a parent that is no longer ordinary, or a version recorded
    under an older eligibility generation -- so immutable history never
    bypasses current privacy.  ``version=None`` reads the latest version.
    """
    row = await pool_or_conn.fetchrow(
        f"""
        SELECT v.item_id, v.version, v.collection_id, v.eligibility_generation,
               v.operation, v.digest, v.content,
               v.version = (
                   SELECT max(latest.version) FROM source_versions AS latest
                   WHERE latest.item_id = v.item_id
               ) AS current
        FROM source_versions AS v
        JOIN collection_items AS item
          ON item.id = v.item_id AND item.collection_id = v.collection_id
        JOIN collections AS c ON c.id = item.collection_id
        WHERE v.item_id = $1
          AND v.operation <> 'delete'
          AND v.eligibility_generation = c.eligibility_generation
          AND {ORDINARY_PARENT}
          AND ($2::bigint IS NULL OR v.version = $2)
        ORDER BY v.version DESC
        LIMIT 1
        """,
        item_id,
        version,
    )
    if row is None:
        return None
    return SourceVersion(
        item_id=row["item_id"],
        version=row["version"],
        collection_id=row["collection_id"],
        eligibility_generation=row["eligibility_generation"],
        operation=row["operation"],
        digest=row["digest"],
        content=_decode(row["content"]),
        current=row["current"],
    )
