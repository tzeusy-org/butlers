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
from datetime import datetime, timedelta
from typing import Any, Literal

import asyncpg

from butlers.core.healing.tracking import ACTIVE_STATUSES
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding

logger = logging.getLogger(__name__)

Outcome = Literal["succeeded", "failed", "ambiguous"]
Observation = Literal["resource_found", "resource_absent"]

_OUTCOME_RESULT = {"succeeded": "success", "failed": "failed", "ambiguous": "ambiguous"}

#: How long after its egress began (or its outcome was recorded) an operation must
#: be before an absent resource is believed.  It must exceed the longest a provider
#: call can stay in flight (client timeout plus GitHub's own processing of an
#: accepted request).  :func:`record_reconciliation` enforces it against database
#: time inside its guarded write.
ABSENCE_SETTLE = timedelta(minutes=15)


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
    egress_started_at: datetime | None
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
    """Persist that egress is about to begin; True for exactly one caller.

    The operation's attempt row is locked (``SELECT ... FOR UPDATE``) and must
    still be active in the same transaction as the stage transition, so a
    timed-out or terminal attempt can never enter ``egress_started``; it is
    refused with ``attempt_not_active``.  The transition stamps
    ``egress_started_at`` with database time (core_300): ``started_at`` is the
    bind time, and a resumed bind can egress long after it.
    """
    try:
        async with pool.acquire() as connection, connection.transaction():
            status = await connection.fetchval(
                """
                SELECT attempt.status
                FROM public.qa_publication_operations AS operation
                JOIN public.healing_attempts AS attempt ON attempt.id = operation.attempt_id
                WHERE operation.id = $1
                FOR UPDATE OF attempt
                """,
                operation_id,
            )
            if status is None:
                raise PublicationRefused("unknown_operation")
            if status not in ACTIVE_STATUSES:
                raise PublicationRefused("attempt_not_active")
            claimed = await connection.fetchval(
                """
                UPDATE public.qa_publication_operations
                SET stage = 'egress_started', stage_order = 2, egress_started_at = now()
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


async def get_operation(
    pool: Any, attempt_id: uuid.UUID, kind: OperationKind
) -> PublicationOperation | None:
    """Return the attempt's operation of ``kind``, or ``None`` when none was bound."""
    try:
        row = await pool.fetchrow(
            f"""
            SELECT {_COLUMNS} FROM public.qa_publication_operations
            WHERE attempt_id = $1 AND kind = $2
            """,
            attempt_id,
            kind.value,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    return None if row is None else PublicationOperation.from_row(row)


async def record_reconciliation(
    pool: Any,
    operation_id: uuid.UUID,
    observation: Observation,
    *,
    observed_object_id: str | None = None,
    expected_stage: str | None = None,
    absence_settle: timedelta = ABSENCE_SETTLE,
    adopted_pr_url: str | None = None,
) -> bool:
    """Release a held lineage with an exact, read-only reconciliation.

    ``resource_found`` must name exactly the bound ``expected_object_id``;
    ``resource_absent`` records that the bound resource does not exist.  A
    ``pr_create`` binds no PR number before egress (``expected_object_id`` is
    NULL), so its ``resource_found`` adopts the observed PR number instead
    (core_298): the reconciler proves it is the only PR for the bound head and
    base.  Any other observation leaves the hold in place.

    Absence is believed only once it has settled, judged by database time inside
    this guarded write: the row's outcome (``ended_at``) or, while still in
    flight, its egress (``egress_started_at``) must be at least
    ``absence_settle`` old.  A row with neither timestamp never settles.  When
    ``expected_stage`` is given the row must still be at the stage the caller
    observed, so an outcome recorded between the read and this write wins.

    A ``pr_create`` adoption also names the adopted PR on its attempt in the same
    statement: ``healing_attempts.pr_number`` and ``pr_url`` (``adopted_pr_url``,
    which must end in ``/pull/<number>``) are filled when the attempt names no PR
    yet.  A number beyond ``INTEGER`` or a mismatched URL skips that fill; the
    reconciliation itself still records.
    """
    try:
        recorded = await pool.fetchval(
            """
            WITH reconciled AS (
            UPDATE public.qa_publication_operations
            SET stage = 'reconciled', stage_order = 4, reconciliation = $2::text,
                reconciled_object_id = $3, new_object_id = COALESCE(new_object_id, $3),
                reconciled_at = now()
            WHERE id = $1 AND stage IN ('egress_started', 'ambiguous')
              AND ($4::text IS NULL OR stage = $4::text)
              AND (
                  ($2 = 'resource_found' AND $3::text IS NOT NULL
                      AND $3::text = expected_object_id)
                  OR ($2 = 'resource_found' AND kind = 'pr_create'
                      AND expected_object_id IS NULL
                      AND $3::text ~ '^[1-9][0-9]{0,9}$'
                      AND new_object_id IS NULL)
                  OR ($2 = 'resource_absent' AND $3::text IS NULL
                      AND COALESCE(ended_at, egress_started_at) <= now() - $5::interval)
              )
            RETURNING id, attempt_id, kind, reconciliation, reconciled_object_id
            ), adopted AS (
                UPDATE public.healing_attempts AS attempt
                SET pr_number = reconciled.reconciled_object_id::integer,
                    pr_url = $6::text,
                    updated_at = now()
                FROM reconciled
                WHERE attempt.id = reconciled.attempt_id
                  AND attempt.pr_number IS NULL
                  AND attempt.pr_url IS NULL
                  AND CASE
                      WHEN reconciled.kind = 'pr_create'
                          AND reconciled.reconciliation = 'resource_found'
                          AND reconciled.reconciled_object_id ~ '^[1-9][0-9]{0,9}$'
                          AND $6::text LIKE ('%/pull/' || reconciled.reconciled_object_id)
                      THEN reconciled.reconciled_object_id::bigint <= 2147483647
                      ELSE false
                  END
            )
            SELECT id FROM reconciled
            """,
            operation_id,
            observation,
            observed_object_id,
            expected_stage,
            absence_settle,
            adopted_pr_url,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc
    return recorded is not None
