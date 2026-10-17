"""Durable QA publication operation transitions (REQ-qa-publication-authority-002/-006).

Every operation is bound to its attempt and persisted at ``bound`` before any
egress, advances only forward (``bound`` -> ``egress_started`` -> an outcome ->
``reconciled``), and an operation left in ``egress_started`` or ``ambiguous``
holds its fingerprint lineage until an exact read-only reconciliation is
recorded.  Only the trusted dispatcher calls this module, and no credential,
provider body or private identifier is stored: receipts carry categories,
digests and the bound object identifier.

Every datastore failure fails closed: the caller receives
:class:`PublicationRefused` and must not egress.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import asyncpg

from butlers.core.qa.publication.binding import OperationKind, PublicationBinding

logger = logging.getLogger(__name__)

Outcome = Literal["succeeded", "failed", "ambiguous"]
Observation = Literal["resource_found", "resource_absent"]

_OUTCOME_RESULT = {"succeeded": "success", "failed": "failed", "ambiguous": "ambiguous"}


class PublicationRefused(RuntimeError):
    """A publication step was refused before egress; ``code`` is a stable category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PublicationOperation:
    id: uuid.UUID
    attempt_id: uuid.UUID
    kind: str
    stage: str
    binding_digest: str
    request_digest: str
    expected_object_id: str | None
    new_object_id: str | None
    result: str | None
    reconciliation: str | None
    started_at: datetime
    ended_at: datetime | None
    reconciled_at: datetime | None

    @property
    def holds_lineage(self) -> bool:
        return self.stage in ("egress_started", "ambiguous")

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> PublicationOperation:
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


_COLUMNS = ", ".join(PublicationOperation.__dataclass_fields__)

_DATASTORE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)


async def begin_operation(
    pool: Any,
    *,
    attempt_id: uuid.UUID,
    kind: OperationKind,
    binding: PublicationBinding,
    request_digest: str,
    expected_object_id: str | None = None,
) -> PublicationOperation:
    """Persist the bound operation, or return the existing one for the same binding.

    A second request for the same ``(attempt, kind)`` with a changed binding or
    payload is refused without touching the stored row.  The unique constraint
    makes two concurrent callers produce exactly one row.  The caller may
    egress only when the returned operation is at ``bound`` and
    :func:`mark_egress_started` then wins.
    """
    binding_digest = binding.digest()
    try:
        row = await pool.fetchrow(
            f"""
            INSERT INTO public.qa_publication_operations
                (attempt_id, kind, stage, stage_order, binding_digest, request_digest,
                 expected_object_id)
            VALUES ($1, $2, 'bound', 1, $3, $4, $5)
            ON CONFLICT (attempt_id, kind) DO NOTHING
            RETURNING {_COLUMNS}
            """,
            attempt_id,
            kind.value,
            binding_digest,
            request_digest,
            expected_object_id,
        )
        if row is None:
            row = await pool.fetchrow(
                f"""
                SELECT {_COLUMNS} FROM public.qa_publication_operations
                WHERE attempt_id = $1 AND kind = $2
                """,
                attempt_id,
                kind.value,
            )
    except asyncpg.ForeignKeyViolationError as exc:
        raise PublicationRefused("unknown_attempt") from exc
    except _DATASTORE_ERRORS as exc:
        logger.warning("QA publication state unavailable; refusing", exc_info=True)
        raise PublicationRefused("datastore_unavailable") from exc
    if row is None:
        raise PublicationRefused("datastore_unavailable")

    operation = PublicationOperation.from_row(row)
    if operation.binding_digest != binding_digest:
        raise PublicationRefused("binding_changed")
    if operation.request_digest != request_digest:
        raise PublicationRefused("payload_changed")
    return operation


async def mark_egress_started(pool: Any, operation_id: uuid.UUID) -> bool:
    """Persist that egress is about to begin; True for exactly one caller."""
    try:
        claimed = await pool.fetchval(
            """
            UPDATE public.qa_publication_operations
            SET stage = 'egress_started', stage_order = 2
            WHERE id = $1 AND stage = 'bound'
            RETURNING id
            """,
            operation_id,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    return claimed is not None


async def record_outcome(
    pool: Any,
    operation_id: uuid.UUID,
    outcome: Outcome,
    *,
    new_object_id: str | None = None,
) -> bool:
    """Record the egress outcome.  ``ambiguous`` (e.g. a lost response) holds the lineage."""
    try:
        recorded = await pool.fetchval(
            """
            UPDATE public.qa_publication_operations
            SET stage = $2, stage_order = 3, result = $3,
                new_object_id = COALESCE($4, new_object_id), ended_at = now()
            WHERE id = $1 AND stage = 'egress_started'
            RETURNING id
            """,
            operation_id,
            outcome,
            _OUTCOME_RESULT[outcome],
            new_object_id,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    return recorded is not None


async def record_reconciliation(
    pool: Any,
    operation_id: uuid.UUID,
    observation: Observation,
    *,
    observed_object_id: str | None = None,
) -> bool:
    """Release a held lineage with an exact, read-only reconciliation.

    ``resource_found`` must name exactly the bound ``expected_object_id``;
    ``resource_absent`` records that the bound resource does not exist.  Any
    other observation leaves the hold in place.
    """
    try:
        recorded = await pool.fetchval(
            """
            UPDATE public.qa_publication_operations
            SET stage = 'reconciled', stage_order = 4, reconciliation = $2::text,
                reconciled_object_id = $3, new_object_id = COALESCE(new_object_id, $3),
                reconciled_at = now()
            WHERE id = $1 AND stage IN ('egress_started', 'ambiguous')
              AND (
                  ($2 = 'resource_found' AND $3::text IS NOT NULL
                      AND $3::text = expected_object_id)
                  OR ($2 = 'resource_absent' AND $3::text IS NULL)
              )
            RETURNING id
            """,
            operation_id,
            observation,
            observed_object_id,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    return recorded is not None
