from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import butlers.core.qa.dispatch as dispatch_module
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


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


def test_notes_parse_counter_registered_with_status_label(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class FakeCounter:
        def __init__(self, name, documentation, labelnames=None):
            captured["name"] = name
            captured["documentation"] = documentation
            captured["labelnames"] = labelnames

    monkeypatch.setattr("prometheus_client.Counter", FakeCounter)

    counter = dispatch_module._get_qa_investigation_notes_parse_total()

    assert isinstance(counter, FakeCounter)
    assert captured["name"] == "qa_investigation_notes_parse_total"
    assert captured["labelnames"] == ["status"]


def _with_verification_defaults(payload: dict) -> dict:
    """The payload as dumped: new optional fields appear with their defaults."""
    out = json.loads(json.dumps(payload))
    for line in out["evidence_lines"]:
        line.setdefault("ref", None)
        line.setdefault("unsupported", False)
    for claim in out["claims"].values():
        claim.setdefault("unsupported", False)
    return out


def test_full_parse():
    payload = _valid_notes_payload()

    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "ok"
    assert isinstance(notes, InvestigationNotes)
    assert notes.model_dump(mode="json") == _with_verification_defaults(payload)


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
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    span = _FakeSpan()
    status = await _persist_investigation_notes(pool, attempt_id, tmp_path, otel_span=span)

    assert status == "ok"
    assert counter.counts == {"ok": 1, "partial": 0, "failed": 0}
    assert span.attributes == {
        "qa.notes_emitted": True,
        "qa.notes_parse_status": "ok",
    }
    pool.execute.assert_awaited_once()
    sql, persisted_attempt_id, persisted_payload = pool.execute.await_args.args
    assert "jsonb_set" in sql
    assert "investigation_notes" in sql
    assert persisted_attempt_id == attempt_id
    assert persisted_payload == _with_verification_defaults(payload)
    assert pool.fetchval.await_count == 3  # includes the bundle manifest lookup


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
    pool.fetchval = AsyncMock()
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, attempt_id, tmp_path)

    assert status == "failed"
    assert counter.counts == {"ok": 0, "partial": 0, "failed": 1}
    pool.execute.assert_not_awaited()
    pool.fetchval.assert_not_awaited()
    assert "emitted no structured notes artifact" in caplog.text


@pytest.mark.asyncio
async def test_dispatcher_sets_span_attrs_when_notes_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    attempt_id = uuid.uuid4()
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock()
    counter = _FakeParseCounter()
    span = _FakeSpan()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, attempt_id, tmp_path, otel_span=span)

    assert status == "failed"
    assert span.attributes == {
        "qa.notes_emitted": False,
        "qa.notes_parse_status": "failed",
    }
    assert counter.counts == {"ok": 0, "partial": 0, "failed": 1}


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
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
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
    assert pool.fetchval.await_count == 3  # includes the bundle manifest lookup


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
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    status = await _persist_investigation_notes(pool, uuid.uuid4(), tmp_path)

    assert status == "ok"
    _sql, _persisted_attempt_id, persisted_payload = pool.execute.await_args.args
    assert raw_token in persisted_payload["evidence_lines"][0]["msg"]


@pytest.mark.asyncio
async def test_considered_emitted_per_counter_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    payload = _valid_notes_payload()
    payload["counter_evidence"] = [
        {
            "hypothesis": "Token expiry",
            "verdict": "rejected",
            "reason": "Refresh reached scope validation first.",
        },
        {
            "hypothesis": "Spotify-wide outage",
            "verdict": "rejected",
            "reason": "Other player endpoints returned 200.",
        },
        {
            "hypothesis": "Network egress block",
            "verdict": "pending",
            "reason": "Egress checks were not available in this run.",
        },
    ]
    _write_structured_notes(tmp_path, payload)
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    with patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record:
        status = await _persist_investigation_notes(pool, uuid.uuid4(), tmp_path)

    assert status == "ok"
    considered = [
        call.kwargs for call in record.await_args_list if call.kwargs["step"] == "considered"
    ]
    assert [event["text"] for event in considered] == [
        "Token expiry",
        "Spotify-wide outage",
        "Network egress block",
    ]
    assert [event["detail"] for event in considered] == [
        "rejected: Refresh reached scope validation first.",
        "rejected: Other player endpoints returned 200.",
        "pending: Egress checks were not available in this run.",
    ]


@pytest.mark.asyncio
async def test_concluded_emitted_once_on_ok_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
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

    with patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record:
        status = await _persist_investigation_notes(pool, attempt_id, tmp_path)

    assert status == "ok"
    concluded = [
        call.kwargs for call in record.await_args_list if call.kwargs["step"] == "concluded"
    ]
    assert len(concluded) == 1
    assert concluded[0]["attempt_id"] == attempt_id
    assert concluded[0]["text"] == payload["hypothesis"]
    assert concluded[0]["detail"].startswith("proposal rationale: ")
    assert payload["why_this_fix"][:80] in concluded[0]["detail"]


