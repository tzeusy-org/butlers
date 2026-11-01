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

The caller must pass the dispatcher-held binding the operation was bound with;
a binding whose digest differs from the stored one is refused, so a reconciler
can never observe (or adopt) a resource the operation did not target.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from butlers.core.qa.publication import state
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding
from butlers.core.qa.publication.state import Observation, PublicationOperation, PublicationRefused

logger = logging.getLogger(__name__)

_QA_BRANCH_PREFIX = "qa/"


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
    try:
        recorded = await ledger.reconcile(operation.id, observation, observed_object_id=object_id)
    except PublicationRefused as exc:
        return _held(exc.code)
    if not recorded:
        return _held("not_recorded")
    return ReconciliationResult("reconciled", observation, observation, object_id)
