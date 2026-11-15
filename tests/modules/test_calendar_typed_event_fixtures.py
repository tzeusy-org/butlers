"""The typed Google event fixture corpus is honest about itself and normalizes as declared.

bu-s11n0s.4 AC B: typed Google payloads persist as fixture inputs with named provenance and
hashes, and synthetic schema-valid fixtures stay distinguishable from captured provider
responses. Every fixture in ``tests/fixtures/google_calendar_typed_events`` is SYNTHETIC (shaped
after the public Calendar API v3 Events resource reference); none is a recorded account payload,
so this corpus does not claim the recorded-payload obligation. Real-Postgres projection of the
same corpus is in ``tests/integration/test_calendar_event_type_projection.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from butlers.modules.calendar.google_mapping import _google_event_to_calendar_event

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "google_calendar_typed_events"
MANIFEST = FIXTURE_DIR / "manifest.json"
NON_FIXTURE_FILES = {"manifest.json", "README.md"}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _entries() -> list[dict[str, Any]]:
    return _manifest()["fixtures"]


def test_the_manifest_lists_exactly_the_fixture_files_with_their_hashes() -> None:
    on_disk = {p.name for p in FIXTURE_DIR.iterdir() if p.name not in NON_FIXTURE_FILES}
    listed = [entry["file"] for entry in _entries()]
    assert len(listed) == len(set(listed))
    assert set(listed) == on_disk
    for entry in _entries():
        digest = hashlib.sha256((FIXTURE_DIR / entry["file"]).read_bytes()).hexdigest()
        assert entry["sha256"] == digest, entry["file"]


def test_every_fixture_is_labelled_synthetic_and_none_claims_a_capture() -> None:
    manifest = _manifest()
    assert manifest["provenance"] == "synthetic"
    assert "Events resource" in manifest["basis"]
    # A recorded payload would need its own provenance; this corpus has none.
    assert {entry.get("provenance", manifest["provenance"]) for entry in _entries()} == {
        "synthetic"
    }


def test_fixtures_carry_no_real_identity_or_provider_url() -> None:
    for entry in _entries():
        text = (FIXTURE_DIR / entry["file"]).read_text(encoding="utf-8")
        payload = json.loads(text)
        for email in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text):
            assert email == "example.com", entry["file"]
        assert not {"htmlLink", "hangoutLink", "conferenceData"} & set(payload), entry["file"]
        assert "http" not in text, entry["file"]


def test_the_corpus_covers_every_typed_shape_the_projection_keeps() -> None:
    expected = [entry["expect"] for entry in _entries()]
    types = {e["event_type"] for e in expected}
    assert {"default", "outOfOffice", "focusTime", "workingLocation"} <= types
    assert any(
        e["event_type"] not in {"default", "outOfOffice", "focusTime", "workingLocation"}
        for e in expected
    )
    locations = {e["working_location"]["type"] for e in expected if e["working_location"]}
    assert locations == {"homeOffice", "officeLocation", "customLocation"}
    # Some workingLocation fixtures normalize to no location at all.
    assert any(
        e["event_type"] == "workingLocation" and e["working_location"] is None for e in expected
    )
    assert {e["all_day"] for e in expected} == {True, False}


def _collected_entries() -> list[dict[str, Any]]:
    # A missing manifest must fail the corpus tests above, not break collection.
    return _entries() if MANIFEST.exists() else []


@pytest.mark.parametrize("entry", _collected_entries(), ids=lambda entry: entry["file"])
def test_each_fixture_normalizes_to_its_declared_typed_fields(entry: dict[str, Any]) -> None:
    payload = json.loads((FIXTURE_DIR / entry["file"]).read_text(encoding="utf-8"))
    event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
    assert event is not None
    assert event.event_type == entry["expect"]["event_type"]
    assert event.working_location == entry["expect"]["working_location"]
    assert event.all_day is entry["expect"]["all_day"]
