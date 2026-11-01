"""Read-only reconciliation of held QA publication operations (bu-mc01y5.29).

REQ-qa-publication-authority-006: an ambiguous operation is released only by an
exact read-only observation of its bound resource, never by a retry.  A lost
``pr_create`` response is adopted by head/base match so the attempt can finish
instead of wedging forever.  No live provider is contacted.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.publication.binding import OperationKind
from butlers.core.qa.publication.publisher import PublicationStatus, QaPublisher
from butlers.core.qa.publication.reconcile import reconcile_operation
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


async def _reconcile(provider, ledger, attempt_id, kind, binding=_BINDING):
    return await reconcile_operation(
        ledger, provider, attempt_id=attempt_id, kind=kind, binding=binding
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


async def test_absent_pr_releases_the_hold_without_a_fresh_create() -> None:
    provider = FakeProvider(raise_on_create=AmbiguousOutcome("timed out before the remote"))
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    await _publish(provider, ledger, attempt_id)

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
