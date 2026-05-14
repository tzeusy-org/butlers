from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.dispatch import _persist_investigation_notes
from butlers.core.qa.notes import InvestigationNotes, parse_investigation_notes

pytestmark = pytest.mark.unit


def _valid_notes_payload() -> dict:
    return {
        "schema_version": 1,
        "headline": "Spotify connector scope mismatch",
        "hypothesis": "The connector requested a retired scope during refresh.",
        "blurb_segments": [
            "Refresh failed before the connector could list sessions.",
            {
                "claim": "scope-error",
                "text": "The OAuth response rejected the requested scope set.",
            },
        ],
        "claims": {
            "scope-error": {
                "evidence_ids": ["line-1"],
                "note": "Provider returned invalid_scope.",
            }
        },
        "evidence_lines": [
            {
                "id": "line-1",
                "ts": "2026-05-14T17:00:00Z",
                "lvl": "ERROR",
                "butler": "lifestyle",
                "msg": "invalid_scope while refreshing Spotify token",
            }
        ],
        "counter_evidence": [
            {
                "hypothesis": "The token was revoked by the owner.",
                "verdict": "rejected",
                "reason": "The refresh call reached scope validation before token validation.",
            }
        ],
        "why_this_fix": "Keeping requested scopes aligned with the provider contract fixes refresh.",
        "diff_snapshot": [
            {"kind": "meta", "text": "src/butlers/connectors/spotify.py"},
            {"kind": "-", "text": "scope=user-read-currently-playing"},
            {"kind": "+", "text": "scope=user-read-playback-state"},
            {"kind": " ", "text": "timeout=30"},
        ],
    }


def _write_structured_notes(worktree: Path, payload: dict) -> None:
    notes_dir = worktree / ".qa"
    notes_dir.mkdir()
    (notes_dir / "investigation_notes.json").write_text(json.dumps(payload), encoding="utf-8")


class _FakeParseCounter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {"ok": 0, "partial": 0, "failed": 0}
        self._status: str | None = None

    def labels(self, *, status: str):
        self._status = status
        return self

    def inc(self) -> None:
        assert self._status is not None
        self.counts[self._status] += 1


def test_full_parse():
    payload = _valid_notes_payload()

    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "ok"
    assert isinstance(notes, InvestigationNotes)
    assert notes.model_dump(mode="json") == payload


def test_partial_parse_missing_optional():
    payload = _valid_notes_payload()
    del payload["counter_evidence"]

    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "partial"
    assert notes is not None
    assert notes.counter_evidence == []
    assert notes.headline == payload["headline"]


def test_partial_parse_wrong_type():
    payload = _valid_notes_payload()
    payload["claims"] = ["not", "a", "dict"]

    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "partial"
    assert notes is not None
    assert notes.claims == {}
    assert notes.evidence_lines[0].id == "line-1"


def test_failed_parse_invalid_json():
    notes, status = parse_investigation_notes("not valid JSON")

    assert status == "failed"
    assert notes is None


def test_schema_version_mismatch():
    payload = _valid_notes_payload()
    payload["schema_version"] = 99

    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "partial"
    assert notes is not None
    assert notes.schema_version == 1
    assert notes.headline == payload["headline"]


@pytest.mark.asyncio
async def test_dispatcher_persists_ok_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = _valid_notes_payload()
    _write_structured_notes(tmp_path, payload)
    attempt_id = uuid.uuid4()
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, attempt_id, tmp_path)

    assert status == "ok"
    assert counter.counts == {"ok": 1, "partial": 0, "failed": 0}
    pool.execute.assert_awaited_once()
    sql, persisted_attempt_id, persisted_payload = pool.execute.await_args.args
    assert "jsonb_set" in sql
    assert "investigation_notes" in sql
    assert persisted_attempt_id == attempt_id
    assert persisted_payload == payload


@pytest.mark.asyncio
async def test_dispatcher_handles_missing_notes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="butlers.core.qa.dispatch")
    attempt_id = uuid.uuid4()
    pool = MagicMock()
    pool.execute = AsyncMock()
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, attempt_id, tmp_path)

    assert status == "failed"
    assert counter.counts == {"ok": 0, "partial": 0, "failed": 1}
    pool.execute.assert_not_awaited()
    assert "emitted no structured notes artifact" in caplog.text


@pytest.mark.asyncio
async def test_dispatcher_persists_partial_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    payload = _valid_notes_payload()
    payload["claims"] = ["not", "a", "dict"]
    _write_structured_notes(tmp_path, payload)
    attempt_id = uuid.uuid4()
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, attempt_id, tmp_path)

    assert status == "partial"
    assert counter.counts == {"ok": 0, "partial": 1, "failed": 0}
    _sql, _persisted_attempt_id, persisted_payload = pool.execute.await_args.args
    assert persisted_payload["claims"] == {}
    assert persisted_payload["headline"] == payload["headline"]


@pytest.mark.asyncio
async def test_evidence_lines_not_reanonymized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    payload = _valid_notes_payload()
    raw_token = "raw-owner-token-abc123"
    payload["evidence_lines"][0]["msg"] = f"provider returned {raw_token}"
    _write_structured_notes(tmp_path, payload)
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, uuid.uuid4(), tmp_path)

    assert status == "ok"
    _sql, _persisted_attempt_id, persisted_payload = pool.execute.await_args.args
    assert raw_token in persisted_payload["evidence_lines"][0]["msg"]
