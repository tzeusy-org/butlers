"""Closed QA publication transport and publisher against a synthetic provider.

REQ-qa-publication-authority-003 (closed operations): only a non-force update of
the bound QA branch and the bound PR create/update/fixed labels reach the
provider; everything else refuses before any provider call.  No live provider
is ever contacted.
"""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.publication.binding import OperationKind
from butlers.core.qa.publication.publisher import PublicationStatus, QaPublisher
from butlers.core.qa.publication.transport import (
    BranchUpdate,
    ClosedTransport,
    GenericOperation,
    ProviderRejected,
    PullRequestCreate,
    PullRequestLabels,
    PullRequestUpdate,
    TransportRefused,
)
from tests.core.qa.publication_fakes import (
    REPOSITORY,
    FakeLedger,
    FakeProvider,
    binding_for,
    synthetic_export,
)

pytestmark = pytest.mark.unit

_LABELS = ("self-healing", "automated")


def _transport(provider: FakeProvider, *, pr_number: int | None = None) -> ClosedTransport:
    export = synthetic_export()
    return ClosedTransport(
        provider, binding=binding_for(export, pr_number=pr_number), labels=_LABELS, export=export
    )


async def test_bound_branch_and_pr_operations_reach_the_provider() -> None:
    provider = FakeProvider()
    transport = _transport(provider)
    branch, head = "qa/finance/abcdef123456-1", "c" * 40

    await transport.execute(BranchUpdate(REPOSITORY, branch, None, head))
    number = await transport.execute(PullRequestCreate(REPOSITORY, branch, "main", "t", "b"))
    await transport.execute(PullRequestUpdate(REPOSITORY, number, "t2", "b2"))
    await transport.execute(PullRequestLabels(REPOSITORY, number, _LABELS))

    assert [name for name, _ in provider.calls] == [
        "update_ref",
        "create_pull_request",
        "update_pull_request",
        "set_labels",
    ]
    assert provider.refs == {branch: head}


@pytest.mark.parametrize(
    "request_",
    [
        GenericOperation(REPOSITORY, "merge"),
        GenericOperation(REPOSITORY, "review"),
        GenericOperation(REPOSITORY, "approve"),
        GenericOperation(REPOSITORY, "enqueue"),
        BranchUpdate(REPOSITORY, "main", None, "c" * 40),
        BranchUpdate(REPOSITORY, "qa/finance/other", None, "c" * 40),
        BranchUpdate(REPOSITORY, "qa/finance/abcdef123456-1", None, "c" * 40, force=True),
        BranchUpdate(REPOSITORY, "qa/finance/abcdef123456-1", None, "d" * 40),
        BranchUpdate("evil/fork", "qa/finance/abcdef123456-1", None, "c" * 40),
        PullRequestCreate(REPOSITORY, "qa/finance/abcdef123456-1", "release", "t", "b"),
        PullRequestUpdate(REPOSITORY, 7, "t", "b"),
        PullRequestLabels(REPOSITORY, 7, _LABELS),
    ],
    ids=lambda r: f"{type(r).__name__}-{getattr(r, 'name', getattr(r, 'branch', ''))}",
)
async def test_unsupported_or_foreign_requests_make_no_provider_call(request_) -> None:
    provider = FakeProvider()

    with pytest.raises(TransportRefused):
        await _transport(provider).execute(request_)

    assert provider.calls == []


async def test_foreign_pr_and_unbound_labels_refuse_once_a_pr_is_bound() -> None:
    provider = FakeProvider()
    transport = _transport(provider, pr_number=42)

    with pytest.raises(TransportRefused, match="foreign_pr"):
        await transport.execute(PullRequestLabels(REPOSITORY, 43, _LABELS))
    with pytest.raises(TransportRefused, match="unbound_labels"):
        await transport.execute(PullRequestLabels(REPOSITORY, 42, ("self-healing", "merge-me")))
    with pytest.raises(TransportRefused, match="pr_already_bound"):
        await transport.execute(
            PullRequestCreate(REPOSITORY, "qa/finance/abcdef123456-1", "main", "t", "b")
        )
    assert provider.calls == []


