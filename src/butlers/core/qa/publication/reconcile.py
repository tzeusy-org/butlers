"""Read-only reconciliation of held QA publication operations (REQ-qa-publication-authority-006).

An operation left in ``egress_started`` or ``ambiguous`` holds its fingerprint
lineage until an exact read-only observation of its bound resource is recorded.
This module makes that observation through :class:`PublicationReader`, whose
protocol names only two reads, and records it with
:func:`state.record_reconciliation`.  It never retries, creates or deletes a
remote resource, and any doubt leaves the hold in place:

- ``branch_push``: the bound ``qa/`` branch at exactly the bound head is
  ``resource_found``; a missing branch is ``resource_absent``; any other head
  stays held.
- ``pr_create``: the operation bound no PR number before egress, so the adoption
  rule is a head/base match.  Exactly one PR (open or closed) from the bound head
  branch onto the bound base is adopted as ``resource_found``; none is
  ``resource_absent``; more than one stays held.
- ``pr_update`` and ``pr_labels`` bind no observable object and stay held.

Absence is trusted only once it has settled: a create or push that timed out
can still land remotely, so a ``resource_absent`` observation is recorded only
when the operation's last ledger timestamp is at least :data:`ABSENCE_SETTLE`
old.  Otherwise a late-landing PR would arrive after its lineage was freed and a
fresh attempt could open a duplicate.

:func:`sweep_held_operations` is the batch entry point: it rebuilds each held
``branch_push``/``pr_create`` binding from the dispatcher's own records (the
attempt's branch and the retained export's head) and reconciles it.  The stored
digest still decides, so a rebuilt binding that differs in any field is refused
with ``binding_mismatch`` and observes nothing.

The caller must pass the dispatcher-held binding the operation was bound with;
a binding whose digest differs from the stored one is refused, so a reconciler
can never observe (or adopt) a resource the operation did not target.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import asyncpg

from butlers.core.qa.publication import state
from butlers.core.qa.publication.binding import (
    QA_BASE_REF,
    InvalidBinding,
    OperationKind,
    PublicationBinding,
)
from butlers.core.qa.publication.state import Observation, PublicationOperation, PublicationRefused

logger = logging.getLogger(__name__)

_QA_BRANCH_PREFIX = "qa/"
_DATASTORE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)

#: How long an operation's last ledger write must be in the past before an absent
#: resource is believed.  It must exceed the longest a provider call can stay in
#: flight (client timeout plus GitHub's own processing of an accepted request).
ABSENCE_SETTLE = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PublicationReader(Protocol):
    """Read-only view of the fixed-origin repository.  There is no write effect."""

    repository: str

    async def read_ref(self, *, branch: str) -> str | None:
        """Return the object id ``refs/heads/<branch>`` points at, or ``None`` if absent."""
        ...

    async def find_pull_requests(self, *, head: str, base: str) -> tuple[int, ...]:
        """Return the numbers of every PR (any state) from ``head`` onto ``base``."""
        ...


class ReconciliationLedger(Protocol):
    """The ledger reads and the single write reconciliation needs."""

    async def get(
        self, attempt_id: uuid.UUID, kind: OperationKind
    ) -> PublicationOperation | None: ...

    async def reconcile(
        self,
        operation_id: uuid.UUID,
        observation: Observation,
        *,
        observed_object_id: str | None,
    ) -> bool: ...


class PoolReconciliationLedger:
    """:class:`ReconciliationLedger` over the real ledger table."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, attempt_id: uuid.UUID, kind: OperationKind) -> PublicationOperation | None:
        return await state.get_operation(self._pool, attempt_id, kind)

    async def reconcile(
        self,
        operation_id: uuid.UUID,
        observation: Observation,
        *,
        observed_object_id: str | None,
    ) -> bool:
        return await state.record_reconciliation(
            self._pool, operation_id, observation, observed_object_id=observed_object_id
        )


