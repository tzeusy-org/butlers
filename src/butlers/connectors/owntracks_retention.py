"""Execute one Chronicler-prepared OwnTracks retention grant (bu-s11n0s.7 S4b-3b). Dark.

Retention runs in two committed stages. Chronicler prepares a grant and forgets its own copies;
the connector then deletes the granted raw rows here, under ``connector_writer``, and leaves its
evidence in ``connectors.owntracks_retention_batches`` and ``..._tombstones`` (core_342).

The grant reaches the connector as a payload of ids, digests and the cutoff only
(:func:`parse_grant`). Anything malformed is :class:`MalformedGrant`: no batch.

:func:`execute_grant` runs one transaction:

* A grant whose receipt exists returns that receipt and changes nothing.
* ``connectors.owntracks_forget_lock()`` is taken first, exclusive (core_343). The points
  insert trigger takes the same lock shared, so a webhook replay racing this transaction waits
  for its commit and then finds the tombstone instead of restoring the row.
* Each granted row still present is locked and must be exactly the row Chronicler granted: same
  logical key, same v1 content digest (recomputed in SQL by the shared
  :func:`~butlers.chronicler.projection_coverage.raw_row_digest_sql`), retention clock strictly
  before the cutoff. Any mismatch, or a key already tombstoned, raises :class:`GrantMismatch`
  and nothing is written.
* Present rows are deleted; rows already gone count as ``already_forgotten``. Every item gets a
  tombstone keyed by its logical key, so even a row that never reached a point event cannot be
  inserted again.
* The connector's parked ``connectors.filtered_events`` copies of the granted fixes (an
  envelope parked after its ingest attempts were spent; the point is still persisted) keep their
  row but lose the fix: ``full_payload.payload`` becomes :data:`WITHHELD_TEXT` and the row is
  marked not replayable (S4c-4). They are matched by ``sender_identity`` and
  ``external_message_id``, which spell the logical key. A copy written later is withheld by
  core_345's insert trigger. A failure here rolls back the delete. A policy-filtered fix is
  recorded without a point row, so it never enters a grant and is outside its reach; the
  connector stores it without coordinates instead (bu-s11n0s.73).
* The receipt is written last.

The worker (:func:`run_retention_once`, :func:`start_retention_worker`) fetches ready grants
from Chronicler through the ``location_retention.grants_ready`` named relay (RFC 0038 D7),
presenting the connector's workload token and the ``owntracks`` daemon name, and executes each.
It fails closed: an unreachable relay, a refusal, a missing token or a malformed reply executes
nothing, and a grant whose rows no longer match is skipped. It runs only when
``OWNTRACKS_RAW_RETENTION_ENABLED=true``, which is off by default and stays off until the owner
enables raw expiry; ``ready`` grants do not exist before holder copies (S4c) anyway.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from butlers.chronicler.projection_coverage import raw_row_digest_sql
from butlers.core.service_plane.client import call_relay
from butlers.core.service_plane.registry import LOCATION_RETENTION_GRANTS_READY
from butlers.core.service_plane.workload_identity import TOKEN_DIR

logger = logging.getLogger(__name__)

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
# core_343's forget lock, exclusive here and shared in the points insert trigger: a webhook
# replay of a key being forgotten waits for this transaction and then reads its tombstone. One
# lock, so a grant of any size holds one lock-table entry and two deleters serialise.
_FORGET_LOCK = "SELECT connectors.owntracks_forget_lock()"
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
#: What a withheld ``connectors.filtered_events`` copy keeps of the fix (S4c-4). core_345's
#: insert trigger writes the same values, for a copy written after the fix was forgotten.
WITHHELD_TEXT = "[location withheld: raw retention]"
#: The replay drain refuses a row carrying this reason instead of submitting a hollow event.
NOT_REPLAYABLE = "the location was forgotten by raw retention"
# A copy is matched by the connector's own identifiers, never by text: OwnTracks parks a
# location envelope under ``sender_identity`` = its endpoint identity and
# ``external_message_id`` = ``{tst}:location``, so ``owntracks:{sender}:{id}`` is the logical
# key. Transition and waypoints copies (``{tst}:transition:{event}``, ``{tst}:waypoints``) never
# spell a location key.
_WITHHOLD_COPIES = f"""
    UPDATE connectors.filtered_events
    SET full_payload = full_payload || jsonb_build_object(
        'payload', jsonb_build_object('raw', NULL, 'normalized_text', '{WITHHELD_TEXT}'),
        'replay_unavailable', '{NOT_REPLAYABLE}'
    )
    WHERE source_channel = 'owntracks'
      AND 'owntracks:' || sender_identity || ':' || external_message_id = ANY($1::text[])
