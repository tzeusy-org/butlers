"""The voice idempotency key never derives from a withheld spoken code (synthetic data only)."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from butlers.connectors.live_listener import envelope as envelope_module
from butlers.connectors.live_listener.envelope import mint_idempotency_key

pytestmark = pytest.mark.unit

EP = "live-listener:mic:kitchen"
MS = 1_760_000_000_000
CODE_A = "Your code is 482913. It expires in 10 minutes."
CODE_B = "Your code is 175302. It expires in 10 minutes."
PLAIN = "turn on the kitchen lights please"


def _legacy(text: str) -> str:
    return f"voice:{EP}:{MS}:{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def test_transcripts_differing_only_in_the_code_share_one_key() -> None:
    assert mint_idempotency_key(EP, MS, CODE_A) == mint_idempotency_key(EP, MS, CODE_B)


def test_key_is_not_the_raw_transcript_hash_a_reader_could_brute_force() -> None:
    for text in (CODE_A, CODE_B):
        assert mint_idempotency_key(EP, MS, text) != _legacy(text)


def test_plain_transcript_key_is_byte_identical_to_the_legacy_derivation() -> None:
    assert mint_idempotency_key(EP, MS, PLAIN) == _legacy(PLAIN)


def test_scrub_failure_fails_closed_to_a_transcript_independent_key() -> None:
    with patch.object(envelope_module, "scrub_message_text", side_effect=RuntimeError("482913")):
        first = mint_idempotency_key(EP, MS, CODE_A)
        second = mint_idempotency_key(EP, MS, PLAIN)

    assert first == second
    assert first != _legacy(CODE_A)
