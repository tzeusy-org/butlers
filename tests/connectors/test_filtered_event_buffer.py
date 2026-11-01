"""Condensed FilteredEventBuffer tests — core state machine only.

Verifies:
- record() accumulates events in buffer
- flush() clears buffer after writing
- flush failure is non-fatal and never publishes a fleet event
- reason_label helpers return non-empty strings

[bu-35fm7]
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import (
    FilteredEventBuffer,
    _sanitize_replay_payload,
)

pytestmark = pytest.mark.unit


def _make_buffer(
    connector_type: str = "gmail",
    endpoint_identity: str = "gmail:user:alice@example.com",
) -> FilteredEventBuffer:
    return FilteredEventBuffer(
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
    )


def _sample_payload() -> dict:
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id="thread-001",
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="sender@example.com",
        raw={"headers": [], "body": "Hello"},
        normalized_text="Hello",
        policy_tier="full",
    )


def _record_one(buf: FilteredEventBuffer) -> None:
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason="label_exclude:SPAM",
        full_payload=_sample_payload(),
    )


def test_new_buffer_is_empty() -> None:
    assert len(_make_buffer()) == 0


def test_record_increments_length() -> None:
    buf = _make_buffer()
    _record_one(buf)
    assert len(buf) == 1


def test_record_multiple_events(caplog) -> None:
    buf = _make_buffer()
    for i in range(3):
        buf.record(
            external_message_id=f"msg-{i}",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=_sample_payload(),
        )
    assert len(buf) == 3

    for sender, preview, expected, payload in [
        (
            "person@example.test",
            "Your verification code is 482913",
            "Your verification code is [auth-code withheld: example.test]",
            {"source": {"provider": "gmail"}},
        ),
        (
            "777000",
            "482913",
            "[auth-code withheld: telegram]",
            {"source": {"provider": "telegram"}, "sender": {"identity": "other"}},
        ),
        (
            "person@example.test",
            "482913",
            "[auth-code withheld: example.test]",
            {"sender": {"participants": ["777000"]}},
        ),
        ("person@example.test", "Order 482913 on 2031-03-09", "Order 482913 on 2031-03-09", {}),
        ("777000", "2031-03-09 at 12:34", "2031-03-09 at 12:34", {}),
        ("777000", None, None, {}),
        (
            "person@example.test",
            "[auth-code withheld: example.test]",
            "[auth-code withheld: example.test]",
            {},
        ),
    ]:
        buf.record(
            external_message_id="scrub",
            source_channel="email",
            sender_identity=sender,
            subject_or_preview=preview,
            filter_reason="validation_error",
            full_payload=payload,
        )
        assert buf._rows[-1][6] == expected
        assert buf._rows[-1][5] == sender and buf._rows[-1][9] == payload

    with patch(
        "butlers.ingestion_bearer_scrub.scrub_text", side_effect=RuntimeError("private code 482913")
    ):
        buf.record(
            external_message_id="failure",
            source_channel="email",
            sender_identity="777000",
            subject_or_preview="482913",
            filter_reason="validation_error",
            full_payload={},
        )
    assert buf._rows[-1][6] is None

    import json

    from butlers.ingestion_bearer_scrub import scrub_filtered_preview
    from tests.three_seams_helpers import preview_label_controls

    for _, sender, preview, expected, payload in preview_label_controls("gmail"):
        for hints in (payload, json.dumps(payload)):
            buf.record(
                external_message_id="hint-control",
                source_channel="email",
                sender_identity=sender,
                subject_or_preview=preview,
                filter_reason="validation_error",
                full_payload=hints,
            )
            assert buf._rows[-1][6] == expected
            assert buf._rows[-1][5] == sender and buf._rows[-1][9] == hints
            assert (
                scrub_filtered_preview(
                    expected, connector_type="gmail", sender_identity=sender, full_payload=hints
                )
                == expected
            )
    with patch(
        "butlers.ingestion_bearer_scrub.scrub_text",
        side_effect=RuntimeError("private synthetic-reset-token 482913"),
    ):
        for hints in (
            {"source": {"provider": "https://example.test/reset?token=synthetic-reset-token"}},
            "not-json",
        ):
            buf.record(
                external_message_id="hint-error-control",
                source_channel="email",
                sender_identity="person@482913.example.test",
                subject_or_preview="Your verification code is 482913",
                filter_reason="validation_error",
                full_payload=hints,
            )
            assert buf._rows[-1][6] is None
    assert "482913" not in caplog.text and "synthetic-reset-token" not in caplog.text


async def test_flush_clears_buffer() -> None:
    """flush() must clear the buffer after successful write."""
    buf = _make_buffer()
    _record_one(buf)
    buf.record(
        external_message_id="auth",
        source_channel="telegram",
        sender_identity="777000",
        subject_or_preview="482913",
        filter_reason="validation_error",
        full_payload={"source": {"provider": "telegram"}},
    )

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()  # pool.execute() called first for partition ensure
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx

    await buf.flush(pool=mock_pool)
    assert len(buf) == 0
    bound = mock_conn.executemany.await_args.args[1]
    assert bound[0][6] == "Hello"
    assert bound[1][6] == "[auth-code withheld: telegram]" and "482913" not in bound[1][6]
    from datetime import UTC, datetime

    from scripts.scrub_filtered_event_previews import scrub_existing_previews

    for size in (0, 501):
        with pytest.raises(ValueError, match="batch_size"):
            await scrub_existing_previews(mock_pool, cutoff=datetime.now(UTC), batch_size=size)
    with pytest.raises(ValueError, match="offset"):
        await scrub_existing_previews(mock_pool, cutoff=datetime(2031, 3, 6))


# Spec: REQ-core-fleet-events-010
async def test_flush_publishes_ingestion_event_after_successful_batch_write() -> None:
    """A committed filtered-event batch invalidates the unified ingestion feed."""
    buf = _make_buffer()
    _record_one(buf)
    _record_one(buf)

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx

    operations: list[str] = []

    async def record_write(*_args: object) -> None:
        operations.append("write")

    async def record_release(*_args: object) -> None:
        operations.append("release")

    async def record_publish(*_args: object) -> None:
        operations.append("publish")

    mock_conn.executemany.side_effect = record_write
    mock_ctx.__aexit__.side_effect = record_release
    mock_publish = AsyncMock(side_effect=record_publish)

    with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
        await buf.flush(pool=mock_pool)

    assert operations == ["write", "release", "publish"]
    mock_publish.assert_awaited_once_with(mock_pool, "ingestion", {})
    assert len(buf) == 0


# Spec: REQ-core-fleet-events-010
async def test_flush_does_not_retry_committed_rows_when_publication_fails() -> None:
    """A failed best-effort signal cannot duplicate an already committed batch."""
    buf = _make_buffer()
    _record_one(buf)

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx
    mock_publish = AsyncMock(side_effect=RuntimeError("NOTIFY unavailable"))

    with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
        await buf.flush(pool=mock_pool)
        await buf.flush(pool=mock_pool)

    mock_conn.executemany.assert_awaited_once()
    mock_publish.assert_awaited_once_with(mock_pool, "ingestion", {})
    assert len(buf) == 0


# Spec: REQ-core-fleet-events-010
async def test_flush_empty_buffer_is_noop() -> None:
    """flush() on an empty buffer must not call the pool at all."""
    buf = _make_buffer()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_publish = AsyncMock()

    with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
        await buf.flush(pool=mock_pool)

    mock_pool.execute.assert_not_called()
    mock_pool.acquire.assert_not_called()
    mock_publish.assert_not_awaited()


# Spec: REQ-core-fleet-events-010
async def test_flush_db_error_is_non_fatal() -> None:
    """A failed batch INSERT does not publish a fleet event."""
    buf = _make_buffer()
    _record_one(buf)

    mock_conn = AsyncMock()
    mock_conn.executemany.side_effect = RuntimeError("DB down")
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx
    mock_publish = AsyncMock()

    with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
        # Must not raise — filtered events are operational visibility data.
        await buf.flush(pool=mock_pool)

    mock_publish.assert_not_awaited()
    assert len(buf) == 0


@pytest.mark.parametrize(
    "make_label",
    [
        lambda: FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
        lambda: FilteredEventBuffer.reason_validation_error(),
        lambda: FilteredEventBuffer.reason_policy_rule("scope", "block", "sender_domain"),
    ],
)
def test_reason_label_helpers_return_non_empty_str(make_label) -> None:
    label = make_label()
    assert label
    assert isinstance(label, str)


def test_important_dropped_marker_is_stripped_before_replay() -> None:
    """The stored drop_context marker must not reach the replayed envelope (extra=forbid)."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    stored = FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id=None,
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="alice@known.example",
        raw={},
        normalized_text="Hello",
        important_dropped_basis="known_contact",
    )
    assert stored["drop_context"] == {"important_dropped": True, "basis": "known_contact"}

    _sanitize_replay_payload(stored)

    assert "drop_context" not in stored
    parse_ingest_envelope({"schema_version": "ingest.v1", **stored})


