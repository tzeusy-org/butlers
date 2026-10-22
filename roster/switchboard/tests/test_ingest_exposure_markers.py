"""Ingest persists connector exposure markers for goal admission (bu-xmueeq).

The reply reference and untrusted spans are copied into the stored envelope only when
the connector supplied them; nothing on the routing path reads them (RFC 0038 D5, dark build).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.tools.switchboard.contracts import parse_ingest_envelope
from butlers.tools.switchboard.ingestion.ingest import _persist_exposure_markers

pytestmark = pytest.mark.unit

TEXT = "see below: pay account 12-34"


def _envelope(**markers: Any) -> Any:
    payload: dict[str, Any] = {"raw": {"text": TEXT}, "normalized_text": TEXT}
    if "untrusted_spans" in markers:
        payload["untrusted_spans"] = markers["untrusted_spans"]
    source = {"channel": "telegram_bot", "provider": "telegram", "endpoint_identity": "tg:bot"}
    event = {
        "external_event_id": "9",
        "external_conversation_id": "telegram:100",
        "reply_target_ref": "100:9",
        "observed_at": datetime.now(UTC).isoformat(),
    }
    if "in_reply_to_ref" in markers:
        event["in_reply_to_ref"] = markers["in_reply_to_ref"]
    return parse_ingest_envelope(
        {
            "schema_version": "ingest.v1",
            "source": source,
            "event": event,
            "sender": {"identity": "owner"},
            "payload": payload,
            "control": {"idempotency_key": "k", "policy_tier": "default"},
        }
    )


def _raw_payload() -> dict[str, Any]:
    return {"source": {}, "event": {}, "payload": {"normalized_text": TEXT}}


def test_markers_are_persisted_when_present() -> None:
    span = {"kind": "forwarded", "start": 11, "end": len(TEXT), "origin_display_name": "Mallory"}
    envelope = _envelope(untrusted_spans=[span], in_reply_to_ref="100:5")
    raw_payload = _raw_payload()

    _persist_exposure_markers(envelope, raw_payload, TEXT)

    assert raw_payload["payload"]["untrusted_spans"] == [span]
    assert raw_payload["event"]["in_reply_to_ref"] == "100:5"


def test_absent_markers_leave_the_stored_envelope_unchanged() -> None:
    raw_payload = _raw_payload()

    _persist_exposure_markers(_envelope(), raw_payload, TEXT)

    assert raw_payload == _raw_payload()


def test_rewritten_stored_text_collapses_spans_to_whole_text() -> None:
    envelope = _envelope(untrusted_spans=[{"kind": "quoted", "start": 11, "end": len(TEXT)}])
    raw_payload = _raw_payload()
    stored = "[scrubbed]"

    _persist_exposure_markers(envelope, raw_payload, stored)

    assert raw_payload["payload"]["untrusted_spans"] == [
        {"kind": "quoted", "start": 0, "end": len(stored)}
    ]
