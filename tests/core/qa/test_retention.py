"""Tests for QA raw evidence retention cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import butlers.modules.qa as qa_module
from butlers.modules.qa import QaConfig, QaModule

pytestmark = pytest.mark.unit


class _FakeRetentionCounter:
    def __init__(self) -> None:
        self.value = 0

    def inc(self, amount: int = 1) -> None:
        self.value += amount


class _FakePool:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.fetch = AsyncMock(side_effect=self._fetch)
        self.execute = AsyncMock(side_effect=self._execute)
        self.updated: dict[uuid.UUID, dict] = {}

    async def _fetch(self, *_args, **_kwargs) -> list[dict]:
        return self.rows

    async def _execute(self, _sql: str, finding_id: uuid.UUID, structured_evidence: dict) -> str:
        self.updated[finding_id] = structured_evidence
        return "UPDATE 1"


def _module_with_pool(pool: _FakePool) -> QaModule:
    module = QaModule()
    module._pool = pool
    return module


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
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 1, "malformed_rows": 0}
    updated = pool.updated[row["id"]]
    assert "evidence_lines" not in updated["investigation_notes"]
    assert updated["source"] == "qa-agent"
    assert counter.value == 1


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_exempts_non_terminal_recent_finding(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(created_at=now - timedelta(days=10), closed_at=None)
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 0}
    assert pool.updated == {}
    assert counter.value == 0


@pytest.mark.asyncio
async def test_daily_evidence_cleanup_exempts_non_terminal_old_finding(monkeypatch):
    now = datetime(2026, 5, 15, 4, tzinfo=UTC)
    row = _row(
        created_at=now - timedelta(days=45),
        healing_attempt_id=uuid.uuid4(),
        closed_at=None,
    )
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 0}
    assert pool.updated == {}
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
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    updated = pool.updated[row["id"]]
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
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)

    with caplog.at_level("WARNING", logger="butlers.modules.qa"):
        result = await _module_with_pool(pool).daily_evidence_cleanup(now=now)

    assert result == {"status": "completed", "cleaned_rows": 0, "malformed_rows": 1}
    assert pool.updated == {}
    assert counter.value == 0
    assert "malformed investigation_notes shape" in caplog.text


@pytest.mark.asyncio
async def test_scheduled_evidence_cleanup_honors_configured_hour(monkeypatch):
    now = datetime(2026, 5, 15, 3, tzinfo=UTC)
    row = _row(created_at=now - timedelta(days=31))
    pool = _FakePool([row])
    counter = _FakeRetentionCounter()
    monkeypatch.setattr(qa_module, "_qa_findings_retention_purged_total", counter)
    module = _module_with_pool(pool)
    module._config = QaConfig(retention_cleanup_hour=4)

    skipped = await module.run_scheduled_evidence_cleanup(now=now)
    cleaned = await module.run_scheduled_evidence_cleanup(now=now.replace(hour=4))

    assert skipped == {"status": "skipped", "cleaned_rows": 0, "malformed_rows": 0}
    assert cleaned == {"status": "completed", "cleaned_rows": 1, "malformed_rows": 0}
