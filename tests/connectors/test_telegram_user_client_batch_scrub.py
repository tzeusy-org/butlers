"""Batch filtered-event rows scrub 777000 login codes like the batch envelope.

The batch paths record sender "multiple"; the connector must also store the
batch participants so the shared auth-service scrub applies to the preview,
the stored payload and ``error_detail`` [bu-23qn1x].
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.filtered_event_buffer import _sanitize_replay_payload
from butlers.connectors.telegram_user_client import (
    ChatBuffer,
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

_SERVICE = "777000"
_CODE = "".join(("48", "29", "13"))  # bare login code, built at runtime
_PREVIEW_IDX = 6
_PAYLOAD_IDX = -2
_ERROR_IDX = -1


@pytest.fixture
def connector() -> TelegramUserClientConnector:
    config = TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram_user_client:telegram:user123",
    )
    return TelegramUserClientConnector(config, cursor_pool=MagicMock())


def _msgs(*sender_ids: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=i + 1, sender_id=sid) for i, sid in enumerate(sender_ids)]


def _row(connector: TelegramUserClientConnector) -> tuple:
    rows = connector._filtered_event_buffer._rows
    assert len(rows) == 1
    return rows[0]


def _record(connector, messages, **kwargs) -> tuple:
    connector._record_batch_filtered_event(
        chat_id="42",
        batch_event_id="batch:42:1-2",
        filter_reason="discretion:ignore",
        messages=messages,
        **kwargs,
    )
    return _row(connector)


def test_service_batch_ignore_row_withholds_bare_code(connector):
    row = _record(connector, _msgs(int(_SERVICE)), subject_or_preview=_CODE)
    assert _CODE not in str(row[_PREVIEW_IDX])
    assert _CODE not in str(row[_PAYLOAD_IDX])


def test_mixed_batch_with_service_sender_withholds_bare_code(connector):
    row = _record(connector, _msgs(1001, int(_SERVICE)), subject_or_preview=_CODE)
    assert _CODE not in str(row[_PREVIEW_IDX])
    assert row[_PAYLOAD_IDX]["sender"]["participants"] == {_SERVICE: _SERVICE}


def test_non_service_batch_row_is_unchanged(connector):
    text = f"table for 4 at noon, ref {_CODE}"
    row = _record(connector, _msgs(1001, 1002), subject_or_preview=text)
    assert row[_PREVIEW_IDX] == text
    assert "participants" not in row[_PAYLOAD_IDX]["sender"]


def test_service_batch_error_row_scrubs_error_detail(connector):
    row = _record(
        connector,
        _msgs(int(_SERVICE)),
        status="error",
        error_detail=f"submit failed for text {_CODE}",
    )
    assert _CODE not in str(row[_ERROR_IDX])
    assert _CODE not in str(row[_PAYLOAD_IDX])


def test_batch_without_messages_keeps_prior_shape(connector):
    row = _record(connector, None, subject_or_preview="hello")
    assert "participants" not in row[_PAYLOAD_IDX]["sender"]


@pytest.mark.parametrize("sender_ids", [(int(_SERVICE),), (1001, 1002), (1001, int(_SERVICE))])
def test_batch_row_payload_replays_as_a_valid_ingest_envelope(connector, sender_ids):
    from roster.switchboard.tools.contracts import parse_ingest_envelope

    row = _record(connector, _msgs(*sender_ids), subject_or_preview="hello")
    replayed = dict(row[_PAYLOAD_IDX])
    _sanitize_replay_payload(replayed)
    replayed["schema_version"] = "ingest.v1"
    parse_ingest_envelope(replayed)


def _service_message() -> MagicMock:
    msg = MagicMock()
    msg.id, msg.chat_id, msg.sender_id = 7, int(_SERVICE), int(_SERVICE)
    msg.message, msg.media = _CODE, None
    msg.to_dict = lambda: {"id": 7, "message": _CODE}
    return msg


def _allow() -> SimpleNamespace:
    return SimpleNamespace(allowed=True, action="pass_through", reason="", matched_rule_type=None)


async def _flush_service_batch(connector, *, submit=None, policy=None, global_policy=None) -> tuple:
    connector._ingestion_policy.evaluate = MagicMock(return_value=policy or _allow())
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=global_policy or _allow())
    connector._weight_resolver = AsyncMock()
    connector._weight_resolver.resolve = AsyncMock(return_value=0.7)
    connector._participant_count_cache[_SERVICE] = (2, time.monotonic())
    msg = _service_message()
    connector._chat_buffers[_SERVICE] = ChatBuffer(messages=[msg])
    connector._fetch_conversation_history = AsyncMock(return_value=[msg])
    connector._resolve_reply_tos = AsyncMock(return_value=[msg])
    connector._submit_to_ingest = submit or AsyncMock()
    connector._flush_and_drain = AsyncMock()
    connector._save_checkpoint = AsyncMock()
    await connector._flush_chat_buffer(_SERVICE)
    return _row(connector)


async def test_flushed_service_batch_error_row_scrubs_error_detail(connector):
    connector._discretion_dispatcher = None
    row = await _flush_service_batch(
        connector, submit=AsyncMock(side_effect=RuntimeError(f"rejected text {_CODE}"))
    )
    assert _CODE not in str(row[_ERROR_IDX])
    assert _SERVICE in row[_PAYLOAD_IDX]["sender"]["participants"]


async def test_flushed_service_batch_ignore_row_carries_the_hint(connector):
    connector._discretion_dispatcher = AsyncMock()
    connector._discretion_dispatcher.call = AsyncMock(return_value="IGNORE")
    row = await _flush_service_batch(connector)
    assert _CODE not in str(row[_PREVIEW_IDX])
    assert _SERVICE in row[_PAYLOAD_IDX]["sender"]["participants"]


@pytest.mark.parametrize("which", ["connector", "global"])
async def test_flushed_service_batch_policy_rows_carry_the_hint(connector, which):
    connector._discretion_dispatcher = None
    block = SimpleNamespace(
        allowed=False, action="skip", reason="rule", matched_rule_type="sender_domain"
    )
    row = await _flush_service_batch(
        connector,
        policy=block if which == "connector" else None,
        global_policy=block if which == "global" else None,
    )
    assert _SERVICE in row[_PAYLOAD_IDX]["sender"]["participants"]
