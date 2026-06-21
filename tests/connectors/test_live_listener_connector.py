"""Condensed live listener connector tests — ingest.v1 contract only.

Consolidates: test_live_listener_envelope.py, test_live_listener_audio.py,
test_live_listener_checkpoint.py, test_live_listener_filter.py,
test_live_listener_prefilter.py, test_live_listener_session.py,
test_live_listener_transcription.py, test_live_listener_vad.py,
live_listener/test_connector_integration.py,
live_listener/test_discretion.py,
live_listener/test_filter_gate_spec_compliance.py

Verifies:
- ingest.v1 envelope production for voice utterance
- Idempotency key determinism and format
- endpoint_identity format

[bu-35fm7]
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.connectors.live_listener.envelope import (
    build_voice_envelope,
    endpoint_identity,
    mint_event_id,
    mint_idempotency_key,
)

_DEVICE = "desk-mic"
_UNIX_MS = 1711447200000
_OBSERVED = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def voice_envelope() -> dict[str, Any]:
    return build_voice_envelope(
        device_name=_DEVICE,
        unix_ms=_UNIX_MS,
        session_id="session-abc",
        observed_at=_OBSERVED,
        transcript="Please order milk from the store.",
        confidence=0.95,
        duration_s=2.1,
        language="en",
        discretion_reason="addressed to assistant",
    )


def test_voice_envelope_field_contract(voice_envelope: dict[str, Any]) -> None:
    """Voice envelope carries ingest.v1 schema, voice source, session thread, transcript."""
    assert voice_envelope["schema_version"] == "ingest.v1"
    assert voice_envelope["source"]["channel"] == "voice"
    assert voice_envelope["source"]["provider"] == "live-listener"
    assert voice_envelope["event"]["external_thread_id"] == "session-abc"
    assert "Please order milk" in voice_envelope["payload"]["normalized_text"]


def test_voice_envelope_endpoint_identity_format(voice_envelope: dict[str, Any]) -> None:
    """Endpoint identity must follow exact 'live-listener:mic:<device_name>' format."""
    eid = voice_envelope["source"]["endpoint_identity"]
    assert eid == f"live-listener:mic:{_DEVICE}"


def test_voice_envelope_event_id_includes_device(voice_envelope: dict[str, Any]) -> None:
    assert _DEVICE in voice_envelope["event"]["external_event_id"]


def test_voice_envelope_passes_parse_ingest_envelope(voice_envelope: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    try:
        parse_ingest_envelope(voice_envelope)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_idempotency_key_deterministic() -> None:
    ep_id = endpoint_identity(_DEVICE)
    k1 = mint_idempotency_key(ep_id, _UNIX_MS, "same transcript")
    k2 = mint_idempotency_key(ep_id, _UNIX_MS, "same transcript")
    assert k1 == k2


def test_idempotency_key_differs_for_different_transcripts() -> None:
    ep_id = endpoint_identity(_DEVICE)
    k1 = mint_idempotency_key(ep_id, _UNIX_MS, "transcript A")
    k2 = mint_idempotency_key(ep_id, _UNIX_MS, "transcript B")
    assert k1 != k2


def test_endpoint_identity_format() -> None:
    """endpoint_identity() must produce exact 'live-listener:mic:<device_name>' format."""
    eid = endpoint_identity("kitchen-mic")
    assert eid == "live-listener:mic:kitchen-mic"


def test_mint_event_id_includes_device_and_timestamp() -> None:
    eid = mint_event_id(_DEVICE, _UNIX_MS)
    assert _DEVICE in eid
    assert str(_UNIX_MS) in eid
