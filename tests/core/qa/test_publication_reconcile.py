"""Read-only reconciliation of held QA publication operations (bu-mc01y5.29).

REQ-qa-publication-authority-006: an ambiguous operation is released only by an
exact read-only observation of its bound resource, never by a retry.  A lost
``pr_create`` response is adopted by head/base match so the attempt can finish
instead of wedging forever.  No live provider is contacted.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.publication.binding import (
    OperationKind,
    PublicationBinding,
    pr_text_payload,
    request_digest,
)
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
_LABELS = ("self-healing", "automated")
_PR = 100


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
        labels=list(_LABELS),
        whitelist=whitelist,
    )


def _settled() -> datetime:
    return datetime.now(UTC) + ABSENCE_SETTLE + timedelta(seconds=1)


def _bound(kind: OperationKind) -> PublicationBinding:
    """The binding the publisher used: PR follow-ups bind the created PR (the fake's first)."""
    if kind in (OperationKind.PR_LABELS, OperationKind.PR_UPDATE):
        return dataclasses.replace(_BINDING, pr_number=_PR)
    return _BINDING


async def _reconcile(
    provider, ledger, attempt_id, kind, binding=None, now=_settled, labels=_LABELS
):
    return await reconcile_operation(
        ledger,
        provider,
        attempt_id=attempt_id,
        kind=kind,
        binding=binding or _bound(kind),
        now=now,
        labels=labels,
    )


@pytest.mark.parametrize(
    ("lost", "kind"),
    [
        ("create_pull_request", OperationKind.PR_CREATE),
        ("update_ref", OperationKind.BRANCH_PUSH),
        ("set_labels", OperationKind.PR_LABELS),
    ],
    ids=["pr_create-adopted", "branch_push-found", "pr_labels-found"],
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
    # Only an adoption names a PR the attempt did not know; the case API reads it there.
    adopted = {attempt_id: (_PR, f"https://github.com/{_BINDING.repository}/pull/{_PR}")}
    assert ledger.attempt_prs == (adopted if kind is OperationKind.PR_CREATE else {})

    provider.lose_response_on = None
    again = await _publish(provider, ledger, attempt_id)
    assert again.status is PublicationStatus.PUBLISHED
    (pr_number,) = provider.prs
    assert again.pr_number == pr_number
    names = [name for name, _ in provider.calls]
    assert names.count("update_ref") == 1 and names.count("create_pull_request") == 1
    assert names.count("set_labels") == 1
    assert provider.prs[pr_number]["labels"] == _LABELS


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


async def test_absent_labels_release_the_hold_after_settling_and_are_never_reapplied() -> None:
    provider = FakeProvider(raise_on_labels=AmbiguousOutcome("timed out before the remote"))
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)
    assert ledger.row(attempt_id, OperationKind.PR_LABELS).expected_object_id == str(_PR)

    ledger.clock = _settled
    result = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_LABELS)

    assert (result.status, result.observation) == ("reconciled", "resource_absent")
    provider.raise_on_labels = None
    again = await _publish(provider, ledger, attempt_id)
    assert (again.status, again.category) == (PublicationStatus.FAILED, "pr_labels_reconciled")
    assert [name for name, _ in provider.calls].count("set_labels") == 1


async def _held_update(provider: FakeProvider, ledger: FakeLedger, attempt_id: uuid.UUID):
    """A ``pr_update`` of the published PR whose response was lost (no publisher path yet)."""
    await _publish(provider, ledger, attempt_id)
    binding = _bound(OperationKind.PR_UPDATE)
    operation = await ledger.begin(
        attempt_id=attempt_id,
        kind=OperationKind.PR_UPDATE,
        binding=binding,
        request_digest=request_digest(pr_text_payload("fix(qa): retitled", "New body.")),
        expected_object_id=str(_PR),
    )
    assert await ledger.start_egress(operation.id)
    assert await ledger.record(operation.id, "ambiguous", new_object_id=None)


