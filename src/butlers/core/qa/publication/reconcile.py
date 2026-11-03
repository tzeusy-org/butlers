"""Read-only reconciliation of held QA publication operations (REQ-qa-publication-authority-006).

An operation left in ``egress_started`` or ``ambiguous`` holds its fingerprint
lineage until an exact read-only observation of its bound resource is recorded.
This module makes that observation through :class:`PublicationReader`, whose
protocol names only three reads, and records it with
:func:`state.record_reconciliation`.  It never retries, creates or deletes a
remote resource, and any doubt leaves the hold in place:

- ``branch_push``: the bound ``qa/`` branch at exactly the bound head is
  ``resource_found``; a missing branch is ``resource_absent``; any other head
  stays held.
- ``pr_create``: the operation bound no PR number before egress, so the adoption
  rule is a head/base match.  Exactly one PR (open or closed) from the bound head
  branch onto the bound base is adopted as ``resource_found``; none is
  ``resource_absent``; more than one stays held.  The same write records the
  adopted PR's number and URL on the attempt (``healing_attempts.pr_number`` /
  ``pr_url``) when it names no PR yet, so the case API shows the PR.
- ``pr_update`` and ``pr_labels`` bind the bound PR's number as their expected
  object (:func:`binding.expected_object_id`), and the reconciler reads that PR.
  It must still run from the bound head onto the bound base; anything else stays
  held.  A ``pr_update`` is ``resource_found`` when the digest of the PR's
  observed title and body equals the stored request digest, else
  ``resource_absent``.  A ``pr_labels`` needs the dispatcher-held labels, whose
  digest must equal the stored request digest (else ``payload_mismatch``); it is
  ``resource_found`` when every bound label is on the PR, else
  ``resource_absent``.  Both effects are idempotent, so a late landing after an
  absence was recorded duplicates nothing.

Absence is trusted only once it has settled: a create or push that timed out
can still land remotely, so a ``resource_absent`` observation is recorded only
when the operation's outcome (``ended_at``) or, while still in flight, its egress
(``egress_started_at``) is at least :data:`ABSENCE_SETTLE` old.  Otherwise a
late-landing PR would arrive after its lineage was freed and a fresh attempt
could open a duplicate.  The bind time (``started_at``) never counts: a resumed
bind can egress long after it.  The database enforces the settle with its own
clock inside the guarded write and requires the row to still be at the stage
this module observed, so an outcome recorded mid-sweep wins; the check here is
only a cheap pre-filter.

:func:`sweep_held_operations` is the batch entry point: it rebuilds each held
binding from the dispatcher's own records (the attempt's branch, the retained
export's head and, for PR follow-ups, the attempt's own created-PR record) and
reconciles it.  The stored digest still decides, so a rebuilt binding that
differs in any field is refused with ``binding_mismatch`` and observes nothing.

The caller must pass the dispatcher-held binding the operation was bound with;
a binding whose digest differs from the stored one is refused, so a reconciler
can never observe (or adopt) a resource the operation did not target.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
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
    labels_payload,
    pr_text_payload,
    pull_request_url,
    request_digest,
)
from butlers.core.qa.publication.state import (
    ABSENCE_SETTLE,
    Observation,
    PublicationOperation,
    PublicationRefused,
)

logger = logging.getLogger(__name__)

_QA_BRANCH_PREFIX = "qa/"
_DATASTORE_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PullRequestView:
    """The observed state of one PR in the fixed-origin repository.

    ``head`` and ``base`` are branch names; a reader reports them only once both
    sides are proven to live in its own repository.
    """

    number: int
    head: str
    base: str
    title: str
    body: str
    labels: tuple[str, ...]


class PublicationReader(Protocol):
    """Read-only view of the fixed-origin repository.  There is no write effect."""

    repository: str

    async def read_ref(self, *, branch: str) -> str | None:
        """Return the object id ``refs/heads/<branch>`` points at, or ``None`` if absent."""
        ...

    async def find_pull_requests(self, *, head: str, base: str) -> tuple[int, ...]:
        """Return the numbers of every PR (any state) from ``head`` onto ``base``."""
        ...

    async def read_pull_request(self, *, number: int) -> PullRequestView | None:
        """Return PR ``number``'s observed state, or ``None`` if it provably does not exist."""
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
        expected_stage: str,
        absence_settle: timedelta,
        adopted_pr_url: str | None = None,
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
        expected_stage: str,
        absence_settle: timedelta,
        adopted_pr_url: str | None = None,
    ) -> bool:
        return await state.record_reconciliation(
            self._pool,
            operation_id,
            observation,
            observed_object_id=observed_object_id,
            expected_stage=expected_stage,
            absence_settle=absence_settle,
            adopted_pr_url=adopted_pr_url,
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
    labels: tuple[str, ...] | None,
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
    if kind in (OperationKind.PR_UPDATE, OperationKind.PR_LABELS):
        return await _observe_pr_state(kind, operation, binding, reader, labels)
    return "unsupported_kind"


async def _observe_pr_state(
    kind: OperationKind,
    operation: PublicationOperation,
    binding: PublicationBinding,
    reader: PublicationReader,
    labels: tuple[str, ...] | None,
) -> tuple[Observation, str | None] | str:
    """Observe the requested state on the bound PR (``pr_update``/``pr_labels``)."""
    number = binding.pr_number
    if number is None or operation.expected_object_id != str(number):
        return "unbound_pr"
    if kind is OperationKind.PR_LABELS:
        # Checked before any read: the labels must be the ones the operation bound.
        if labels is None:
            return "labels_unbound"
        if request_digest(labels_payload(labels)) != operation.request_digest:
            return "payload_mismatch"
    pr = await reader.read_pull_request(number=number)
    if pr is None:
        # The bound PR existed when the operation was bound; its absence proves nothing.
        return "pr_missing"
    if not isinstance(pr, PullRequestView) or pr.number != number:
        return "invalid_observation"
    if pr.head != binding.branch or pr.base != binding.base_ref:
        return "foreign_pr"
    if kind is OperationKind.PR_UPDATE:
        landed = request_digest(pr_text_payload(pr.title, pr.body)) == operation.request_digest
    else:
        assert labels is not None
        landed = set(labels) <= set(pr.labels)
    return ("resource_found", str(number)) if landed else ("resource_absent", None)


async def reconcile_operation(
    ledger: ReconciliationLedger,
    reader: PublicationReader,
    *,
    attempt_id: uuid.UUID,
    kind: OperationKind,
    binding: PublicationBinding,
    labels: Sequence[str] | None = None,
    settle: timedelta = ABSENCE_SETTLE,
    now: Callable[[], datetime] = _utcnow,
) -> ReconciliationResult:
    """Observe the held operation's bound resource read-only and record the exact result.

    ``labels`` are the dispatcher-held labels a ``pr_labels`` operation was bound
    with; other kinds ignore them.  Returns ``not_held`` when there is nothing to
    release, ``reconciled`` when the hold was released, and ``held`` (with the
    reason) otherwise.  Every failure, including a reader or ledger error, keeps
    the hold.
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
        bound_labels = None if labels is None else tuple(labels)
        observed = await _observe(kind, operation, binding, reader, bound_labels)
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
        # Pre-filter only; record_reconciliation re-checks against database time.
        anchor = operation.ended_at or operation.egress_started_at
        if anchor is None:
            return _held("absence_unanchored")
        if now() - anchor < settle:
            return _held("absence_settling")
    adopted_pr_url = None
    if kind is OperationKind.PR_CREATE and observation == "resource_found" and object_id:
        # The attempt never learned this PR (its create response was lost): name it on
        # the attempt in the same write so the case API shows the adopted PR.
        adopted_pr_url = pull_request_url(binding.repository, int(object_id))
    try:
        recorded = await ledger.reconcile(
            operation.id,
            observation,
            observed_object_id=object_id,
            expected_stage=operation.stage,
            absence_settle=settle,
            adopted_pr_url=adopted_pr_url,
        )
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
    labels: Sequence[str] | None = None,
    limit: int = 50,
    settle: timedelta = ABSENCE_SETTLE,
    now: Callable[[], datetime] = _utcnow,
) -> tuple[SweepEntry, ...]:
    """Reconcile up to ``limit`` held operations, oldest first.

    Each binding is rebuilt from dispatcher records: the reader's fixed origin,
    the attempt's ``branch_name``, :data:`QA_BASE_REF`, the retained export's
    ``head_sha`` and, for ``pr_update``/``pr_labels``, the PR number the attempt's
    own ``pr_create`` recorded (succeeded, or reconciled ``resource_found``).
    ``labels`` are the dispatcher's configured PR labels for ``pr_labels`` holds;
    the stored request digest still decides.  A row whose records cannot form a
    binding is reported ``unbound_attempt`` and never read.  A failed candidate
    query raises :class:`PublicationRefused`; per-operation failures keep that
    hold only.
    """
    ledger = ledger if ledger is not None else PoolReconciliationLedger(pool)
    try:
        rows = await pool.fetch(
            """
            SELECT op.id AS operation_id, op.attempt_id, op.kind,
                   attempt.branch_name, export.head_sha, created.new_object_id AS pr_number
            FROM public.qa_publication_operations AS op
            JOIN public.healing_attempts AS attempt ON attempt.id = op.attempt_id
            LEFT JOIN public.qa_publication_exports AS export
                ON export.attempt_id = op.attempt_id
            LEFT JOIN public.qa_publication_operations AS created
                ON created.attempt_id = op.attempt_id
               AND created.kind = 'pr_create'
               AND op.kind IN ('pr_update', 'pr_labels')
               AND (created.stage = 'succeeded'
                    OR (created.stage = 'reconciled'
                        AND created.reconciliation = 'resource_found'))
            WHERE op.stage IN ('egress_started', 'ambiguous')
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
                pr_number=_sweep_pr_number(kind, row["pr_number"]),
            )
        except (InvalidBinding, TypeError, ValueError):
            result = _held("unbound_attempt")
        else:
            result = await reconcile_operation(
                ledger,
                reader,
                attempt_id=row["attempt_id"],
                kind=kind,
                binding=binding,
                labels=labels,
                settle=settle,
                now=now,
            )
        entries.append(
            SweepEntry(
                row["operation_id"], row["attempt_id"], kind.value, result.status, result.category
            )
        )
    return tuple(entries)


def _sweep_pr_number(kind: OperationKind, recorded: str | None) -> int | None:
    """The created PR a follow-up binds; raises when a follow-up has no created-PR record."""
    if kind not in (OperationKind.PR_UPDATE, OperationKind.PR_LABELS):
        return None
    if recorded is None or not recorded.isascii() or not recorded.isdigit():
        raise InvalidBinding("follow-up without a created PR")
    return int(recorded)
