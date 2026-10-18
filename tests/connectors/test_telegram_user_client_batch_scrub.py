"""Batch filtered-event rows scrub 777000 login codes like the batch envelope.

The batch paths record sender "multiple"; the connector must also store the
batch participants so the shared auth-service scrub applies to the preview,
the stored payload and ``error_detail`` [bu-23qn1x].
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from butlers.connectors.telegram_user_client import (
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
    assert row[_PAYLOAD_IDX]["sender"]["participants"] == [_SERVICE]


def test_non_service_batch_row_is_unchanged(connector):
    text = f"table for 4 at noon, ref {_CODE}"
    row = _record(connector, _msgs(1001, 1002), subject_or_preview=text)
    assert row[_PREVIEW_IDX] == text
    assert row[_PAYLOAD_IDX]["sender"]["participants"] == ["1001", "1002"]


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