def _publisher(provider: FakeProvider, ledger: FakeLedger) -> QaPublisher:
    return QaPublisher(provider=provider, ledger=ledger, repo_root=Path("/r"))


def _whitelist(allowed: bool = True) -> MagicMock:
    whitelist = MagicMock()
    whitelist.ensure_loaded = AsyncMock()
    whitelist.is_allowed = MagicMock(return_value=(allowed, "ok" if allowed else "not_listed"))
    return whitelist


async def _publish(publisher: QaPublisher, attempt_id: uuid.UUID, *, allowed=True, **overrides):
    export = synthetic_export()
    kwargs = {
        "attempt_id": attempt_id,
        "binding": binding_for(export),
        "export": export,
        "title": "fix(qa): ValueError in module:1",
        "body": "## Root Cause\nA missing guard.",
        "labels": list(_LABELS),
        "whitelist": _whitelist(allowed),
    } | overrides
    return await publisher.publish(**kwargs)


async def test_stale_expected_head_refuses_without_force() -> None:
    provider = FakeProvider(refs={"qa/finance/abcdef123456-1": "e" * 40})
    ledger = FakeLedger()
    attempt_id = uuid.uuid4()

    result = await _publish(_publisher(provider, ledger), attempt_id)

    assert (result.status, result.category) == (PublicationStatus.FAILED, "stale_expected_head")
    assert provider.refs == {"qa/finance/abcdef123456-1": "e" * 40}
    assert [name for name, _ in provider.calls] == ["update_ref"]
    assert ledger.row(attempt_id, OperationKind.BRANCH_PUSH).stage == "failed"


def _rejection(code: str, *, replaced_code: str | None = None) -> ProviderRejected:
    exc = ProviderRejected(code)
    if replaced_code is not None:
        exc.code = replaced_code  # a provider rewriting the code after construction
    return exc


@pytest.mark.parametrize(
    ("rejection", "category"),
    [
        (_rejection("conflict"), "provider_rejected_conflict"),
        (_rejection("blocked: secret-token-123 in fix.py"), "provider_rejected_unrecognized"),
        (
            _rejection("conflict", replaced_code="secret-token-123"),
            "provider_rejected_unrecognized",
        ),
    ],
)
async def test_provider_rejection_reports_only_an_approved_code(rejection, category) -> None:
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    provider = FakeProvider(raise_on_push=rejection)

    result = await _publish(_publisher(provider, ledger), attempt_id)

    assert (result.status, result.category) == (PublicationStatus.FAILED, category)
    assert "secret-token-123" not in str(rejection)
    assert ledger.row(attempt_id, OperationKind.BRANCH_PUSH).stage == "failed"


@pytest.mark.parametrize(
    ("code", "status", "stage"),
    [
        # GitHub answers "a pull request already exists for <head>" with 422
        # validation_failed: a live PR may exist, so it must not prove absence.
        ("validation_failed", PublicationStatus.AMBIGUOUS, "ambiguous"),
        ("conflict", PublicationStatus.AMBIGUOUS, "ambiguous"),
        ("raw provider text", PublicationStatus.AMBIGUOUS, "ambiguous"),
        # A retrying client can be throttled after an earlier try created the PR.
        ("rate_limited", PublicationStatus.AMBIGUOUS, "ambiguous"),
        # Access refusals precede any existence check, so they stay definite.
        ("forbidden", PublicationStatus.FAILED, "failed"),
        ("not_found", PublicationStatus.FAILED, "failed"),
    ],
)
async def test_pr_create_rejection_proves_absence_only_for_access_refusals(
    code, status, stage
) -> None:
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    provider = FakeProvider(raise_on_create=ProviderRejected(code))

    result = await _publish(_publisher(provider, ledger), attempt_id)

    assert result.status is status
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).stage == stage
    assert result.pr_number is None


