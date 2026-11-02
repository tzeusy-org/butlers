"""Durable retention of a sealed QA export outside agent control (bu-mc01y5.19).

The spec scenario "Committed proposal cannot be published" requires the sealed
artifact to outlive the agent worktree, whose branch and objects are deleted at
teardown.  :class:`~butlers.core.qa.publication.publisher.QaPublisher` therefore
writes the exact validated bytes to ``public.qa_publication_exports`` (core_294)
before its first egress, so a push or PR creation that fails or ends ambiguous
still leaves the proposal recoverable for read-only reconciliation.

One attempt retains at most one export, and the row is immutable: retaining the
same export again is a no-op, a different digest refuses with ``export_changed``.
Loading re-derives every object id and the digest (:meth:`SealedExport.verify`),
so altered stored bytes refuse with ``export_tampered``.  Every datastore failure
refuses closed and the publisher then sends nothing.

Retention (bu-mc01y5.25, core_301): :func:`prune_retained_exports` empties the object
bytes of an export whose attempt has been terminal for :data:`EXPORT_RETENTION` and holds
no publication lineage, and stamps ``pruned_at``.  The row itself stays as a tombstone:
base, head, commits and digest still bind the proposal for the case API and the
reconciliation sweep, and no row is ever deleted.  The database guard refuses any other
rewrite and any prune before eligibility, for every role.  A pruned export refuses with
``export_pruned`` on load and on re-retention.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta
from typing import Any

import asyncpg

from butlers.core.qa.publication.export import (
    MAX_EXPORT_BYTES,
    ExportedObject,
    ExportRefused,
    SealedExport,
)
from butlers.core.qa.publication.state import PublicationRefused

logger = logging.getLogger(__name__)

_DATASTORE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)
_HEADER = re.compile(rb"([0-9a-f]{40}) (blob|tree|commit) ([0-9]{1,9})\n")
#: Framing allowance over the export's own size bound.
_MAX_ENCODED_BYTES = MAX_EXPORT_BYTES + (1 << 20)

#: How long a terminal attempt's export bytes are kept after ``closed_at``.  The core_301
#: guard enforces the same window in SQL (``interval '30 days'``); change both together.
EXPORT_RETENTION = timedelta(days=30)
#: Default rows pruned per call, so one run never rewrites an unbounded batch.
PRUNE_BATCH = 100


def encode_objects(objects: tuple[ExportedObject, ...]) -> bytes:
    """Frame each object as ``<oid> <kind> <size>\\n<data>``, in export order."""
    return b"".join(
        f"{obj.oid} {obj.kind} {len(obj.data)}\n".encode() + obj.data for obj in objects
    )


def decode_objects(payload: bytes) -> tuple[ExportedObject, ...]:
    """Inverse of :func:`encode_objects`; any malformed frame refuses with ``export_tampered``."""
    if len(payload) > _MAX_ENCODED_BYTES:
        raise ExportRefused("export_tampered")
    objects: list[ExportedObject] = []
    offset = 0
    while offset < len(payload):
        header = _HEADER.match(payload, offset)
        if header is None:
            raise ExportRefused("export_tampered")
        start = header.end()
        end = start + int(header.group(3))
        if end > len(payload):
            raise ExportRefused("export_tampered")
        objects.append(
            ExportedObject(header.group(1).decode(), header.group(2).decode(), payload[start:end])
        )
        offset = end
    return tuple(objects)


async def retain_export(pool: Any, attempt_id: uuid.UUID, export: SealedExport) -> None:
    """Persist ``export`` for ``attempt_id`` once; raise :class:`PublicationRefused` otherwise."""
    export.verify()
    try:
        stored = await pool.fetchrow(
            """
            WITH inserted AS (
                INSERT INTO public.qa_publication_exports
                    (attempt_id, base_sha, head_sha, commits, objects, digest)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (attempt_id) DO NOTHING
                RETURNING digest, false AS pruned
            )
            SELECT digest, pruned FROM inserted
            UNION ALL
            SELECT digest, pruned_at IS NOT NULL
            FROM public.qa_publication_exports WHERE attempt_id = $1
            LIMIT 1
            """,
            attempt_id,
            export.base_sha,
            export.head_sha,
            list(export.commits),
            encode_objects(export.objects),
            export.digest,
        )
    except asyncpg.ForeignKeyViolationError as exc:
        raise PublicationRefused("unknown_attempt") from exc
    except _DATASTORE_ERRORS as exc:
        logger.warning("QA export retention unavailable; refusing", exc_info=True)
        raise PublicationRefused("export_unretained") from exc
    if stored is None:
        raise PublicationRefused("export_unretained")
    if stored["digest"] != export.digest:
        raise PublicationRefused("export_changed")
    if stored["pruned"]:
        raise PublicationRefused("export_pruned")


async def load_retained_export(pool: Any, attempt_id: uuid.UUID) -> SealedExport | None:
    """Return the verified retained export for ``attempt_id``, or ``None`` if none was retained."""
    try:
        row = await pool.fetchrow(
            """
            SELECT base_sha, head_sha, commits, objects, digest, pruned_at IS NOT NULL AS pruned
            FROM public.qa_publication_exports
            WHERE attempt_id = $1
            """,
            attempt_id,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    if row is None:
        return None
    if row["pruned"]:
        raise PublicationRefused("export_pruned")
    export = SealedExport(
        base_sha=row["base_sha"],
        head_sha=row["head_sha"],
        commits=tuple(row["commits"]),
        objects=decode_objects(bytes(row["objects"])),
        digest=row["digest"],
    )
    export.verify()
    return export


async def prune_retained_exports(pool: Any, *, limit: int = PRUNE_BATCH) -> int:
    """Empty the bytes of up to ``limit`` exports past retention; return how many were pruned.

    Eligible: the attempt is terminal, closed at least :data:`EXPORT_RETENTION` ago by
    database time, and none of its publication operations is held (``egress_started`` or
    ``ambiguous``, the predicate of ``get_publication_hold``).  The core_301 guard re-checks
    the same rule per row, so this query only selects; it cannot widen what is prunable.
    Datastore errors propagate to the caller.
    """
    status = await pool.execute(
        """
        WITH due AS (
            SELECT x.attempt_id
            FROM public.qa_publication_exports AS x
            JOIN public.healing_attempts AS a ON a.id = x.attempt_id
            WHERE x.pruned_at IS NULL
              AND a.status IN ('pr_merged', 'failed', 'unfixable', 'anonymization_failed',
                               'timeout')
              AND a.closed_at <= now() - interval '30 days'
              AND NOT EXISTS (
                  SELECT 1 FROM public.qa_publication_operations AS op
                  WHERE op.attempt_id = x.attempt_id
                    AND op.stage IN ('egress_started', 'ambiguous')
              )
            ORDER BY a.closed_at
            LIMIT $1
            FOR UPDATE OF x SKIP LOCKED
        )
        UPDATE public.qa_publication_exports AS x
        SET objects = ''::bytea, pruned_at = now()
        FROM due
        WHERE x.attempt_id = due.attempt_id
        """,
        limit,
    )
    return int(str(status).rsplit(" ", 1)[-1])
