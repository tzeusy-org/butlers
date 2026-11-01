"""Read-only reconciliation of held QA publication operations (bu-mc01y5.29).

REQ-qa-publication-authority-006: an ambiguous operation is released only by an
exact read-only observation of its bound resource, never by a retry.  A lost
``pr_create`` response is adopted by head/base match so the attempt can finish
instead of wedging forever.  No live provider is contacted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.publication.binding import OperationKind
from butlers.core.qa.publication.publisher import PublicationStatus, QaPublisher
from butlers.core.qa.publication.reconcile import (
    ABSENCE_SETTLE,
    reconcile_operation,
    sweep_held_operations,
)
from butlers.core.qa.publication.transport import AmbiguousOutcome
from tests.core.qa.publication_fakes import FakeLedger, FakeProvider, binding_for, synthetic_export

pytestmark = pytest.mark.unit

_EXPORT = synthetic_export()
_BINDING = binding_for(_EXPORT)


async def _publish(provider: FakeProvider, ledger: FakeLedger, attempt_id: uuid.UUID):
    whitelist = MagicMock()
    whitelist.ensure_loaded = AsyncMock()
    whitelist.is_allowed = MagicMock(return_value=(True, "ok"))
    publisher = QaPublisher(provider=provider, ledger=ledger, repo_root=Path("/r"))
    return await publisher.publish(
        attempt_id=attempt_id,
        binding=_BINDING,
        export=_EXPORT,
        title="fix(qa): ValueError in module:1",
        body="## Root Cause\nA missing guard.",
        labels=["self-healing", "automated"],
        whitelist=whitelist,
    )


def _settled() -> datetime:
    return datetime.now(UTC) + ABSENCE_SETTLE + timedelta(seconds=1)


async def _reconcile(provider, ledger, attempt_id, kind, binding=_BINDING, now=_settled):
    return await reconcile_operation(
        ledger, provider, attempt_id=attempt_id, kind=kind, binding=binding, now=now
    )


@pytest.mark.parametrize(
    ("lost", "kind"),
    [
        ("create_pull_request", OperationKind.PR_CREATE),
        ("update_ref", OperationKind.BRANCH_PUSH),
    ],
    ids=["pr_create-adopted", "branch_push-found"],
)
async def test_found_resource_releases_the_hold_and_a_rerun_finishes(lost, kind) -> None:
    provider, ledger, attempt_id = FakeProvider(lose_response_on=lost), FakeLedger(), uuid.uuid4()
    first = await _publish(provider, ledger, attempt_id)
    assert first.status is PublicationStatus.AMBIGUOUS
    effects = list(provider.calls)

    result = await _reconcile(provider, ledger, attempt_id, kind)

    assert (result.status, result.observation) == ("reconciled", "resource_found")
    assert provider.calls == effects  # reads only: no effect reached the provider
    assert not ledger.row(attempt_id, kind).holds_lineage

    provider.lose_response_on = None
    again = await _publish(provider, ledger, attempt_id)
    assert again.status is PublicationStatus.PUBLISHED
    (pr_number,) = provider.prs
    assert again.pr_number == pr_number
    names = [name for name, _ in provider.calls]
    assert names.count("update_ref") == 1 and names.count("create_pull_request") == 1
    assert provider.prs[pr_number]["labels"] == ("self-healing", "automated")


async def test_absent_pr_releases_the_hold_only_after_settling_and_never_recreates() -> None:
    provider = FakeProvider(raise_on_create=AmbiguousOutcome("timed out before the remote"))
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)

    # A create that timed out may still land: absence is not trusted until it settles.
    early = await _reconcile(
        provider, ledger, attempt_id, OperationKind.PR_CREATE, now=lambda: datetime.now(UTC)
    )
    assert (early.status, early.category) == ("held", "absence_settling")
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).holds_lineage

    ledger.clock = _settled  # the database clock has moved on too
    result = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_CREATE)

    assert (result.status, result.observation, result.object_id) == (
        "reconciled",
        "resource_absent",
        None,
    )
    provider.raise_on_create = None
    again = await _publish(provider, ledger, attempt_id)
    assert (again.status, again.category) == (PublicationStatus.FAILED, "pr_create_reconciled")
    assert [name for name, _ in provider.calls].count("create_pull_request") == 1


async def _in_flight_create(
    ledger: FakeLedger, attempt_id: uuid.UUID, *, bound_at: datetime, egress_at: datetime | None
) -> None:
    """A ``pr_create`` bound at ``bound_at`` whose egress began at ``egress_at``, no outcome."""
    provider = FakeProvider(raise_on_create=AmbiguousOutcome("timed out before the remote"))
    await _publish(provider, ledger, attempt_id)
    op = ledger.row(attempt_id, OperationKind.PR_CREATE)
    ledger._replace(
        op.id,
        stage="egress_started",
        started_at=bound_at,
        egress_started_at=egress_at,
        ended_at=None,
    )


async def test_resumed_bind_measures_the_settle_from_egress_not_from_the_bind() -> None:
    # Bound long ago, interrupted, resumed: the push/create is in flight right now.
    ledger, attempt_id, now = FakeLedger(), uuid.uuid4(), datetime.now(UTC)
    await _in_flight_create(ledger, attempt_id, bound_at=now - 2 * ABSENCE_SETTLE, egress_at=now)

    result = await _reconcile(
        FakeProvider(), ledger, attempt_id, OperationKind.PR_CREATE, now=lambda: now
    )

    assert (result.status, result.category) == ("held", "absence_settling")
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).holds_lineage


async def test_absence_without_an_egress_timestamp_never_settles() -> None:
    ledger, attempt_id, now = FakeLedger(), uuid.uuid4(), datetime.now(UTC)
    await _in_flight_create(ledger, attempt_id, bound_at=now - 2 * ABSENCE_SETTLE, egress_at=None)

    result = await _reconcile(FakeProvider(), ledger, attempt_id, OperationKind.PR_CREATE)

    assert (result.status, result.category) == ("held", "absence_unanchored")
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).holds_lineage


async def test_ledger_settle_decides_even_when_the_reconciler_clock_runs_ahead() -> None:
    provider = FakeProvider(raise_on_create=AmbiguousOutcome("timed out before the remote"))
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)

    # The host clock says settled; the ledger's (database) clock does not.
    result = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_CREATE)

    assert (result.status, result.category) == ("held", "not_recorded")
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).holds_lineage


class _OutcomeRacesTheSweep(FakeLedger):
    """The dispatcher records ``ambiguous`` between the sweep's read and its write."""

    async def reconcile(self, operation_id, observation, **kwargs) -> bool:
        assert await self.record(operation_id, "ambiguous", new_object_id=None)
        return await super().reconcile(operation_id, observation, **kwargs)


