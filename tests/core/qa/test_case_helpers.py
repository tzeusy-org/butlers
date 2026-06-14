"""Tests for QA case helper utilities and API model imports."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from butlers.api.routers.qa import (
    QaCaseDossier,
    QaCaseSummary,
    QaJournalEvent,
    QaPrSummary,
    _row_to_pr_summary,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.severity import headline_for_case, short_id_from_uuid, state_of_case

pytestmark = pytest.mark.unit


def _uuid7_with_timestamp(timestamp_ms: int) -> uuid.UUID:
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= 0b10 << 62
    return uuid.UUID(int=value)


def _attempt(**overrides: object) -> dict[str, object]:
    attempt = {
        "status": "investigating",
        "exception_type": "RuntimeError",
        "butler_name": "qa",
        "error_detail": None,
    }
    attempt.update(overrides)
    return attempt


def _finding(**overrides: object) -> QaFinding:
    now = datetime.now(UTC)
    values = {
        "fingerprint": "a" * 64,
        "source_type": "log_scanner",
        "source_butler": "finance",
        "severity": 1,
        "exception_type": "ValueError",
        "event_summary": "Event summary fallback",
        "call_site": "module:function",
        "occurrence_count": 1,
        "first_seen": now,
        "last_seen": now,
        "timestamp": now,
    }
    values.update(overrides)
    return QaFinding(**values)


def test_short_id_stable() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_890)

    assert short_id_from_uuid(attempt_id) == "#890"
    assert short_id_from_uuid(attempt_id) == short_id_from_uuid(attempt_id)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pr_merged", "landed"),
        ("pr_open", "pr"),
        ("drafted", "detect"),
        ("unfixable", "escalated"),
        ("failed", "detect"),
        ("timeout", "detect"),
        ("anonymization_failed", "detect"),
        ("investigating", "diagnose"),
        ("dispatch_pending", "detect"),
    ],
)
def test_state_of_case_mapping(status: str, expected: str) -> None:
    assert state_of_case(_attempt(status=status)) == expected


@pytest.mark.parametrize(
    "error_detail",
    [
        "Needs human action: inspect PR",
        "Operator must rotate credential",
        "Escalated after repeated failures",
    ],
)
def test_state_of_case_escalates_attempt_with_operator_action(error_detail: str) -> None:
    assert state_of_case(_attempt(status="failed", error_detail=error_detail)) == "escalated"


def test_state_of_case_accepts_object_rows() -> None:
    attempt = SimpleNamespace(status="pr_open", error_detail=None)

    assert state_of_case(attempt) == "pr"


@pytest.mark.integration
async def test_state_of_case_accepts_asyncpg_record(provisioned_postgres_pool) -> None:
    """A real ``asyncpg.Record`` row must derive its state from key access.

    Regression for the StateTrack-frozen-at-``detect`` bug: ``asyncpg.Record``
    is not a ``Mapping`` subclass, so the prior ``isinstance(source, Mapping)``
    gate fell through to ``getattr`` and yielded ``status=None`` → ``detect``
    for every case regardless of the actual ``status`` column. Exercising a
    genuine Record (not a dict subclass, which would mask the bug) proves the
    duck-typed key accessor reads the column.
    """

    async with provisioned_postgres_pool() as pool:
        record = await pool.fetchrow(
            "SELECT $1::text AS status, NULL::text AS error_detail",
            "pr_open",
        )

    # Guard the test's own premise: a real Record is not a Mapping.
    assert not isinstance(record, Mapping)
    assert state_of_case(record) == "pr"


def test_headline_fallback_chain() -> None:
    attempt = _attempt(exception_type="RuntimeError", butler_name="finance")
    finding_with_notes = _finding(
        structured_evidence={"investigation_notes": {"headline": "Notes headline"}}
    )
    finding_without_notes = _finding(event_summary="Finding summary")

    assert headline_for_case(attempt, finding_with_notes) == "Notes headline"
    assert headline_for_case(attempt, finding_without_notes) == "Finding summary"
    assert headline_for_case(attempt, None) == "RuntimeError in finance"


def test_case_api_models_import_and_validate() -> None:
    now = datetime.now(UTC)
    case = QaCaseSummary(
        id=uuid.uuid4(),
        short_id="#123",
        sev="high",
        butler="finance",
        headline="Case headline",
        detected=now,
        age_seconds=42,
        state="pr",
        pr_state="open",
        pr_url="https://github.com/Tzeusy/butlers/pull/1",
    )
    pr = QaPrSummary(
        number=1,
        state="open",
        title="Fix finance failure",
        branch="agent/bu-a96av",
        ci_status="pending",
        additions=10,
        deletions=2,
        opened_at=now,
        merged_at=None,
        url="https://github.com/Tzeusy/butlers/pull/1",
    )
    event = QaJournalEvent(
        id=uuid.uuid4(),
        ts=now,
        step="flagged",
        text="Failure flagged",
        detail=None,
        data={"fingerprint": "a" * 64},
    )

    dossier = QaCaseDossier(
        case=case,
        state_track_stage="pr",
        investigation_notes=None,
        pr=pr,
        journal=[event],
    )

    assert dossier.case.short_id == "#123"
    assert dossier.pr is not None
    assert dossier.pr.ci_status == "pending"
    assert dossier.journal[0].step == "flagged"


def test_row_to_pr_summary_does_not_fabricate_ci_or_diff_stats() -> None:
    """CI status and diff stats are not tracked locally, so the dossier PR
    summary must report them as unavailable (``None``) rather than asserting a
    fabricated ``"unknown"`` / ``+0/-0`` placeholder (bu-cnvg7.3)."""
    now = datetime.now(UTC)
    row = {
        "status": "pr_open",
        "pr_url": "https://github.com/Tzeusy/butlers/pull/1653",
        "pr_number": 1653,
        "branch_name": "agent/bu-z34mk",
        "created_at": now,
        "closed_at": None,
    }

    summary = _row_to_pr_summary(row)

    assert summary is not None
    assert summary.number == 1653
    assert summary.state == "open"
    assert summary.branch == "agent/bu-z34mk"
    # The honest fix: no fabricated CI/diff data.
    assert summary.ci_status is None
    assert summary.additions is None
    assert summary.deletions is None


def _pr_row() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "status": "pr_open",
        "pr_url": "https://github.com/Tzeusy/butlers/pull/1653",
        "pr_number": 1653,
        "branch_name": "agent/bu-z34mk",
        "created_at": now,
        "closed_at": None,
    }


class _StubClient:
    """Stub GithubPrClient: records calls and returns a canned PrMetadata."""

    def __init__(self, meta) -> None:
        self._meta = meta
        self.calls: list[tuple[str, str, int, str | None]] = []

    async def fetch(self, owner, repo, number, *, token):
        self.calls.append((owner, repo, number, token))
        return self._meta


async def test_row_to_pr_summary_live_enriches_with_github_metadata() -> None:
    """With a token + reachable GitHub, the PR summary carries real CI + diff stats."""
    from butlers.api.routers.qa import _row_to_pr_summary_live
    from butlers.core.qa.github_pr import PrMetadata

    client = _StubClient(PrMetadata(ci_status="passing", additions=12, deletions=3))

    summary = await _row_to_pr_summary_live(_pr_row(), token="t0ken", client=client)

    assert summary is not None
    assert summary.ci_status == "passing"
    assert summary.additions == 12
    assert summary.deletions == 3
    assert client.calls == [("Tzeusy", "butlers", 1653, "t0ken")]


async def test_row_to_pr_summary_live_falls_back_when_unavailable() -> None:
    """No token / GitHub unreachable -> honest unavailable (None) fields, no fake +0/-0."""
    from butlers.api.routers.qa import _row_to_pr_summary_live
    from butlers.core.qa.github_pr import PrMetadata

    client = _StubClient(PrMetadata(ci_status=None, additions=None, deletions=None))

    summary = await _row_to_pr_summary_live(_pr_row(), token=None, client=client)

    assert summary is not None
    assert summary.ci_status is None
    assert summary.additions is None
    assert summary.deletions is None


async def test_row_to_pr_summary_live_returns_none_without_pr() -> None:
    """No PR on the row -> None (no GitHub fetch attempted)."""
    from butlers.api.routers.qa import _row_to_pr_summary_live
    from butlers.core.qa.github_pr import PrMetadata

    client = _StubClient(PrMetadata(ci_status="passing", additions=1, deletions=1))
    row = {
        "status": "investigating",
        "pr_url": None,
        "pr_number": None,
        "branch_name": None,
        "created_at": datetime.now(UTC),
        "closed_at": None,
    }

    summary = await _row_to_pr_summary_live(row, token="t0ken", client=client)

    assert summary is None
    assert client.calls == []
