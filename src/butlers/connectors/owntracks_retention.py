"""Execute one Chronicler-prepared OwnTracks retention grant (bu-s11n0s.7 S4b-3b). Dark.

Retention runs in two committed stages. Chronicler prepares a grant and forgets its own copies;
the connector then deletes the granted raw rows here, under ``connector_writer``, and leaves its
evidence in ``connectors.owntracks_retention_batches`` and ``..._tombstones`` (core_342).

The grant reaches the connector as a payload of ids, digests and the cutoff only
(:func:`parse_grant`). Anything malformed is :class:`MalformedGrant`: no batch.

:func:`execute_grant` runs one transaction:

* A grant whose receipt exists returns that receipt and changes nothing.
* Each granted row still present is locked and must be exactly the row Chronicler granted: same
  logical key, same v1 content digest (recomputed in SQL by the shared
  :func:`~butlers.chronicler.projection_coverage.raw_row_digest_sql`), retention clock strictly
  before the cutoff. Any mismatch, or a key already tombstoned, raises :class:`GrantMismatch`
  and nothing is written.
* Present rows are deleted; rows already gone count as ``already_forgotten``. Every item gets a
  tombstone keyed by its logical key, so even a row that never reached a point event cannot be
  inserted again, and the receipt is written last.

Nothing calls this yet: deleting raw fixes is irreversible and waits for the owner to enable it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from butlers.chronicler.projection_coverage import raw_row_digest_sql

#: Chronicler grants at most 500 raw rows per preparation run.
MAX_GRANT_ITEMS = 500
_DIGEST = re.compile(r"^v1:[0-9a-f]{64}$")


class MalformedGrant(ValueError):
    """The grant payload is not exactly ids, digests and a cutoff; nothing is executed."""


class GrantMismatch(RuntimeError):
    """A granted row is not the row Chronicler granted; the whole grant is refused."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GrantItem(_Strict):
    raw_source_id: UUID
    logical_source_key: str = Field(min_length=1)
    content_digest: str

    @field_validator("logical_source_key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank logical key")
        return value

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("not a v1 content digest")
        return value


class RetentionGrant(_Strict):
    grant_id: UUID
    cutoff: AwareDatetime
    items: tuple[GrantItem, ...] = Field(max_length=MAX_GRANT_ITEMS)

    @field_validator("items")
    @classmethod
    def _unique(cls, items: tuple[GrantItem, ...]) -> tuple[GrantItem, ...]:
        if len({item.raw_source_id for item in items}) != len(items):
            raise ValueError("a raw row is granted twice")
        if len({item.logical_source_key for item in items}) != len(items):
            raise ValueError("a logical key is granted twice")
        return items


@dataclass(frozen=True, slots=True)
class BatchReceipt:
    grant_id: UUID
    item_count: int
    deleted_count: int
    already_forgotten_count: int
    #: The receipt already existed: this call changed nothing.
    replayed: bool


def parse_grant(payload: Any) -> RetentionGrant:
    """Validate a grant payload, or raise :class:`MalformedGrant`."""
    if not isinstance(payload, Mapping):
        raise MalformedGrant("grant payload is not an object")
    try:
        return RetentionGrant.model_validate(dict(payload))
    except ValidationError as exc:
        # Field names only: a payload's values never reach a log.
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        raise MalformedGrant(f"malformed grant payload: {', '.join(fields)}") from None


_RECEIPT = (
    "SELECT item_count, deleted_count, already_forgotten_count "
    "FROM connectors.owntracks_retention_batches WHERE grant_id = $1"
)
_LOCK_ROWS = f"""
    SELECT p.id, p.idempotency_key, p.retention_at, {raw_row_digest_sql("p")} AS content_digest
    FROM connectors.owntracks_points p
    WHERE p.id = ANY($1::uuid[])
    ORDER BY p.id
    FOR UPDATE
"""
_TOMBSTONED = (
    "SELECT count(*) FROM connectors.owntracks_retention_tombstones "
    "WHERE logical_source_key = ANY($1::text[]) OR raw_source_id = ANY($2::uuid[])"
)
_DELETE = "DELETE FROM connectors.owntracks_points WHERE id = ANY($1::uuid[])"
_TOMBSTONE = (
    "INSERT INTO connectors.owntracks_retention_tombstones "
    "(logical_source_key, raw_source_id, content_digest, grant_id) "
    "SELECT key, raw_id, digest, $4 FROM unnest($1::text[], $2::uuid[], $3::text[]) "
    "AS item(key, raw_id, digest)"
)
_BATCH = (
    "INSERT INTO connectors.owntracks_retention_batches "
    "(grant_id, item_count, deleted_count, already_forgotten_count) VALUES ($1, $2, $3, $4)"
)


def _check(item: GrantItem, row: asyncpg.Record, cutoff: datetime) -> None:
    if row["idempotency_key"] != item.logical_source_key:
        raise GrantMismatch("a granted row's logical key changed")
    if row["content_digest"] != item.content_digest:
        raise GrantMismatch("a granted row's content changed")
    if row["retention_at"] is None or row["retention_at"] >= cutoff:
        raise GrantMismatch("a granted row is not older than the grant's cutoff")


async def execute_grant(conn: asyncpg.Connection, grant: RetentionGrant) -> BatchReceipt:
    """Delete the grant's raw rows and record the tombstones and receipt, all or nothing."""
    raw_ids = [item.raw_source_id for item in grant.items]
    keys = [item.logical_source_key for item in grant.items]
    async with conn.transaction():
        existing = await conn.fetchrow(_RECEIPT, grant.grant_id)
        if existing is not None:
            return BatchReceipt(grant.grant_id, *existing.values(), replayed=True)
        if await conn.fetchval(_TOMBSTONED, keys, raw_ids):
            raise GrantMismatch("a granted row is already tombstoned under another grant")
        rows = {row["id"]: row for row in await conn.fetch(_LOCK_ROWS, raw_ids)}
        for item in grant.items:
            if (row := rows.get(item.raw_source_id)) is not None:
                _check(item, row, grant.cutoff)
        deleted = await conn.execute(_DELETE, list(rows)) if rows else "DELETE 0"
        deleted_count = int(deleted.rsplit(" ", 1)[-1])
        if deleted_count != len(rows):
            raise GrantMismatch("a locked granted row was not deleted")
        await conn.execute(
            _TOMBSTONE,
            keys,
            raw_ids,
            [item.content_digest for item in grant.items],
            grant.grant_id,
        )
        receipt = BatchReceipt(
            grant.grant_id,
            item_count=len(grant.items),
            deleted_count=deleted_count,
            already_forgotten_count=len(grant.items) - deleted_count,
            replayed=False,
        )
        await conn.execute(
            _BATCH,
            grant.grant_id,
            receipt.item_count,
            receipt.deleted_count,
            receipt.already_forgotten_count,
        )
    return receipt
