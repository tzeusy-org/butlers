from __future__ import annotations

import json

import pytest

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