async def test_unknown_error_after_egress_logs_no_content(caplog) -> None:
    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    provider = FakeProvider(raise_on_push=ValueError("body leaked: secret-token-123"))

    with caplog.at_level("WARNING", logger="butlers.core.qa.publication.publisher"):
        result = await _publish(_publisher(provider, ledger), attempt_id)

    assert (result.status, result.category) == (
        PublicationStatus.AMBIGUOUS,
        "branch_push_ambiguous",
    )
    assert caplog.records and all(r.exc_info is None for r in caplog.records)
    assert "secret-token-123" not in caplog.text


async def test_unwhitelisted_or_inactive_attempt_publishes_nothing() -> None:
    provider = FakeProvider()

    refusing, inactive, attempt_id = (
        FakeLedger(),
        FakeLedger(attempt_status="timeout"),
        uuid.uuid4(),
    )
    refused = await _publish(_publisher(provider, refusing), attempt_id, allowed=False)
    timed_out = await _publish(_publisher(provider, inactive), attempt_id)

    assert (refused.status, refused.category) == (
        PublicationStatus.REFUSED,
        "repo_not_whitelisted",
    )
    assert (timed_out.status, timed_out.category) == (
        PublicationStatus.REFUSED,
        "attempt_not_active",
    )
    assert provider.calls == []
    # Refused before retention: a receipt proves the publisher handled it.  The inactive
    # attempt refused after retention, so its retained export is that proof instead.
    assert refusing.refusals == {attempt_id: ("refused", "repo_not_whitelisted")}
    assert inactive.refusals == {} and attempt_id in inactive.exports


async def test_swapped_export_bytes_refuse_before_first_push() -> None:
    provider = FakeProvider()
    export = synthetic_export()
    swapped = dataclasses.replace(export, head_sha="d" * 40)

    ledger, attempt_id = FakeLedger(), uuid.uuid4()
    result = await _publish(
        _publisher(provider, ledger),
        attempt_id,
        export=swapped,
        binding=binding_for(swapped),
    )

    assert (result.status, result.category) == (PublicationStatus.REFUSED, "export_tampered")
    assert provider.calls == []
    assert ledger.refusals == {attempt_id: ("refused", "export_tampered")}


@pytest.mark.parametrize(
    "overrides",
    [
        {"labels": ["self-healing", "999.1.1.1"]},
        {"body": "## Root Cause\nSee below.\n## Evidence\nraw log"},
        {"title": "fix(qa): reported by a%b@x.io"},
    ],
    ids=["label", "raw-evidence-body", "title"],
)
async def test_unsanitizable_pr_text_or_labels_block_before_any_egress(overrides) -> None:
    provider, ledger, attempt_id = FakeProvider(), FakeLedger(), uuid.uuid4()

    result = await _publish(_publisher(provider, ledger), attempt_id, **overrides)

    assert result.status is PublicationStatus.ANONYMIZATION_FAILED
    assert provider.calls == [] and ledger.rows == {}
    assert ledger.refusals == {attempt_id: ("anonymization_failed", result.category)}


async def test_failed_push_leaves_the_sealed_export_retained_outside_the_worktree() -> None:
    provider = FakeProvider(refs={"qa/finance/abcdef123456-1": "e" * 40})
    ledger, attempt_id = FakeLedger(), uuid.uuid4()

    result = await _publish(_publisher(provider, ledger), attempt_id)

    assert result.status is PublicationStatus.FAILED
    assert ledger.exports[attempt_id] == synthetic_export()
    assert ledger.refusals == {}


@pytest.mark.parametrize(
    ("code", "receipt_lost"),
    [("export_unretained", False), ("export_changed", False), ("export_unretained", True)],
)
async def test_unretainable_export_publishes_nothing(code: str, receipt_lost: bool) -> None:
    provider, attempt_id = FakeProvider(), uuid.uuid4()
    ledger = FakeLedger(retain_refusal=code, refusal_unrecordable=receipt_lost)

    result = await _publish(_publisher(provider, ledger), attempt_id)

    # An unrecordable receipt leaves the refusal unchanged; the case API stays ambiguous.
    assert (result.status, result.category) == (PublicationStatus.REFUSED, code)
    assert provider.calls == [] and ledger.rows == {}
    assert ledger.refusals == ({} if receipt_lost else {attempt_id: ("refused", code)})