async def test_scrubbed_row_replays_with_placeholder_and_unchanged_identity() -> None:
    """A row recorded with an OTP drains and submits placeholder text, identity intact."""
    from butlers.connectors.filtered_event_buffer import drain_replay_pending

    buf = _make_buffer()
    payload = _bearer_payload("Your code is 482913")
    payload["payload"]["untrusted_spans"] = [
        {"kind": "quoted", "start": 13, "end": 19, "origin_identity": "original-author"}
    ]
    payload["event"]["in_reply_to_ref"] = "opaque-replied-message"
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason="label_exclude:SPAM",
        full_payload=payload,
    )
    stored = buf._rows[0][9]

    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "received_at": buf._rows[0][0],
                "external_message_id": "msg-1",
                "full_payload": stored,
            }
        ]
    )
    conn.transaction = MagicMock(return_value=AsyncMock())
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = ctx
    submitted: list[dict] = []

    async def submit(envelope: dict) -> None:
        submitted.append(envelope)

    await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit)

    assert len(submitted) == 1
    envelope = submitted[0]
    assert "482913" not in str(envelope)
    assert "[auth-code withheld" in envelope["payload"]["normalized_text"]
    assert envelope["event"] == payload["event"]
    assert envelope["sender"] == payload["sender"]
    assert envelope["payload"]["untrusted_spans"] == [
        {
            "kind": "quoted",
            "start": 0,
            "end": len(envelope["payload"]["normalized_text"]),
            "origin_identity": "original-author",
        }
    ]
    assert conn.execute.await_args.args[1] == "replay_complete"