async def test_outcome_recorded_mid_sweep_wins_over_the_absence() -> None:
    ledger, attempt_id, now = _OutcomeRacesTheSweep(), uuid.uuid4(), datetime.now(UTC)
    old = now - 2 * ABSENCE_SETTLE
    await _in_flight_create(ledger, attempt_id, bound_at=old, egress_at=old)

    result = await _reconcile(
        FakeProvider(), ledger, attempt_id, OperationKind.PR_CREATE, now=lambda: now
    )

    assert (result.status, result.category) == ("held", "not_recorded")
    row = ledger.row(attempt_id, OperationKind.PR_CREATE)
    assert (row.stage, row.holds_lineage) == ("ambiguous", True)


def _second_pr(provider: FakeProvider) -> None:
    provider.prs[999] = {
        "head": _BINDING.branch,
        "base": _BINDING.base_ref,
        "title": "",
        "body": "",
    }


def _moved_branch(provider: FakeProvider) -> None:
    provider.refs[_BINDING.branch] = "d" * 40


def _failing_read(provider: FakeProvider) -> None:
    provider.find_pull_requests = AsyncMock(side_effect=RuntimeError("secret-bearing body"))
    provider.read_ref = AsyncMock(side_effect=RuntimeError("secret-bearing body"))


def _foreign_origin(provider: FakeProvider) -> None:
    provider.repository = "evil/fork"


