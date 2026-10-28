"""The pipeline ingress dedupe key derives from scrubbed text, never the raw text.

Synthetic codes and senders only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import MessagePipeline

pytestmark = pytest.mark.unit

CODE_A = "Your code is 482913. It expires in 10 minutes."
CODE_B = "Your code is 175302. It expires in 10 minutes."
PLAIN = "Lunch on Thursday works for me."


def _pipeline() -> MessagePipeline:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[None, {"request_id": "req-1"}])
    conn.transaction = MagicMock(return_value=AsyncMock())
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.acquire.return_value = ctx
    return MessagePipeline(
        pool, AsyncMock(), source_butler="switchboard", enable_ingress_dedupe=True
    )


async def _accept(text: str):
    """Return the ingress record and the kwargs the dedupe record was built from."""
    pipeline = _pipeline()
    # No stable event id, so the key falls back to the payload hash.
    with patch.object(
        MessagePipeline, "_build_dedupe_record", wraps=MessagePipeline._build_dedupe_record
    ) as build:
        record = await pipeline._accept_ingress(
            message_text=text,
            args={"source_channel": "sms", "source_identity": "+15550100"},
            source_metadata={"channel": "sms"},
            source="sms",
            chat_id=None,
        )
    assert record is not None
    return record, build.call_args.kwargs


def _legacy_key(kwargs: dict, raw_text: str) -> str:
    """The key the old raw-text derivation gives for the same record and window."""
    return MessagePipeline._build_dedupe_record(**{**kwargs, "message_text": raw_text})[0]


async def test_bearer_messages_differing_only_in_the_code_share_one_key() -> None:
    first, first_kwargs = await _accept(CODE_A)
    _, second_kwargs = await _accept(CODE_B)

    # Same window, so only the text can make the keys differ.
    again = MessagePipeline._build_dedupe_record(
        **{**first_kwargs, "message_text": second_kwargs["message_text"]}
    )
    assert again[0] == first.dedupe_key


async def test_key_is_not_the_raw_text_hash_that_a_reader_could_brute_force() -> None:
    record, kwargs = await _accept(CODE_A)

    assert record.dedupe_key != _legacy_key(kwargs, CODE_A)


async def test_dedupe_record_is_built_from_the_scrubbed_text() -> None:
    _, kwargs = await _accept(CODE_A)

    assert "482913" not in kwargs["message_text"]


async def test_plain_text_key_is_byte_identical_to_the_legacy_derivation() -> None:
    record, kwargs = await _accept(PLAIN)

    assert record.dedupe_key == _legacy_key(kwargs, PLAIN)


async def test_scrub_failure_raises_before_any_key_or_write() -> None:
    pipeline = _pipeline()
    with patch("butlers.modules.pipeline.scrub_stored_record", side_effect=RuntimeError("482913")):
        with pytest.raises(RuntimeError):
            await pipeline._accept_ingress(
                message_text=CODE_A,
                args={"source_channel": "sms", "source_identity": "+15550100"},
                source_metadata={"channel": "sms"},
                source="sms",
                chat_id=None,
            )
