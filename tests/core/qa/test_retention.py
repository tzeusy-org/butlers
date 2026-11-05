"""Tests for QA raw evidence retention cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import butlers.modules.qa as qa_module
from butlers.modules.qa import QaConfig, QaModule
from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import records

pytestmark = pytest.mark.unit


class _FakeRetentionCounter:
    def __init__(self) -> None:
        self.value = 0

    def inc(self, amount: int = 1) -> None:
        self.value += amount


def _retention_pool(rows: list[dict]) -> FakePool:
    """Candidate findings for one cleanup pass; each UPDATE succeeds."""
    return fake_pool(fetch=records(rows), execute="UPDATE 1")


def _updated(pool: FakePool) -> dict[uuid.UUID, dict]:
    """finding id -> rewritten structured_evidence, from the cleanup's UPDATEs."""
    return {call.args[1]: call.args[2] for call in pool.execute.await_args_list}


def _module_with_pool(pool: FakePool) -> QaModule:
    module = QaModule()
    module._pool = pool
    return module


def test_retention_counter_registered_without_labels(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class FakeCounter:
        def __init__(self, name, documentation, labelnames=None):
            captured["name"] = name
            captured["documentation"] = documentation
            captured["labelnames"] = labelnames

    monkeypatch.setattr("prometheus_client.Counter", FakeCounter)

    counter = qa_module._get_qa_findings_retention_purged_total()

    assert isinstance(counter, FakeCounter)
    assert captured["name"] == "qa_findings_retention_purged_total"
    assert captured["labelnames"] is None


def _row(
    *,
    created_at: datetime,
    healing_attempt_id: uuid.UUID | None = None,
    closed_at: datetime | None = None,
    structured_evidence: dict | list | str | None = None,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "created_at": created_at,
        "healing_attempt_id": healing_attempt_id,
        "closed_at": closed_at,
        "structured_evidence": structured_evidence
        if structured_evidence is not None
        else {
            "source": "qa-agent",
            "investigation_notes": {
                "headline": "Found root cause",
                "summary": "Narrative summary survives cleanup.",
                "evidence_lines": ["raw traceback line", "raw stderr line"],
            },
        },
    }


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_removes_evidence_lines_for_old_finding(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(created_at=now - timedelta(days=31))
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 1, "malformed_rows": 0}
    updated = _updated(pool)[row["id"]]
    assert "evidence_lines" not in updated["investigation_notes"]
    assert updated["source"] == "qa-agent"
    assert counter.value == 1


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_exempts_non_terminal_recent_finding(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(created_at=now - timedelta(days=10), closed_at=None)
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 0}
    pool.execute.assert_not_awaited()
    assert counter.value == 0


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_exempts_non_terminal_old_finding(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(
        created_at=now - timedelta(days=45),
        healing_attempt_id=uuid.uuid4(),
        closed_at=None,
    )
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 0}
    pool.execute.assert_not_awaited()
    assert counter.value == 0


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_preserves_narrative_fields(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(
        created_at=now - timedelta(days=3),
        closed_at=now - timedelta(days=15),
        structured_evidence={
            "repository": "Tzeusy/butlers",
            "investigation_notes": {
                "headline": "Failure has a known cause",
                "blurb_segments": [{"text": "The timeout came from setup."}],
                "counter_evidence": [{"claim": "not a code regression"}],
                "why_this_fix": "It narrows the failing setup path.",
                "diff_snapshot": {"files": ["tests/example_test.py"]},
                "evidence_lines": ["raw log 1", "raw log 2"],
            },
        },
    )
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    updated = _updated(pool)[row["id"]]
    assert updated["repository"] == "Tzeusy/butlers"
    assert updated["investigation_notes"] == {
        "headline": "Failure has a known cause",
        "blurb_segments": [{"text": "The timeout came from setup."}],
        "counter_evidence": [{"claim": "not a code regression"}],
        "why_this_fix": "It narrows the failing setup path.",
        "diff_snapshot": {"files": ["tests/example_test.py"]},
    }


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_skips_malformed_investigation_notes(caplog, monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(
        created_at=now - timedelta(days=31),
        structured_evidence={
            "investigation_notes": ["raw line without narrative object"],
        },
    )
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    with caplog.at_level("WARNING", logger="butlers.modules.qa"):
        result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 1}
    pool.execute.assert_not_awaited()
    assert counter.value == 0
    assert "malformed investigation_notes shape" in caplog.text


@pytest.mark.asyncio
async def test_scheduled_evidence_cleanup_honors_configured_hour(monkeypatch):
    now = datetime(2026, 5, 15, 3, tzinfo=UTC)
    row = _row(created_at=now - timedelta(days=31))
    pool = _retention_pool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)
    prune = AsyncMock(return_value=2)
    monkeypatch.setattr(qa_module, "prune_retained_exports", prune)
    module = _module_with_pool(pool)
    module._config = QaConfig(retention_cleanup_hour=4)

    skipped = await module.run_scheduled_evidence_cleanup(now=now)
    cleaned = await module.run_scheduled_evidence_cleanup(now=now.replace(hour=4))

    assert skipped == {"status": "skipped", "cleaned_rows": 0, "malformed_rows": 0}
    assert cleaned == {
        "status": "completed",
        "cleaned_rows": 1,
        "malformed_rows": 0,
        "pruned_exports": 2,
    }
    prune.assert_awaited_once_with(pool)


@pytest.mark.asyncio
async def test_export_prune_failure_does_not_fail_the_evidence_cleanup(monkeypatch, caplog):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    pool = _retention_pool([_row(created_at=now - timedelta(days=31))])
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", _FakeRetentionCounter())
    monkeypatch.setattr(qa_module, "prune_retained_exports", AsyncMock(side_effect=OSError))
    module = _module_with_pool(pool)
    module._config = QaConfig(retention_cleanup_hour=4)

    with caplog.at_level("WARNING", logger="butlers.modules.qa"):
        cleaned = await module.run_scheduled_evidence_cleanup(now=now)

    assert cleaned["cleaned_rows"] == 1 and cleaned["pruned_exports"] == 0
    assert "export prune failed" in caplog.text