@pytest.mark.parametrize(
    ("lost", "kind", "tamper", "binding", "category"),
    [
        (
            "create_pull_request",
            OperationKind.PR_CREATE,
            _second_pr,
            _BINDING,
            "multiple_candidates",
        ),
        ("update_ref", OperationKind.BRANCH_PUSH, _moved_branch, _BINDING, "unexpected_head"),
        ("create_pull_request", OperationKind.PR_CREATE, _failing_read, _BINDING, "read_failed"),
        (
            "create_pull_request",
            OperationKind.PR_CREATE,
            _foreign_origin,
            _BINDING,
            "foreign_origin",
        ),
        (
            "create_pull_request",
            OperationKind.PR_CREATE,
            None,
            binding_for(_EXPORT, base_ref="release"),
            "binding_mismatch",
        ),
    ],
    ids=["two-prs", "moved-branch", "read-error", "foreign-origin", "other-binding"],
)
async def test_inexact_observation_keeps_the_hold(lost, kind, tamper, binding, category) -> None:
    provider, ledger, attempt_id = FakeProvider(lose_response_on=lost), FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)
    if tamper is not None:
        tamper(provider)
    effects = list(provider.calls)

    result = await _reconcile(provider, ledger, attempt_id, kind, binding)

    assert (result.status, result.category) == ("held", category)
    assert ledger.row(attempt_id, kind).holds_lineage
    assert provider.calls == effects


async def test_operation_that_holds_nothing_is_left_alone() -> None:
    provider, ledger, attempt_id = FakeProvider(), FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)

    done = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_CREATE)
    missing = await _reconcile(provider, ledger, uuid.uuid4(), OperationKind.PR_CREATE)

    assert (done.status, done.category) == ("not_held", "succeeded")
    assert (missing.status, missing.category) == ("not_held", "no_operation")
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).stage == "succeeded"


async def test_sweep_rebuilds_each_binding_from_dispatcher_records_and_reconciles() -> None:
    provider, ledger = FakeProvider(lose_response_on="create_pull_request"), FakeLedger()
    adopted, foreign = uuid.uuid4(), uuid.uuid4()
    await _publish(provider, ledger, adopted)
    provider.lose_response_on = "update_ref"
    provider.refs.clear()
    await _publish(provider, ledger, foreign)
    effects = list(provider.calls)

    def row(attempt_id, kind, branch):
        op = ledger.row(attempt_id, kind)
        return {
            "operation_id": op.id,
            "attempt_id": attempt_id,
            "kind": kind.value,
            "branch_name": branch,
            "head_sha": _EXPORT.head_sha,
        }

    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            row(adopted, OperationKind.PR_CREATE, _BINDING.branch),
            # The dispatcher records disagree with the stored digest: never observed.
            row(foreign, OperationKind.BRANCH_PUSH, "qa/finance/somebody-else"),
            {**row(foreign, OperationKind.BRANCH_PUSH, None), "operation_id": uuid.uuid4()},
        ]
    )

    entries = await sweep_held_operations(pool, provider, ledger=ledger, now=_settled)

    assert [(e.kind, e.status, e.category) for e in entries] == [
        ("pr_create", "reconciled", "resource_found"),
        ("branch_push", "held", "binding_mismatch"),
        ("branch_push", "held", "unbound_attempt"),
    ]
    assert not ledger.row(adopted, OperationKind.PR_CREATE).holds_lineage
    assert ledger.row(foreign, OperationKind.BRANCH_PUSH).holds_lineage
    assert provider.calls == effects
    (sql, limit) = pool.fetch.await_args.args
    assert "stage IN ('egress_started', 'ambiguous')" in sql and limit == 50
