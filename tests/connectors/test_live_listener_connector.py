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

    from butlers.tools.switchboard.contracts import parse_ingest_envelope

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


async def test_process_segment_logs_ids_and_length_not_transcript(caplog) -> None:
    """A spoken code never reaches the INFO transcription or DEBUG submitted-utterance logs."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from butlers.connectors.live_listener.connector import LiveListenerConnector

    connector = object.__new__(LiveListenerConnector)
    connector._ll_metrics = {"kitchen": MagicMock()}
    connector._mic_states = {"kitchen": SimpleNamespace()}
    transcript = "my login code is 482913"
    connector._transcription_clients = {
        "kitchen": SimpleNamespace(
            transcribe=AsyncMock(
                return_value=SimpleNamespace(
                    text=transcript, confidence=0.9, language="en", healthy=True
                )
            ),
            healthy=True,
        )
    }
    connector._prefilters = {}
    connector._discretion_evaluators = {}
    connector._sessions = {
        "kitchen": SimpleNamespace(
            get_or_create_session=lambda _ts: "session-1",
            session_id="session-1",
            session_last_ts_ms=0,
        )
    }
    connector._mcp_client = SimpleNamespace(call_tool=AsyncMock(return_value={"status": "ok"}))
    connector._connector_metrics = MagicMock()
    connector._db_pool = None
    segment = SimpleNamespace(duration_ms=1000, audio_bytes=b"x", offset_ts=1_782_000_000.0)

    with (
        patch(
            "butlers.connectors.live_listener.connector.evaluate_voice_filter",
            return_value=SimpleNamespace(allowed=True),
        ),
        caplog.at_level(logging.DEBUG, logger="butlers.connectors.live_listener.connector"),
    ):
        await connector._process_segment(SimpleNamespace(name="kitchen"), segment, object())

    connector._mcp_client.call_tool.assert_awaited_once()
    messages = [record.getMessage() for record in caplog.records]
    assert any("submitted utterance" in m for m in messages)
    assert all("482913" not in m and "login code" not in m for m in messages)
    assert any(f"chars={len(transcript)}" in m for m in messages)


async def test_wyoming_discard_log_has_length_not_transcript(caplog) -> None:
    """A low-confidence Wyoming transcript is discarded without its text reaching the logs."""
    import logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from wyoming.asr import Transcript

    from butlers.connectors.live_listener.transcription import WyomingTranscriptionClient

    client = WyomingTranscriptionClient(url="tcp://localhost:10300", mic_name="kitchen")
    client._writer = MagicMock()
    client._reader = MagicMock()
    low_confidence = Transcript(
        text="my login code is 482913", language="en", context={"confidence": 0.01}
    ).event()

    with (
        patch("wyoming.event.async_write_event", new=AsyncMock()),
        patch("wyoming.event.async_read_event", new=AsyncMock(return_value=low_confidence)),
        caplog.at_level(logging.DEBUG, logger="butlers.connectors.live_listener.transcription"),
    ):
        result = await client._do_transcribe(b"\x00" * 3200)

    assert result is None
    assert any("discarding result" in record.getMessage() for record in caplog.records)
    assert "482913" not in caplog.text and "login code" not in caplog.text


async def test_voice_known_quote_markers_and_benign_unmarked_transcript():
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    for transcript, kind in [
        ("Owner says\n> earlier instruction", "quoted"),
        ("FYI\n---------- Forwarded message ---------\nthird party", "forwarded"),
        ("ordinary pasted words without markers", None),
    ]:
        env = build_voice_envelope(
            device_name=_DEVICE,
            unix_ms=_UNIX_MS,
            session_id="session-abc",
            observed_at=_OBSERVED,
            transcript=transcript,
            confidence=0.95,
            duration_s=2.1,
            language="en",
            discretion_reason="addressed",
        )
        if kind:
            (span,) = env["payload"]["untrusted_spans"]
            assert span["kind"] == kind and span["end"] == len(transcript)
            assert "Owner says" not in transcript[span["start"] : span["end"]]
        else:
            assert "untrusted_spans" not in env["payload"]
        assert env["sender"]["identity"] == "ambient"
        parse_ingest_envelope(env)

    from butlers.connectors.discretion import DiscretionResult
    from butlers.connectors.filtered_event_buffer import _sanitize_replay_payload
    from tests.connectors.live_listener.test_discretion_ignore_persistence import (
        _bare_connector,
        _mock_buffer,
    )

    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED,
        text="Owner says\n> earlier instruction",
        disc_result=DiscretionResult(verdict="IGNORE", reason="noise", is_fail_open=False),
    )
    stored = buffer.record.call_args.kwargs["full_payload"]
    _sanitize_replay_payload(stored)
    assert stored["payload"]["raw"] == {}
    assert stored["payload"]["untrusted_spans"] == [
        {"kind": "quoted", "start": 0, "end": len("[no text]")}
    ]
    buffer.flush.assert_awaited_once()


def test_audio_config_keeps_device_and_transport_inputs_with_fixed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butlers.connectors.live_listener.config import LiveListenerConfig

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.test/sse")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "ignored-provider")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "ignored-channel")
    monkeypatch.setenv("LIVE_LISTENER_DEVICES", '[{"name":"desk-mic","device":1}]')
    monkeypatch.setenv("LIVE_LISTENER_TRANSCRIPTION_URL", "tcp://transcription.test:10300")
    monkeypatch.setenv("LIVE_LISTENER_VAD_MODEL_PATH", "/synthetic/vad.onnx")
    monkeypatch.setenv("LIVE_LISTENER_SESSION_GAP_S", "17")
    config = LiveListenerConfig.from_env()
    assert (config.channel, config.provider) == ("voice", "live-listener")
    assert [(d.name, d.device) for d in config.devices] == [("desk-mic", 1)]
    assert config.transcription_url == "tcp://transcription.test:10300"
    assert config.session_gap_s == 17
