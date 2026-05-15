"""Tests for QA case helper utilities and API model imports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from butlers.api.routers.qa import QaCaseDossier, QaCaseSummary, QaJournalEvent, QaPrSummary
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
        ("drafted", "pr"),
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