def _bearer_payload(text: str, *, sender: str = "sender@example.com", provider: str = "gmail"):
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider=provider,
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id="thread-001",
        observed_at="2026-03-11T10:00:00Z",
        sender_identity=sender,
        raw={"nested": {"body": "Reset: https://example.com/reset?token=synthetic-reset-token"}},
        normalized_text=text,
        policy_tier="full",
    )


def test_record_scrubs_payload_and_error_detail() -> None:
    buf = _make_buffer()
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason="validation_error",
        full_payload=_bearer_payload("Your code is 482913"),
        status="error",
        error_detail="failed on: Your code is 482913",
    )
    row = buf._rows[0]
    payload, error = row[9], row[10]
    assert "482913" not in str(payload) and "synthetic-reset-token" not in str(payload)
    assert "482913" not in error
    assert payload["control"]["bearer_scrubbed"] is True
    assert payload["event"]["external_event_id"] == "msg-001"
    assert (row[7], row[8]) == ("validation_error", "error")

    # Non-native leaves are scrubbed as the text they are persisted as.
    odd = _bearer_payload("hello")
    odd["payload"]["raw"] = {"blob": b"Your code is 482913", "tags": {"Your code is 482913"}}
    buf.record(
        external_message_id="odd",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview=None,
        filter_reason="validation_error",
        full_payload=odd,
    )
    assert "482913" not in str(buf._rows[-1][9])
    buf._rows.pop()

    # Telegram 777000 service sender: a bare code is withheld (aggressive path).
    buf.record(
        external_message_id="auth",
        source_channel="telegram",
        sender_identity="777000",
        subject_or_preview=None,
        filter_reason="validation_error",
        full_payload=_bearer_payload("123456 is your login", sender="777000", provider="telegram"),
    )
    assert "123456" not in buf._rows[1][9]["payload"]["normalized_text"]


def test_record_keeps_ordinary_numbers_byte_identical() -> None:
    import json

    payload = _bearer_payload("Invoice 482913 total 20.50")
    payload["payload"]["raw"] = {"body": "Invoice 482913 total 20.50"}
    expected = json.loads(json.dumps(payload))
    buf = _make_buffer()
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason="label_exclude:SPAM",
        full_payload=payload,
        error_detail="HTTP 500",
    )
    assert buf._rows[0][9] == expected
    assert buf._rows[0][10] == "HTTP 500"


