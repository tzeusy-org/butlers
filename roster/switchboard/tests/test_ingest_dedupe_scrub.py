"""Content-hash and dedupe keys never derive from withheld bearer material.

Keys are stored beside the scrubbed text, so a key hashed from the raw text lets any reader of
message_inbox/ingestion_events brute-force a withheld one-time code. Fixtures use synthetic
codes and senders only.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.tools.switchboard.ingestion import ingest as ingest_module
from butlers.tools.switchboard.ingestion.ingest import (
    _compute_content_hash_key,
    _compute_dedupe_key,
    _dedupe_text,
    ingest_v1,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestControlV1,
    IngestEnvelopeV1,
    IngestEventV1,
    IngestPayloadV1,
    IngestSenderV1,
    IngestSourceV1,
)

pytestmark = pytest.mark.unit

SENDER = "no-reply@example.com"
CODE_A = "Your code is 482913. It expires in 10 minutes."
CODE_B = "Your code is 175302. It expires in 10 minutes."
PLAIN = "Lunch on Thursday works for me."


def _envelope(
    text: str,
    *,
    event_id: str = "placeholder",
    channel: str = "email",
    bearer_scrubbed: bool = False,
) -> IngestEnvelopeV1:
    return IngestEnvelopeV1(
        schema_version="ingest.v1",
        source=IngestSourceV1(
            channel=channel, provider="gmail", endpoint_identity="owner@example.org"
        ),
        event=IngestEventV1(
            external_event_id=event_id,
            observed_at=datetime(2026, 10, 18, 9, 30, tzinfo=UTC).isoformat(),
        ),
        sender=IngestSenderV1(identity=SENDER),
        payload=IngestPayloadV1(raw={}, normalized_text=text),
        control=IngestControlV1(bearer_scrubbed=bearer_scrubbed),
    )


def _legacy_hash(text: str) -> str:
    return hashlib.sha256(f"{text}:{SENDER}".encode()).hexdigest()[:16]


def test_bearer_messages_differing_only_in_the_code_share_one_key() -> None:
    first, second = _envelope(CODE_A), _envelope(CODE_B)

    assert _compute_content_hash_key(first) == _compute_content_hash_key(second)
    assert _compute_dedupe_key(first) == _compute_dedupe_key(second)


def test_keys_are_not_the_raw_text_hash_that_a_reader_could_brute_force() -> None:
    for text in (CODE_A, CODE_B):
        key = _compute_dedupe_key(_envelope(text))
        assert _legacy_hash(text) not in key


def test_plain_text_key_is_byte_identical_to_the_legacy_derivation() -> None:
    key = _compute_content_hash_key(_envelope(PLAIN))

    assert key.endswith(f":{_legacy_hash(PLAIN)}")
    assert _dedupe_text(_envelope(PLAIN)) == (PLAIN, False)


def test_bearer_text_is_flagged_and_scrubbed() -> None:
    text, withheld = _dedupe_text(_envelope(CODE_A))

    assert withheld is True
    assert "482913" not in text


def test_scrub_failure_fails_closed_to_a_text_independent_key() -> None:
    with patch.object(ingest_module, "scrub_message_text", side_effect=RuntimeError("482913")):
        first = _compute_content_hash_key(_envelope(CODE_A))
        second = _compute_content_hash_key(_envelope(PLAIN))
        flagged = _dedupe_text(_envelope(CODE_A))[1]

    assert first == second
    assert flagged is True
    assert _legacy_hash(CODE_A) not in first


PRE_SCRUBBED_A = "Your code is [auth-code withheld: example.com]. It expires in 10 minutes."


async def _ingest_with_lookups(text: str, *, bearer_scrubbed: bool = False) -> AsyncMock:
    content_lookup = AsyncMock(return_value={"request_id": "00000000-0000-0000-0000-000000000001"})
    envelope = _envelope(text, event_id="evt-1", bearer_scrubbed=bearer_scrubbed).model_dump(
        mode="json"
    )
    with (
        patch.object(ingest_module, "_find_request_by_dedupe_key", AsyncMock(return_value=None)),
        patch.object(ingest_module, "_find_request_by_content_hash", content_lookup),
    ):
        pool = MagicMock()
        pool.acquire.side_effect = RuntimeError("stop after the dedupe lookups")
        try:
            await ingest_v1(pool, envelope)
        except Exception:
            pass
    return content_lookup


async def test_secondary_lookup_runs_for_plain_text() -> None:
    lookup = await _ingest_with_lookups(PLAIN)

    lookup.assert_awaited_once()


async def test_secondary_lookup_is_skipped_when_bearer_material_was_withheld() -> None:
    lookup = await _ingest_with_lookups(CODE_A)

    lookup.assert_not_awaited()


def test_pre_scrubbed_placeholder_text_counts_as_withheld() -> None:
    text, withheld = _dedupe_text(_envelope(PRE_SCRUBBED_A))

    assert text == PRE_SCRUBBED_A
    assert withheld is True


def test_connector_scrub_flag_counts_as_withheld() -> None:
    assert _dedupe_text(_envelope(PLAIN, bearer_scrubbed=True))[1] is True


async def test_secondary_lookup_is_skipped_for_a_pre_scrubbed_second_otp() -> None:
    lookup = await _ingest_with_lookups(PRE_SCRUBBED_A, bearer_scrubbed=True)

    lookup.assert_not_awaited()
