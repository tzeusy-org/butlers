"""Owner-audio authorship decision over an accepted row (REQ-audio-authorship-001..003).

The executing Postgres seam is ``test_goal_ingest_postgres.py``; this pins the full matrix of
bound facts the reader compares, each one a way a transcript could otherwise gain authority.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from butlers.audio_authorship import (
    ASR_TRANSFORMS,
    QUALIFIED_TRANSFORM_IDS,
    TELEGRAM_AUDIO,
    TELEGRAM_VOICE,
    AudioAuthorship,
    sha256_hex,
    transform_for_protocol,
    usable_key,
)
from butlers.tools.switchboard.ingestion.audio_admission import (
    owner_speech_verified,
    speech_derived,
)

pytestmark = pytest.mark.unit

KEY = b"k" * 32
TEXT = "synthetic owner words"


def _row(
    *, media: str = "voice", source_media: str = TELEGRAM_VOICE, transform: str = "wyoming-asr.v1"
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "message_id": 7,
        "from": {"id": 987},
        "chat": {"id": 100},
        media: {"file_id": "f", "file_unique_id": "u1", "duration": 3},
    }
    record = AudioAuthorship(
        channel="telegram_bot",
        endpoint_identity="telegram:bot:@b",
        idempotency_key="tg:100:7",
        external_event_id="70",
        sender_identity="987",
        source_media=source_media,
        media_ref="u1",
        media_sha256=sha256_hex(b"audio"),
        transform=transform,
        transcript_sha256=sha256_hex(TEXT),
    )
    payload = {
        "raw": {"update_id": 70, "message": msg},
        "normalized_text": TEXT,
        "transcription": {"source_media": "voice", "status": "transcribed"},
        "audio_authorship": record.attest(KEY),
    }
    return {
        "context": {
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "telegram:bot:@b",
            "idempotency_key": "tg:100:7",
        },
        "raw": {
            "source": {"channel": "telegram_bot", "provider": "telegram"},
            "event": {"external_event_id": "70"},
            "sender": {"identity": "987"},
            "payload": payload,
        },
        "payload": payload,
        "text": TEXT,
    }


def _verified(row: dict[str, Any], key: bytes | None = KEY) -> bool:
    return owner_speech_verified(
        key=key, context=row["context"], raw=row["raw"], payload=row["payload"], text=row["text"]
    )


def _msg(row: dict[str, Any]) -> dict[str, Any]:
    return row["payload"]["raw"]["message"]


def test_bound_owner_voice_and_audio_verify():
    assert _verified(_row())
    assert _verified(_row(media="audio", source_media=TELEGRAM_AUDIO))


def _forwarded(row):
    _msg(row)["forward_origin"] = {"type": "hidden_user", "sender_user_name": "x"}


def _legacy_forward(row):
    _msg(row)["forward_from"] = {"id": 5}


def _external_reply(row):
    _msg(row)["external_reply"] = {"origin": {"type": "user"}}


def _quote(row):
    _msg(row)["quote"] = {"text": "x"}


def _via_bot(row):
    # Sent through an inline bot: bot content under the owner's name, not owner speech.
    _msg(row)["via_bot"] = {"id": 42, "is_bot": True, "username": "somebot"}


def _caption(row):
    _msg(row)["caption"] = "typed"


def _spans(row):
    row["payload"]["untrusted_spans"] = [{"kind": "embedded", "start": 0, "end": 4}]


def _other_text(row):
    row["text"] = "different words"


def _other_media(row):
    _msg(row)["voice"]["file_unique_id"] = "u2"


def _other_identity(row):
    row["context"]["idempotency_key"] = "tg:100:8"


def _other_event(row):
    row["raw"]["event"]["external_event_id"] = "71"


def _other_sender(row):
    row["raw"]["sender"]["identity"] = "111"


def _other_endpoint(row):
    row["context"]["source_endpoint_identity"] = "telegram:bot:@other"


def _other_channel(row):
    row["context"]["source_channel"] = "dashboard"


def _failed_outcome(row):
    row["payload"]["transcription"]["status"] = "stt_unavailable"


def _forged_tag(row):
    row["payload"]["audio_authorship"]["tag"] = "0" * 64


def _edited_field(row):
    row["payload"]["audio_authorship"]["media_ref"] = "u9"


def _no_binding(row):
    del row["payload"]["audio_authorship"]


def _wrong_media_kind(row):
    _msg(row)["audio"] = _msg(row).pop("voice")


@pytest.mark.parametrize(
    "mutate",
    [
        _forwarded,
        _legacy_forward,
        _external_reply,
        _quote,
        _via_bot,
        _caption,
        _spans,
        _other_text,
        _other_media,
        _other_identity,
        _other_event,
        _other_sender,
        _other_endpoint,
        _other_channel,
        _failed_outcome,
        _forged_tag,
        _edited_field,
        _no_binding,
        _wrong_media_kind,
    ],
)
def test_any_mismatched_or_third_party_fact_fails_closed(mutate):
    row = _row()
    mutate(row)
    assert not _verified(row)


def test_unqualified_transform_or_absent_key_cannot_verify():
    # A generic STT endpoint is declared but not instruction-free; even a correctly keyed
    # binding naming it supplies no authorship.
    assert not _verified(_row(transform=ASR_TRANSFORMS["http"].id))
    assert not _verified(_row(transform="claude-transcribe"))
    assert not _verified(_row(), key=None)
    assert not _verified(_row(), key=b"z" * 32)
    assert QUALIFIED_TRANSFORM_IDS == {"wyoming-asr.v1"}
    wyoming, websocket = transform_for_protocol("WYOMING"), transform_for_protocol("websocket")
    assert wyoming is not None and wyoming.instruction_free
    assert websocket is not None and not websocket.instruction_free
    assert transform_for_protocol("openai") is None
    assert usable_key("short") is None and usable_key("x" * 32) == b"x" * 32


def test_room_or_dashboard_source_media_cannot_verify():
    for media in ("live_listener_room", "dashboard_recording"):
        assert not _verified(_row(source_media=media))


def test_speech_derived_reads_the_update_not_the_words():
    row = _row()
    assert speech_derived("telegram", row["payload"], TEXT)
    # Unattested, unlabelled transcript text over a voice update is still speech-derived.
    bare = {"raw": row["payload"]["raw"], "normalized_text": TEXT}
    assert speech_derived("telegram", bare, TEXT)
    # The connector's own descriptor after a failed or disabled attempt is not speech.
    assert not speech_derived("telegram", bare, "[Voice message]")
    audio = copy.deepcopy(bare)
    audio["raw"]["message"]["audio"] = audio["raw"]["message"].pop("voice")
    assert not speech_derived("telegram", audio, "[Audio]")
    assert speech_derived("telegram", audio, TEXT)
    # Typed text and captions keep their own handling.
    typed = copy.deepcopy(bare)
    typed["raw"]["message"]["caption"] = TEXT
    assert not speech_derived("telegram", typed, TEXT)
    assert not speech_derived("telegram", {"raw": {"message": {"text": TEXT}}}, TEXT)
    # Any channel claiming a transcript is speech-derived, so the claim cannot help it.
    dashboard = {"raw": {}, "transcription": {"status": "transcribed"}}
    assert speech_derived("internal", dashboard, TEXT)
    assert speech_derived("internal", {"raw": {}, "audio_authorship": {}}, TEXT)
    assert not speech_derived("internal", {"raw": {}}, TEXT)