def test_record_detector_failure_withholds_text_without_changing_status() -> None:
    buf = _make_buffer()
    payload = _bearer_payload("Your code is 482913")
    payload["payload"]["untrusted_spans"] = [{"kind": "forwarded", "start": 13, "end": 19}]
    payload["event"]["in_reply_to_ref"] = "opaque-previous-message"
    with patch(
        "butlers.connectors.filtered_event_buffer.scrub_envelope", side_effect=RuntimeError("x")
    ):
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview="Hello",
            filter_reason="validation_error",
            full_payload=payload,
            status="error",
            error_detail="Your code is 482913",
        )
    row = buf._rows[0]
    assert "482913" not in str(row[9]) and "synthetic-reset-token" not in str(row[9])
    assert row[10] == "[withheld: bearer scrub unavailable]"
    assert row[9]["event"]["external_event_id"] == "msg-001"
    assert (row[7], row[8]) == ("validation_error", "error")
    assert row[9]["event"] == payload["event"]
    assert row[9]["payload"]["untrusted_spans"] == [
        {"kind": "forwarded", "start": 0, "end": len(row[9]["payload"]["normalized_text"])}
    ]

    # The fallback is total: a non-mapping payload value is withheld, not raised.
    broken = _bearer_payload("Your code is 482913")
    broken["payload"] = "Your code is 482913"
    with patch(
        "butlers.connectors.filtered_event_buffer.scrub_envelope", side_effect=RuntimeError("x")
    ):
        buf.record(
            external_message_id="broken",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=broken,
        )
    assert "482913" not in str(buf._rows[-1][9])

    # Rewrites may fit known kinds conservatively, but malformed-present metadata
    # must remain rejectable rather than become an unmarked message.
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    for spans in ([{"kind": "unknown", "start": 0, "end": 1}], ["broken"], {"bad": 1}):
        invalid = _bearer_payload("Your code is 482913")
        invalid["payload"]["untrusted_spans"] = spans
        invalid["control"]["policy_tier"] = "default"
        buf.record(
            external_message_id="invalid",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=invalid,
        )
        stored = buf._rows[-1][9]
        assert stored["payload"]["untrusted_spans"] == spans
        with pytest.raises(ValueError, match="untrusted_spans"):
            parse_ingest_envelope({"schema_version": "ingest.v1", **stored})

    # Existing full-payload construction preserves exact fields for unchanged
    # text, collapses only rewritten NUL text, and never treats reply target as
    # the replied-message locator.
    for text in ("owner quote", "owner\x00 quote"):
        spans = [{"kind": "quoted", "start": 6, "end": 11}]
        stored = FilteredEventBuffer.full_payload(
            channel="email",
            provider="gmail",
            endpoint_identity="gmail:test",
            external_event_id="identity",
            external_thread_id=None,
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="owner",
            raw={},
            normalized_text=text,
            untrusted_spans=spans,
            in_reply_to_ref="opaque-prior",
            reply_target_ref="current-target",
        )
        buf.record(
            external_message_id="identity",
            source_channel="email",
            sender_identity="owner",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=stored,
        )
        persisted = buf._rows[-1][9]
        _sanitize_replay_payload(persisted)
        _sanitize_replay_payload(persisted)
        assert persisted["event"]["in_reply_to_ref"] == "opaque-prior"
        assert persisted["event"]["reply_target_ref"] == "current-target"
        assert persisted["payload"]["normalized_text"] == "owner quote"
        assert persisted["payload"]["untrusted_spans"] == (
            spans if "\x00" not in text else [{"kind": "quoted", "start": 0, "end": 11}]
        )
        parse_ingest_envelope({"schema_version": "ingest.v1", **persisted})


def _stored(channel: str, provider: str, thread: str | None, raw: dict, **split: str) -> dict:
    return FilteredEventBuffer.full_payload(
        channel=channel,
        provider=provider,
        endpoint_identity=f"{provider}:endpoint",
        external_event_id="evt-1",
        external_thread_id=thread,
        observed_at="2026-10-01T10:00:00Z",
        sender_identity="4242",
        raw=raw,
        normalized_text="hello",
        **split,
    )


@pytest.mark.parametrize(
    ("stored", "conversation_id", "reply_target_ref"),
    [
        pytest.param(
            _stored(
                "telegram_bot",
                "telegram",
                "-100777",
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 5,
                        "message_thread_id": 9,
                        "is_topic_message": True,
                        "chat": {"id": -100777},
                    },
                },
            ),
            "telegram:-100777:topic:9",
            "-100777:5",
            id="legacy-bot-row-keeps-forum-topic-from-retained-update",
        ),
        pytest.param(
            _stored("telegram_bot", "telegram", "-100777", {}),
            "telegram:-100777",
            "-100777",
            id="legacy-bot-row-without-update-uses-chat",
        ),
        pytest.param(
            _stored("telegram_user_client", "telegram", "998877", {}),
            "telegram:998877",
            None,
            id="legacy-user-client-row",
        ),
        pytest.param(
            _stored("whatsapp_user_client", "whatsapp", "6591234567@s.whatsapp.net", {}),
            "whatsapp:6591234567@s.whatsapp.net",
            None,
            id="legacy-whatsapp-row",
        ),
        pytest.param(
            _stored(
                "telegram_bot",
                "telegram",
                None,
                {},
                external_conversation_id="telegram:-100777:topic:9",
                reply_target_ref="-100777:5",
            ),
            "telegram:-100777:topic:9",
            "-100777:5",
            id="split-row-is-replayed-as-stored",
        ),
    ],
)
def test_replay_splits_pre_split_conversation_identity(
    stored: dict, conversation_id: str, reply_target_ref: str | None
) -> None:
    """Replay gives stored envelopes the conversation key live ingress emits (bu-7exe4.2)."""
    from butlers.tools.switchboard.contracts import parse_ingest_envelope

    _sanitize_replay_payload(stored)

    assert stored["event"]["external_conversation_id"] == conversation_id
    assert stored["event"].get("reply_target_ref") == reply_target_ref
    parse_ingest_envelope({"schema_version": "ingest.v1", **stored})
