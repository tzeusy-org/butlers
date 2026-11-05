"""Bound owner-audio authorship evidence (RFC 0038 D5/D18, REQ-audio-authorship-001..003).

The transcript of the verified owner's own non-forwarded audio is owner input only when the
ingress producer that heard the audio binds its facts to the original ingest identity, and only
when the transform that produced the words is an instruction-free ASR-class model function.
This module holds both halves of that contract:

- :data:`ASR_TRANSFORMS` is the declaration of the connector speech-to-text model function
  (D18). A transcription protocol is *qualified* only when its wire contract carries audio and
  fixed server-held decoding configuration, with no task or conversation instructions. Generic
  HTTP/WebSocket speech endpoints are not proven instruction-free, so their output stays
  third-party model-function content.
- :class:`AudioAuthorship` is the keyed binding. The producer signs the canonical record with
  the ``AUDIO_AUTHORSHIP_KEY`` credential; the admission reader verifies it with the same key
  and re-checks every bound field against the accepted row. ``payload.transcription``,
  transcript words, captions and provider metadata are never evidence; without the key nothing
  can be attested or verified, so owner audio fails closed to the non-owner label.

The record carries digests and the provider media identity only: never audio bytes, transcript
text or the key. Nothing here logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final

#: Tier-1 credential (CredentialStore, no env fallback) shared by the producer and admission.
AUDIO_AUTHORSHIP_KEY_NAME: Final = "AUDIO_AUTHORSHIP_KEY"

#: A shorter key is treated as absent (fail closed).
MIN_KEY_BYTES: Final = 32

RECORD_VERSION: Final = 1

#: The only outcome that can be attested; every non-success outcome supplies no words.
OUTCOME_TRANSCRIBED: Final = "transcribed"


@dataclass(frozen=True)
class AsrTransform:
    """One declared connector speech-to-text model function (RFC 0038 D18)."""

    id: str
    protocol: str
    instruction_free: bool


#: Declared speech-to-text transforms, keyed by the transcription client protocol.
ASR_TRANSFORMS: Final[Mapping[str, AsrTransform]] = {
    # Wyoming ``transcribe``: audio chunks plus a fixed language hint; no prompt field.
    "wyoming": AsrTransform("wyoming-asr.v1", "wyoming", instruction_free=True),
    # Generic speech endpoints whose implementation is not proven instruction-free.
    "websocket": AsrTransform("websocket-stt.v1", "websocket", instruction_free=False),
    "http": AsrTransform("http-stt.v1", "http", instruction_free=False),
}

QUALIFIED_TRANSFORM_IDS: Final = frozenset(
    t.id for t in ASR_TRANSFORMS.values() if t.instruction_free
)

#: Audio sources whose own-speech transcript can keep owner authorship.
TELEGRAM_VOICE: Final = "telegram_voice"
TELEGRAM_AUDIO: Final = "telegram_audio"
SOURCE_MEDIA: Final = frozenset({TELEGRAM_VOICE, TELEGRAM_AUDIO})


def transform_for_protocol(protocol: str) -> AsrTransform | None:
    """The declared transform for a transcription client protocol, or ``None``."""
    return ASR_TRANSFORMS.get(protocol.strip().lower())


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def usable_key(secret: str | bytes | None) -> bytes | None:
    """The key as bytes, or ``None`` when absent or too short to bind anything."""
    if secret is None:
        return None
    key = secret.encode() if isinstance(secret, str) else secret
    return key if len(key) >= MIN_KEY_BYTES else None


@dataclass(frozen=True)
class AudioAuthorship:
    """The facts an ingress producer binds to one transcript (design table rows 1-4)."""

    channel: str
    endpoint_identity: str
    idempotency_key: str
    external_event_id: str
    sender_identity: str
    source_media: str
    media_ref: str
    media_sha256: str
    transform: str
    transcript_sha256: str
    outcome: str = OUTCOME_TRANSCRIBED

    def _canonical(self) -> bytes:
        return json.dumps(
            {"v": RECORD_VERSION, **asdict(self)}, sort_keys=True, separators=(",", ":")
        ).encode()

    def attest(self, key: bytes) -> dict[str, Any]:
        """The ``payload.audio_authorship`` object: the record plus its keyed tag."""
        tag = hmac.new(key, self._canonical(), hashlib.sha256).hexdigest()
        return {"v": RECORD_VERSION, **asdict(self), "tag": tag}

    @classmethod
    def verified(cls, value: object, key: bytes | None) -> AudioAuthorship | None:
        """The record when ``value`` carries a valid tag under ``key``; otherwise ``None``.

        A valid tag proves only that the producer bound these fields. The caller still compares
        every field with the accepted row and the provider update it retained.
        """
        if key is None or not isinstance(value, Mapping):
            return None
        fields = {k: v for k, v in value.items() if k not in {"v", "tag"}}
        tag = value.get("tag")
        if value.get("v") != RECORD_VERSION or not isinstance(tag, str):
            return None
        try:
            record = cls(**fields)
        except TypeError:
            return None
        if not all(isinstance(v, str) and v for v in asdict(record).values()):
            return None
        expected = hmac.new(key, record._canonical(), hashlib.sha256).hexdigest()
        return record if hmac.compare_digest(expected, tag) else None
