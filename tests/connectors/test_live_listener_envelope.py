"""Tests for live-listener ingest.v1 envelope builder.

Covers tasks 6.1, 6.3 from the connector-live-listener openspec:
- ingest.v1 field mapping (source.channel=voice, sender.identity=ambient, ...)
- Synthetic event ID minting (utt:{device_name}:{unix_ms})
- Idempotency key construction (voice:{endpoint_identity}:{unix_ms}:{hash[:8]})
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timezone

import pytest

from butlers.connectors.live_listener.envelope import (
    build_voice_envelope,
    endpoint_identity,
    mint_event_id,
    mint_idempotency_key,
    unix_ms_from_datetime,
    unix_ms_now,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEVICE = "kitchen"
_UNIX_MS = 1_700_000_000_000  # 2023-11-14T22:13:20Z in ms
_TRANSCRIPT = "Hey butler, what's the weather like today?"
_OBSERVED_AT = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
_SESSION_ID = f"voice:{_DEVICE}:{_UNIX_MS}"


# ---------------------------------------------------------------------------
# endpoint_identity
# ---------------------------------------------------------------------------


def test_endpoint_identity_format() -> None:
    """endpoint_identity should produce 'live-listener:mic:{device_name}'."""
    assert endpoint_identity("kitchen") == "live-listener:mic:kitchen"


def test_endpoint_identity_preserves_device_name() -> None:
    """Device names with special characters should be preserved as-is."""
    assert endpoint_identity("living-room-2") == "live-listener:mic:living-room-2"


# ---------------------------------------------------------------------------
# mint_event_id
# ---------------------------------------------------------------------------


def test_mint_event_id_format() -> None:
    """Event IDs should follow 'utt:{device_name}:{unix_ms}' format."""
    event_id = mint_event_id("kitchen", 1_700_000_000_000)
    assert event_id == "utt:kitchen:1700000000000"


def test_mint_event_id_unique_across_mics() -> None:
    """Same unix_ms but different device names produce distinct event IDs."""
    id1 = mint_event_id("kitchen", 12345)
    id2 = mint_event_id("bedroom", 12345)
    assert id1 != id2


def test_mint_event_id_monotonic_per_mic() -> None:
    """Later timestamps produce lexicographically larger event IDs for same mic."""
    id_early = mint_event_id("kitchen", 1_000)
    id_late = mint_event_id("kitchen", 2_000)
    # Numeric ordering is used (unix_ms values are always positive integers)
    ts_early = int(id_early.split(":")[2])
    ts_late = int(id_late.split(":")[2])
    assert ts_late > ts_early


# ---------------------------------------------------------------------------
# mint_idempotency_key
# ---------------------------------------------------------------------------


def test_mint_idempotency_key_format() -> None:
    """Idempotency key should be 'voice:{endpoint}:{unix_ms}:{hash[:8]}'."""
    ep_id = endpoint_identity(_DEVICE)
    key = mint_idempotency_key(ep_id, _UNIX_MS, _TRANSCRIPT)
    parts = key.split(":")
    # Format: voice : live-listener : mic : kitchen : unix_ms : hash
    # Note that endpoint_identity itself contains colons
    assert key.startswith(f"voice:{ep_id}:{_UNIX_MS}:")
    # Hash part is 8 hex chars
    hash_part = parts[-1]
    assert len(hash_part) == 8
    assert all(c in "0123456789abcdef" for c in hash_part)


def test_mint_idempotency_key_content_hash_correctness() -> None:
    """The hash suffix must be the first 8 chars of SHA-256 of the transcript."""
    ep_id = endpoint_identity(_DEVICE)
    expected_hash = hashlib.sha256(_TRANSCRIPT.encode()).hexdigest()[:8]
    key = mint_idempotency_key(ep_id, _UNIX_MS, _TRANSCRIPT)
    assert key.endswith(expected_hash)


def test_mint_idempotency_key_differs_by_content() -> None:
    """Different transcripts produce different idempotency keys."""
    ep_id = endpoint_identity(_DEVICE)
    key1 = mint_idempotency_key(ep_id, _UNIX_MS, "Hello world")
    key2 = mint_idempotency_key(ep_id, _UNIX_MS, "Goodbye world")
    assert key1 != key2


def test_mint_idempotency_key_differs_by_timestamp() -> None:
    """Same transcript but different timestamps produce different keys."""
    ep_id = endpoint_identity(_DEVICE)
    key1 = mint_idempotency_key(ep_id, 1000, _TRANSCRIPT)
    key2 = mint_idempotency_key(ep_id, 2000, _TRANSCRIPT)
    assert key1 != key2


# ---------------------------------------------------------------------------
# build_voice_envelope — field mapping
# ---------------------------------------------------------------------------


@pytest.fixture
def envelope() -> dict:
    """A fully-populated voice envelope built from known test inputs."""
    return build_voice_envelope(
        device_name=_DEVICE,
        unix_ms=_UNIX_MS,
        session_id=_SESSION_ID,
        observed_at=_OBSERVED_AT,
        transcript=_TRANSCRIPT,
        confidence=0.95,
        duration_s=2.3,
        language="en",
        discretion_reason="Sounds like a direct request to the butler.",
    )


def test_envelope_schema_version(envelope: dict) -> None:
    assert envelope["schema_version"] == "ingest.v1"


def test_envelope_source_channel(envelope: dict) -> None:
    assert envelope["source"]["channel"] == "voice"


def test_envelope_source_provider(envelope: dict) -> None:
    assert envelope["source"]["provider"] == "live-listener"


def test_envelope_source_endpoint_identity(envelope: dict) -> None:
    assert envelope["source"]["endpoint_identity"] == f"live-listener:mic:{_DEVICE}"


def test_envelope_event_external_event_id(envelope: dict) -> None:
    assert envelope["event"]["external_event_id"] == f"utt:{_DEVICE}:{_UNIX_MS}"


def test_envelope_event_external_thread_id(envelope: dict) -> None:
    assert envelope["event"]["external_thread_id"] == _SESSION_ID


def test_envelope_event_observed_at_is_iso8601(envelope: dict) -> None:
    """observed_at should be a valid ISO-8601 datetime string."""
    obs = envelope["event"]["observed_at"]
    parsed = datetime.fromisoformat(obs)
    assert parsed.tzinfo is not None  # must be timezone-aware


def test_envelope_event_observed_at_utc(envelope: dict) -> None:
    """observed_at must be in UTC."""
    obs = envelope["event"]["observed_at"]
    parsed = datetime.fromisoformat(obs)
    # UTC offset is 0
    assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_envelope_sender_identity_ambient(envelope: dict) -> None:
    """sender.identity must always be 'ambient' (no speaker ID in v1)."""
    assert envelope["sender"]["identity"] == "ambient"


def test_envelope_payload_raw_fields(envelope: dict) -> None:
    raw = envelope["payload"]["raw"]
    assert raw["transcript"] == _TRANSCRIPT
    assert raw["confidence"] == 0.95
    assert raw["duration_s"] == 2.3
    assert raw["mic"] == _DEVICE
    assert raw["language"] == "en"
    assert raw["discretion_reason"] == "Sounds like a direct request to the butler."


def test_envelope_payload_normalized_text(envelope: dict) -> None:
    assert envelope["payload"]["normalized_text"] == _TRANSCRIPT


def test_envelope_control_policy_tier(envelope: dict) -> None:
    assert envelope["control"]["policy_tier"] == "interactive"


def test_envelope_control_ingestion_tier(envelope: dict) -> None:
    assert envelope["control"]["ingestion_tier"] == "full"


def test_envelope_control_idempotency_key_prefix(envelope: dict) -> None:
    key = envelope["control"]["idempotency_key"]
    ep_id = endpoint_identity(_DEVICE)
    assert key.startswith(f"voice:{ep_id}:{_UNIX_MS}:")


def test_envelope_none_session_id_is_preserved() -> None:
    """When no session exists yet, external_thread_id should be None."""
    env = build_voice_envelope(
        device_name=_DEVICE,
        unix_ms=_UNIX_MS,
        session_id=None,
        observed_at=_OBSERVED_AT,
        transcript=_TRANSCRIPT,
        confidence=0.9,
        duration_s=1.0,
        language="en",
        discretion_reason="test",
    )
    assert env["event"]["external_thread_id"] is None


def test_envelope_naive_datetime_treated_as_utc() -> None:
    """Naive datetimes should be interpreted as UTC in observed_at."""
    naive_dt = datetime(2023, 11, 14, 22, 13, 20)  # no tzinfo
    env = build_voice_envelope(
        device_name=_DEVICE,
        unix_ms=_UNIX_MS,
        session_id=_SESSION_ID,
        observed_at=naive_dt,
        transcript=_TRANSCRIPT,
        confidence=0.9,
        duration_s=1.0,
        language="en",
        discretion_reason="test",
    )
    # Should not raise; observed_at should still be valid ISO-8601
    datetime.fromisoformat(env["event"]["observed_at"])


# ---------------------------------------------------------------------------
# unix_ms helpers
# ---------------------------------------------------------------------------


def test_unix_ms_now_is_positive_integer() -> None:
    ts = unix_ms_now()
    assert isinstance(ts, int)
    assert ts > 0


def test_unix_ms_from_datetime_known_value() -> None:
    dt = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    expected = int(dt.timestamp() * 1000)
    assert unix_ms_from_datetime(dt) == expected


def test_unix_ms_from_datetime_naive_assumed_utc() -> None:
    naive = datetime(2023, 11, 14, 22, 13, 20)
    aware = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert unix_ms_from_datetime(naive) == unix_ms_from_datetime(aware)


def test_unix_ms_from_datetime_non_utc_tz() -> None:
    """Non-UTC datetimes should be converted correctly."""
    from datetime import timedelta

    tz_plus2 = timezone(timedelta(hours=2))
    dt_plus2 = datetime(2023, 11, 14, 22, 13, 20, tzinfo=tz_plus2)
    dt_utc = datetime(2023, 11, 14, 20, 13, 20, tzinfo=UTC)
    assert unix_ms_from_datetime(dt_plus2) == unix_ms_from_datetime(dt_utc)