@pytest.mark.parametrize(
    ("title", "observation"),
    [
        ("fix(qa): retitled", "resource_found"),
        ("fix(qa): ValueError in module:1", "resource_absent"),
    ],
    ids=["text-landed", "text-not-landed"],
)
async def test_pr_update_is_observed_by_the_bound_pr_text_digest(title, observation) -> None:
    provider, ledger, attempt_id = FakeProvider(), FakeLedger(), uuid.uuid4()
    await _held_update(provider, ledger, attempt_id)
    provider.prs[_PR] |= {"title": title, "body": "New body."}
    ledger.clock = _settled
    effects = list(provider.calls)

    result = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_UPDATE, labels=None)

    expected_object = str(_PR) if observation == "resource_found" else None
    assert (result.status, result.observation, result.object_id) == (
        "reconciled",
        observation,
        expected_object,
    )
    assert provider.calls == effects


def _other_head(provider: FakeProvider, ledger: FakeLedger, attempt_id: uuid.UUID) -> None:
    provider.prs[_PR]["head"] = "qa/finance/somebody-else"


def _missing_pr(provider: FakeProvider, ledger: FakeLedger, attempt_id: uuid.UUID) -> None:
    del provider.prs[_PR]


def _unbound_row(provider: FakeProvider, ledger: FakeLedger, attempt_id: uuid.UUID) -> None:
    # A row bound before follow-ups named their PR: nothing exact to observe.
    ledger._replace(ledger.row(attempt_id, OperationKind.PR_LABELS).id, expected_object_id=None)


@pytest.mark.parametrize(
    ("tamper", "labels", "category"),
    [
        (None, ("self-healing",), "payload_mismatch"),
        (None, None, "labels_unbound"),
        (_other_head, _LABELS, "foreign_pr"),
        (_missing_pr, _LABELS, "pr_missing"),
        (_unbound_row, _LABELS, "unbound_pr"),
    ],
    ids=["other-labels", "no-labels", "other-head", "missing-pr", "unbound-row"],
)
async def test_inexact_label_observation_keeps_the_hold(tamper, labels, category) -> None:
    provider = FakeProvider(lose_response_on="set_labels")
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)
    if tamper is not None:
        tamper(provider, ledger, attempt_id)

    result = await _reconcile(provider, ledger, attempt_id, OperationKind.PR_LABELS, labels=labels)

    assert (result.status, result.category) == ("held", category)
    assert ledger.row(attempt_id, OperationKind.PR_LABELS).holds_lineage


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
    adopted, foreign, labelled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _publish(provider, ledger, adopted)
    provider.lose_response_on = "update_ref"
    provider.refs.clear()
    await _publish(provider, ledger, foreign)
    provider.lose_response_on = "set_labels"
    provider.refs.clear()
    provider.prs.clear()
    await _publish(provider, ledger, labelled)
    effects = list(provider.calls)

    def row(attempt_id, kind, branch, pr_number=None):
        op = ledger.row(attempt_id, kind)
        return {
            "operation_id": op.id,
            "attempt_id": attempt_id,
            "kind": kind.value,
            "branch_name": branch,
            "head_sha": _EXPORT.head_sha,
            "pr_number": pr_number,
        }

    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            row(adopted, OperationKind.PR_CREATE, _BINDING.branch),
            # The dispatcher records disagree with the stored digest: never observed.
            row(foreign, OperationKind.BRANCH_PUSH, "qa/finance/somebody-else"),
            {**row(foreign, OperationKind.BRANCH_PUSH, None), "operation_id": uuid.uuid4()},
            # The PR comes from the attempt's own created-PR record.
            row(labelled, OperationKind.PR_LABELS, _BINDING.branch, str(_PR)),
            {**row(labelled, OperationKind.PR_LABELS, _BINDING.branch), "attempt_id": foreign},
        ]
    )

    entries = await sweep_held_operations(
        pool, provider, ledger=ledger, now=_settled, labels=_LABELS
    )

    assert [(e.kind, e.status, e.category) for e in entries] == [
        ("pr_create", "reconciled", "resource_found"),
        ("branch_push", "held", "binding_mismatch"),
        ("branch_push", "held", "unbound_attempt"),
        ("pr_labels", "reconciled", "resource_found"),
        ("pr_labels", "held", "unbound_attempt"),
    ]
    assert not ledger.row(adopted, OperationKind.PR_CREATE).holds_lineage
    assert ledger.row(foreign, OperationKind.BRANCH_PUSH).holds_lineage
    assert not ledger.row(labelled, OperationKind.PR_LABELS).holds_lineage
    assert provider.calls == effects
    (sql, limit) = pool.fetch.await_args.args
    assert "stage IN ('egress_started', 'ambiguous')" in sql and limit == 50