"""
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
        # Locked before the receipt read: a concurrent run of the same grant waits, then
        # returns the receipt this one wrote.
        await conn.execute(_FORGET_LOCK)
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
        withheld = await conn.execute(_WITHHOLD_COPIES, keys)
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
    # Counts only: which rows were withheld never reaches a log.
    logger.info(
        "owntracks retention grant %s: %d deleted, %d connector copies withheld",
        grant.grant_id,
        deleted_count,
        int(withheld.rsplit(" ", 1)[-1]),
    )
    return receipt


# ── Worker ────────────────────────────────────────────────────────────────

#: The daemon name the connector presents beside its workload token.
RELAY_DAEMON = "owntracks"
_ENABLED_ENV = "OWNTRACKS_RAW_RETENTION_ENABLED"
_INTERVAL_ENV = "OWNTRACKS_RAW_RETENTION_INTERVAL_S"
_DEFAULT_INTERVAL_S = 3600.0
_MIN_INTERVAL_S = 60.0

FetchGrants = Callable[[], Awaitable[Any]]
ExecuteGrant = Callable[[Any, RetentionGrant], Awaitable[BatchReceipt]]


@dataclass(frozen=True, slots=True)
class RetentionWorkerConfig:
    enabled: bool = False
    interval_s: float = _DEFAULT_INTERVAL_S

    @classmethod
    def from_env(cls) -> RetentionWorkerConfig:
        """Enabled only by an exact ``true``; any other value leaves the worker off."""
        enabled = os.environ.get(_ENABLED_ENV, "").strip().lower() == "true"
        try:
            interval = float(os.environ.get(_INTERVAL_ENV, "") or _DEFAULT_INTERVAL_S)
        except ValueError:
            interval = _DEFAULT_INTERVAL_S
        return cls(enabled=enabled, interval_s=max(interval, _MIN_INTERVAL_S))


class _Reply(_Strict):
    grants: list[Any]


def _grants_from(reply: Any) -> list[RetentionGrant]:
    """Every grant in a relay reply, or :class:`MalformedGrant` for the whole reply."""
    if not isinstance(reply, Mapping):
        raise MalformedGrant("relay reply is not an object")
    try:
        grants = _Reply.model_validate(dict(reply)).grants
    except ValidationError:
        raise MalformedGrant("relay reply is not {grants: [...]}") from None
    return [parse_grant(grant) for grant in grants]


async def fetch_ready_grants(
    *,
    switchboard_url: str,
    token_dir: Path = TOKEN_DIR,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """Ask Chronicler, through the named relay, which grants are ready."""
    return await call_relay(
        LOCATION_RETENTION_GRANTS_READY,
        {},
        daemon=RELAY_DAEMON,
        switchboard_url=switchboard_url,
        token_dir=token_dir,
        transport=transport,
    )


async def run_retention_once(
    pool: Any, fetch: FetchGrants, *, execute: ExecuteGrant = execute_grant
) -> list[BatchReceipt]:
    """Fetch the ready grants and execute each; anything untrusted executes nothing."""
    try:
        grants = _grants_from(await fetch())
    except MalformedGrant as exc:
        logger.warning("owntracks retention: %s; nothing executed", exc)
        return []
    except Exception as exc:  # unreachable, refused, no token: fail closed
        logger.warning(
            "owntracks retention: ready grants unavailable (%s); nothing executed",
            type(exc).__name__,
        )
        return []
    receipts: list[BatchReceipt] = []
    for grant in grants:
        try:
            async with pool.acquire() as conn:
                receipts.append(await execute(conn, grant))
        except GrantMismatch as exc:
            logger.warning("owntracks retention: grant %s refused: %s", grant.grant_id, exc)
    return receipts


async def _worker_loop(pool: Any, fetch: FetchGrants, interval_s: float) -> None:
    while True:
        try:
            receipts = await run_retention_once(pool, fetch)
            if receipts:
                logger.info(
                    "owntracks retention: %d grants executed, %d rows deleted",
                    len(receipts),
                    sum(r.deleted_count for r in receipts),
                )
        except Exception:
            logger.exception("owntracks retention: run failed; retrying next interval")
        await asyncio.sleep(interval_s)


def start_retention_worker(
    config: RetentionWorkerConfig, pool: Any, *, switchboard_url: str
) -> asyncio.Task[None] | None:
    """Start the worker when the owner enabled it; otherwise start nothing."""
    if not config.enabled or pool is None or not switchboard_url:
        return None

    async def fetch() -> Any:
        return await fetch_ready_grants(switchboard_url=switchboard_url)

    return asyncio.create_task(_worker_loop(pool, fetch, config.interval_s))