ReconciliationStatus = Literal["reconciled", "held", "not_held"]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of one reconciliation.  ``category`` is stable and content-free."""

    status: ReconciliationStatus
    category: str
    observation: Observation | None = None
    object_id: str | None = None


def _held(category: str) -> ReconciliationResult:
    return ReconciliationResult("held", category)


def _is_pr_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


async def _observe(
    kind: OperationKind,
    operation: PublicationOperation,
    binding: PublicationBinding,
    reader: PublicationReader,
) -> tuple[Observation, str | None] | str:
    """Return the exact observation, or a category string that keeps the hold."""
    if kind is OperationKind.BRANCH_PUSH:
        if not binding.branch.startswith(_QA_BRANCH_PREFIX) or binding.branch == binding.base_ref:
            return "foreign_ref"
        head = await reader.read_ref(branch=binding.branch)
        if head is None:
            return ("resource_absent", None)
        if head == operation.expected_object_id:
            return ("resource_found", head)
        return "unexpected_head"
    if kind is OperationKind.PR_CREATE:
        if binding.pr_number is not None or operation.expected_object_id is not None:
            return "unbound_pr_create"
        numbers = await reader.find_pull_requests(head=binding.branch, base=binding.base_ref)
        if not isinstance(numbers, tuple) or not all(_is_pr_number(n) for n in numbers):
            return "invalid_observation"
        candidates = set(numbers)
        if not candidates:
            return ("resource_absent", None)
        if len(candidates) > 1:
            return "multiple_candidates"
        (number,) = candidates
        return ("resource_found", str(number))
    return "unsupported_kind"


async def reconcile_operation(
    ledger: ReconciliationLedger,
    reader: PublicationReader,
    *,
    attempt_id: uuid.UUID,
    kind: OperationKind,
    binding: PublicationBinding,
    settle: timedelta = ABSENCE_SETTLE,
    now: Callable[[], datetime] = _utcnow,
) -> ReconciliationResult:
    """Observe the held operation's bound resource read-only and record the exact result.

    Returns ``not_held`` when there is nothing to release, ``reconciled`` when the
    hold was released, and ``held`` (with the reason) otherwise.  Every failure,
    including a reader or ledger error, keeps the hold.
    """
    if reader.repository != binding.repository:
        return _held("foreign_origin")
    try:
        operation = await ledger.get(attempt_id, kind)
    except PublicationRefused as exc:
        return _held(exc.code)
    if operation is None:
        return ReconciliationResult("not_held", "no_operation")
    if not operation.holds_lineage:
        return ReconciliationResult("not_held", operation.stage)
    if operation.binding_digest != binding.digest():
        return _held("binding_mismatch")

    try:
        observed = await _observe(kind, operation, binding, reader)
    except Exception as exc:  # noqa: BLE001 — a failed read proves nothing; keep the hold
        # Type name only: the message can carry repository or provider content.
        logger.warning(
            "QA publication reconciliation read for %s raised %s", kind.value, type(exc).__name__
        )
        return _held("read_failed")
    if isinstance(observed, str):
        return _held(observed)

    observation, object_id = observed
    if observation == "resource_absent":
        last_write = operation.ended_at or operation.started_at
        if now() - last_write < settle:
            return _held("absence_settling")
    try:
        recorded = await ledger.reconcile(operation.id, observation, observed_object_id=object_id)
    except PublicationRefused as exc:
        return _held(exc.code)
    if not recorded:
        return _held("not_recorded")
    return ReconciliationResult("reconciled", observation, observation, object_id)


@dataclass(frozen=True, slots=True)
class SweepEntry:
    """One held operation the sweep visited.  Identifiers and categories only."""

    operation_id: uuid.UUID
    attempt_id: uuid.UUID
    kind: str
    status: ReconciliationStatus
    category: str


async def sweep_held_operations(
    pool: Any,
    reader: PublicationReader,
    *,
    ledger: ReconciliationLedger | None = None,
    limit: int = 50,
    settle: timedelta = ABSENCE_SETTLE,
    now: Callable[[], datetime] = _utcnow,
) -> tuple[SweepEntry, ...]:
    """Reconcile up to ``limit`` held ``branch_push``/``pr_create`` operations, oldest first.

    Each binding is rebuilt from dispatcher records: the reader's fixed origin,
    the attempt's ``branch_name``, :data:`QA_BASE_REF` and the retained export's
    ``head_sha``.  A row whose records cannot form a binding is reported
    ``unbound_attempt`` and never read.  ``pr_update``/``pr_labels`` holds are
    not swept: they bind no observable object.  A failed candidate query raises
    :class:`PublicationRefused`; per-operation failures keep that hold only.
    """
    ledger = ledger if ledger is not None else PoolReconciliationLedger(pool)
    try:
        rows = await pool.fetch(
            """
            SELECT op.id AS operation_id, op.attempt_id, op.kind,
                   attempt.branch_name, export.head_sha
            FROM public.qa_publication_operations AS op
            JOIN public.healing_attempts AS attempt ON attempt.id = op.attempt_id
            LEFT JOIN public.qa_publication_exports AS export
                ON export.attempt_id = op.attempt_id
            WHERE op.stage IN ('egress_started', 'ambiguous')
              AND op.kind IN ('branch_push', 'pr_create')
            ORDER BY op.started_at, op.id
            LIMIT $1
            """,
            limit,
        )
    except _DATASTORE_ERRORS as exc:
        raise PublicationRefused("datastore_unavailable") from exc

    entries: list[SweepEntry] = []
    for row in rows:
        kind = OperationKind(row["kind"])
        try:
            binding = PublicationBinding(
                repository=reader.repository,
                branch=row["branch_name"],
                base_ref=QA_BASE_REF,
                head_sha=row["head_sha"],
            )
        except (InvalidBinding, TypeError):
            result = _held("unbound_attempt")
        else:
            result = await reconcile_operation(
                ledger,
                reader,
                attempt_id=row["attempt_id"],
                kind=kind,
                binding=binding,
                settle=settle,
                now=now,
            )
        entries.append(
            SweepEntry(
                row["operation_id"], row["attempt_id"], kind.value, result.status, result.category
            )
        )
    return tuple(entries)
