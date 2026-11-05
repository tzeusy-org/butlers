"""Owner-audio authorship at goal admission (RFC 0038 D5/D18, REQ-audio-authorship-001..003).

A transcript is connector-produced text, so the accepted row's owner sender is not enough to
make it owner input. :func:`speech_derived` recognises such text from the retained provider
update and the stored fields, never from the words. :func:`owner_speech_verified` then accepts
it as the owner's own speech only when the producer's keyed binding verifies and every bound
fact matches what the accepted row itself retains: source and dedupe identity, sender, the
provider media identity, a qualified instruction-free transform, the exact stored transcript,
and an update with no forward, quote or external-reply evidence. Anything else fails closed:
the caller fences the whole transcript as third-party data and starts the goal exposed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from butlers.audio_authorship import (
    OUTCOME_TRANSCRIBED,
    QUALIFIED_TRANSFORM_IDS,
    TELEGRAM_AUDIO,
    TELEGRAM_VOICE,
    AudioAuthorship,
    sha256_hex,
)
from butlers.connectors.telegram_spans import bot_message_spans, forward_origin

#: Telegram speech media and the descriptor the connector keeps when nothing was transcribed.
_SPEECH_DESCRIPTORS = {
    "voice": "[Voice message]",
    "audio": "[Audio]",
    "video_note": "[Video message]",
}
_MEDIA_KEY = {TELEGRAM_VOICE: "voice", TELEGRAM_AUDIO: "audio"}


def telegram_message(update: object) -> dict[str, Any] | None:
    if not isinstance(update, Mapping):
        return None
    for key in ("message", "edited_message", "channel_post"):
        msg = update.get(key)
        if isinstance(msg, dict):
            return msg
    return None


def speech_derived(provider: object, payload: Mapping[str, Any], text: str) -> bool:
    """Whether the accepted text claims to be, or can only be, a transcript of audio."""
    transcription = payload.get("transcription")
    if payload.get("audio_authorship") is not None or (
        isinstance(transcription, Mapping) and transcription.get("status") == OUTCOME_TRANSCRIBED
    ):
        return True
    if provider != "telegram":
        return False
    msg = telegram_message(payload.get("raw"))
    if msg is None or msg.get("text") or msg.get("caption"):
        return False  # typed text and captions keep their own handling
    descriptors = [d for key, d in _SPEECH_DESCRIPTORS.items() if key in msg]
    return bool(descriptors) and text not in descriptors


def owner_speech_verified(
    *,
    key: bytes | None,
    context: Mapping[str, Any],
    raw: Mapping[str, Any],
    payload: Mapping[str, Any],
    text: str,
) -> bool:
    """Whether a speech-derived accepted text is the owner's own, qualified transcript."""
    record = AudioAuthorship.verified(payload.get("audio_authorship"), key)
    if record is None:
        return False
    source, event, sender = raw.get("source"), raw.get("event"), raw.get("sender")
    if (
        not isinstance(source, Mapping)
        or not isinstance(event, Mapping)
        or not isinstance(sender, Mapping)
    ):
        return False
    media_key = _MEDIA_KEY.get(record.source_media)
    msg = telegram_message(payload.get("raw"))
    if (
        media_key is None
        or msg is None
        or record.outcome != OUTCOME_TRANSCRIBED
        or record.transform not in QUALIFIED_TRANSFORM_IDS
        or record.channel != "telegram_bot"
        or source.get("provider") != "telegram"
        or record.channel != context.get("source_channel")
        or record.endpoint_identity != context.get("source_endpoint_identity")
        or record.idempotency_key != context.get("idempotency_key")
        or record.external_event_id != event.get("external_event_id")
        or record.sender_identity != sender.get("identity")
        or record.transcript_sha256 != sha256_hex(text)
    ):
        return False
    media = msg.get(media_key)
    transcription = payload.get("transcription")
    return (
        isinstance(media, Mapping)
        and media.get("file_unique_id") == record.media_ref
        # Own speech only: the provider update carries no forward, quote or outside reply,
        # and the connector marked nothing either.
        and not msg.get("text")
        and not msg.get("caption")
        and forward_origin(msg) is None
        and not msg.get("quote")
        # Sent through an inline bot: bot content under the owner's name, not owner speech.
        and not msg.get("via_bot")
        and not bot_message_spans(msg, text)
        and not payload.get("untrusted_spans")
        # Outcome metadata is no evidence, but a contradicting outcome voids the binding.
        and (
            not isinstance(transcription, Mapping)
            or transcription.get("status") == OUTCOME_TRANSCRIBED
        )
    )