@pytest.mark.asyncio
async def test_no_considered_or_concluded_on_failed_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    notes_dir = tmp_path / ".qa"
    notes_dir.mkdir()
    (notes_dir / "investigation_notes.json").write_text("not valid JSON", encoding="utf-8")
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    counter = _FakeParseCounter()
    monkeypatch.setattr(
        "butlers.core.qa.dispatch._qa_investigation_notes_parse_total",
        counter,
    )

    with patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record:
        status = await _persist_investigation_notes(pool, uuid.uuid4(), tmp_path)

    assert status == "failed"
    pool.execute.assert_not_awaited()
    record.assert_not_awaited()


# --- evidence verification against the dispatcher's bundle (bu-ejow5p.13) ----------------------


def _bundle_manifest(**texts: str) -> dict:
    """The dispatcher's recorded bundle bodies (sections -> items with text)."""
    return {
        "revision": 1,
        "digest": "d",
        "sections": [
            {
                "name": "sessions",
                "status": "available",
                "items": [
                    {"id": ref, "kind": "session", "digest": "x", "text": t}
                    for ref, t in texts.items()
                ],
            }
        ],
    }


def _notes_with_lines(*lines: dict, claims: dict | None = None):
    payload = _valid_notes_payload()
    payload["schema_version"] = 2
    payload["evidence_lines"] = list(lines)
    payload["claims"] = claims or {}
    notes, status = parse_investigation_notes(json.dumps(payload))
    assert status == "ok"
    return notes


def _line(line_id: str, msg: str, ref: str | None, **extra) -> dict:
    return {
        "id": line_id,
        "ts": "t",
        "lvl": "ERROR",
        "butler": "b",
        "msg": msg,
        "ref": ref,
        **extra,
    }


def test_a_line_quoting_a_bundle_item_is_supported_and_an_invented_one_is_not() -> None:
    from butlers.core.qa.notes import can_be_patch_ready, verify_evidence_against_bundle

    notes = _notes_with_lines(
        _line("e1", "real text", "E1"),
        _line("e2", "invented text", "E9"),
        _line("e3", "edited text", "E1"),
        _line("e4", "no ref", None, unsupported=False),
        claims={
            "c1": {"evidence_ids": ["e1"], "note": "n"},
            "c2": {"evidence_ids": ["e2", "e3"], "note": "n"},
            "c3": {"evidence_ids": [], "note": "n"},
        },
    )

    checked = verify_evidence_against_bundle(notes, _bundle_manifest(E1="real text"))

    assert [line.unsupported for line in checked.evidence_lines] == [False, True, True, True]
    assert can_be_patch_ready(checked)
    assert {k: c.unsupported for k, c in checked.claims.items()} == {
        "c1": False,
        "c2": True,
        "c3": True,
    }


def test_an_agent_cannot_mark_its_own_line_supported() -> None:
    from butlers.core.qa.notes import verify_evidence_against_bundle

    notes = _notes_with_lines(_line("e1", "made up", "E1", unsupported=False))

    checked = verify_evidence_against_bundle(notes, _bundle_manifest(E1="the real one"))

    assert checked.evidence_lines[0].unsupported is True


def test_notes_without_a_ref_still_parse_as_schema_v1() -> None:
    payload = _valid_notes_payload()
    notes, status = parse_investigation_notes(json.dumps(payload))

    assert status == "ok" and notes.evidence_lines[0].ref is None
    assert notes.evidence_lines[0].unsupported is False


@pytest.mark.asyncio
async def test_persisted_notes_mark_the_invented_line_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _valid_notes_payload()
    payload["schema_version"] = 2
    payload["evidence_lines"] = [
        _line("e1", "real text", "E1"),
        _line("e2", "invented", "E7"),
    ]
    payload["claims"] = {"c1": {"evidence_ids": ["e2"], "note": "n"}}
    _write_structured_notes(tmp_path, payload)
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchval = AsyncMock(
        side_effect=lambda sql, *a: (
            _bundle_manifest(E1="real text") if "evidence_bundle" in sql else uuid.uuid4()
        )
    )
    monkeypatch.setattr("butlers.core.qa.dispatch._qa_investigation_notes_parse_total", None)

    await _persist_investigation_notes(pool, uuid.uuid4(), tmp_path)

    persisted = pool.execute.await_args.args[2]
    assert [line["unsupported"] for line in persisted["evidence_lines"]] == [False, True]
    assert persisted["claims"]["c1"]["unsupported"] is True


def test_a_quote_is_contained_in_the_item_text_after_whitespace_normalisation() -> None:
    from butlers.core.qa.notes import verify_evidence_against_bundle

    notes = _notes_with_lines(
        _line("e1", "401   scope_mismatch\n/me", "E1"),
        _line("e2", "scope_mismatch /other", "E1"),
        _line("e3", "   ", "E1"),
    )

    checked = verify_evidence_against_bundle(
        notes, _bundle_manifest(E1="spotify 401 scope_mismatch /me/player failed")
    )

    assert [line.unsupported for line in checked.evidence_lines] == [False, True, True]


def test_a_case_with_only_unsupported_claims_cannot_be_patch_ready() -> None:
    from butlers.core.qa.notes import can_be_patch_ready, verify_evidence_against_bundle

    notes = _notes_with_lines(
        _line("e1", "invented", "E1"),
        claims={"c1": {"evidence_ids": ["e1"], "note": "n"}},
    )

    checked = verify_evidence_against_bundle(notes, _bundle_manifest(E1="real"))

    assert checked.claims["c1"].unsupported is True
    assert can_be_patch_ready(checked) is False
