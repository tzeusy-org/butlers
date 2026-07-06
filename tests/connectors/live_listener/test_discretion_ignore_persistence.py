"""LiveListenerConnector persists discretion IGNORE verdicts (bu-j3fzp).

From bu-n0336: live_listener used the shared DiscretionEvaluator but had zero
filtered_events persistence — an IGNORE verdict just returned silently
(connector.py, pre-bu-j3fzp), unlike whatsapp/telegram which always
self-persist filtered messages. Dropped voice utterances vanished without a
trace, defeating any drop-rate audit.

Covers:
- ``_record_discretion_ignore`` labels the persisted ``filter_reason`` with
  the specific IGNORE kind (genuine LLM verdict vs. each fail-closed default)
  via ``classify_ignore_kind``/``FilteredEventBuffer.reason_discretion_ignore``.
- No-op (no buffer touched) when there is no DB pool.
- Exactly one ``record`` + one ``flush`` per IGNORE — no double-persist.
- Privacy: ``full_payload.raw`` stays empty and ``subject_or_preview`` is
  truncated to 200 chars — metadata-tier, matching the WhatsApp connector's
  discretion-IGNORE persistence rather than Telegram's full raw payload.
- ``stop()`` flushes any buffered-but-unflushed filtered-events rows.

Uses a bare instance (``object.__new__``) per the pattern established in
test_connector_health_state.py, since ``LiveListenerConnector.__init__`` pulls
in full env-parsed device config unrelated to this persistence path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.discretion import DiscretionResult
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.live_listener.connector import LiveListenerConnector

pytestmark = pytest.mark.unit

_OBSERVED_AT = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
_UNIX_MS = 1_782_000_000_000


def _bare_connector(
    *, db_pool: object | None, buffer: FilteredEventBuffer | None
) -> LiveListenerConnector:
    connector = object.__new__(LiveListenerConnector)
    connector._db_pool = db_pool
    connector._filtered_event_buffers = {"kitchen": buffer} if buffer is not None else {}
    return connector


def _mock_buffer() -> MagicMock:
    buffer = MagicMock(spec=FilteredEventBuffer)
    buffer.flush = AsyncMock()
    return buffer


# ---------------------------------------------------------------------------
# filter_reason labeling per IGNORE kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected_kind"),
    [
        ("", "llm_verdict"),
        ("fail-closed: auth_failure", "auth_failure_default"),
        ("fail-closed: failover_exhausted", "failover_exhausted"),
        ("fail-closed: timeout", "timeout_default"),
        ("fail-closed: parse_error", "parse_error_default"),
        ("fail-closed: ValueError", "error_default"),
    ],
)
async def test_record_discretion_ignore_labels_reason_by_kind(
    reason: str, expected_kind: str
) -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason=reason, is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="ambient chatter",
        disc_result=disc_result,
    )

    buffer.record.assert_called_once()
    kwargs = buffer.record.call_args.kwargs
    assert kwargs["filter_reason"] == f"discretion:ignore:{expected_kind}"


# ---------------------------------------------------------------------------
# No-op when there is no DB pool
# ---------------------------------------------------------------------------


async def test_record_discretion_ignore_noop_without_db_pool() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=None, buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="ambient chatter",
        disc_result=disc_result,
    )

    buffer.record.assert_not_called()
    buffer.flush.assert_not_called()


async def test_record_discretion_ignore_noop_when_no_buffer_for_mic() -> None:
    """Defensive: a mic with no registered buffer must not raise."""
    connector = _bare_connector(db_pool=object(), buffer=None)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    # Must not raise even though "kitchen" has no buffer entry.
    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="ambient chatter",
        disc_result=disc_result,
    )


# ---------------------------------------------------------------------------
# No double-persist
# ---------------------------------------------------------------------------


async def test_record_discretion_ignore_persists_exactly_once() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="ambient chatter",
        disc_result=disc_result,
    )

    buffer.record.assert_called_once()
    buffer.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# Privacy: metadata-tier persistence (matches WhatsApp, not Telegram)
# ---------------------------------------------------------------------------


async def test_record_discretion_ignore_full_payload_raw_is_empty() -> None:
    """full_payload.raw stays empty; the transcript never lands there.

    Mirrors the WhatsApp connector's discretion-IGNORE persistence
    (raw={}) rather than Telegram's full message.to_dict() — ambient voice
    capture can pick up bystanders who never opted into the connector.
    """
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="a very long ambient transcript that must not leak into full_payload",
        disc_result=disc_result,
    )

    kwargs = buffer.record.call_args.kwargs
    assert kwargs["full_payload"]["payload"]["raw"] == {}
    assert "normalized_text" not in kwargs["full_payload"]["payload"]


async def test_record_discretion_ignore_preview_truncated_to_200_chars() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)
    long_text = "x" * 500

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text=long_text,
        disc_result=disc_result,
    )

    kwargs = buffer.record.call_args.kwargs
    assert kwargs["subject_or_preview"] == "x" * 200


async def test_record_discretion_ignore_preview_none_for_empty_text() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="",
        disc_result=disc_result,
    )

    kwargs = buffer.record.call_args.kwargs
    assert kwargs["subject_or_preview"] is None


async def test_record_discretion_ignore_uses_ambient_sender_and_voice_channel() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    disc_result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)

    await connector._record_discretion_ignore(
        mic="kitchen",
        unix_ms=_UNIX_MS,
        observed_at=_OBSERVED_AT,
        text="hey",
        disc_result=disc_result,
    )

    kwargs = buffer.record.call_args.kwargs
    assert kwargs["source_channel"] == "voice"
    assert kwargs["sender_identity"] == "ambient"
    assert kwargs["external_message_id"] == "utt:kitchen:1782000000000"
    assert kwargs["full_payload"]["source"]["endpoint_identity"] == "live-listener:mic:kitchen"
    assert kwargs["full_payload"]["event"]["external_thread_id"] is None


# ---------------------------------------------------------------------------
# Buffer flush lifecycle on shutdown
# ---------------------------------------------------------------------------


async def test_stop_flushes_filtered_event_buffers() -> None:
    """stop() must flush any rows buffered since the last per-event flush."""
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    connector._pipeline_tasks = {}
    connector._transcription_clients = {}
    connector._heartbeat = None
    connector._mcp_client = AsyncMock()

    await connector.stop()

    buffer.flush.assert_awaited_once_with(connector._db_pool)


async def test_stop_skips_flush_without_db_pool() -> None:
    buffer = _mock_buffer()
    connector = _bare_connector(db_pool=None, buffer=buffer)
    connector._pipeline_tasks = {}
    connector._transcription_clients = {}
    connector._heartbeat = None
    connector._mcp_client = AsyncMock()

    await connector.stop()

    buffer.flush.assert_not_called()


async def test_stop_continues_after_flush_error() -> None:
    """A flush failure for one mic's buffer must not prevent shutdown from
    completing (best-effort safety net, per FilteredEventBuffer's own
    fail-soft contract)."""
    buffer = _mock_buffer()
    buffer.flush.side_effect = RuntimeError("db unreachable")
    connector = _bare_connector(db_pool=object(), buffer=buffer)
    connector._pipeline_tasks = {}
    connector._transcription_clients = {}
    connector._heartbeat = None
    connector._mcp_client = AsyncMock()

    # Must not raise.
    await connector.stop()

    buffer.flush.assert_awaited_once()
