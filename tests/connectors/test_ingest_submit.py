from unittest.mock import AsyncMock

import pytest

from butlers.connectors.ingest_submit import IngestRejected, IngestSubmitResult, submit_ingest
from butlers.connectors.mcp_client import TransientToolError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("reply", "duplicate"),
    [
        ({"status": "accepted"}, False),
        ({"status": "queued"}, False),
        ({"status": "duplicate"}, True),
        ({"status": "accepted", "duplicate": True}, True),
    ],
)
async def test_successful_reply_preserves_request_identity(reply, duplicate):
    client = AsyncMock()
    client.call_tool.return_value = {**reply, "request_id": "00000000-0000-7000-8000-000000000001"}
    envelope = {"schema_version": "ingest.v1"}

    result = await submit_ingest(client, envelope)

    assert result == IngestSubmitResult(duplicate, "00000000-0000-7000-8000-000000000001")
    client.call_tool.assert_awaited_once_with("ingest", envelope)


@pytest.mark.parametrize(
    ("reply", "is_refusal"),
    [
        ({"status": "error", "retryable": False, "error": "synthetic echoed sender"}, True),
        ({}, False),
        ({"status": "unrecognized"}, False),
        ({"status": []}, False),
        ("unknown result", False),
    ],
)
async def test_refusal_or_malformed_reply_raises(reply, is_refusal):
    client = AsyncMock()
    client.call_tool.return_value = reply

    with pytest.raises(IngestRejected) as caught:
        await submit_ingest(client, {})

    assert caught.value.is_refusal is is_refusal
    if is_refusal:
        assert reply["error"] not in str(caught.value)
    client.call_tool.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [RuntimeError("unexpected MCP error"), TransientToolError("downstream unavailable")],
)
async def test_existing_client_error_propagates_unchanged(error):
    client = AsyncMock()
    client.call_tool.side_effect = error

    with pytest.raises(type(error)) as caught:
        await submit_ingest(client, {})

    assert caught.value is error
    client.call_tool.assert_awaited_once()


async def test_transient_reply_from_a_direct_client_stays_retryable():
    client = AsyncMock()
    client.call_tool.return_value = {"status": "transient_error", "retryable": True}

    with pytest.raises(TransientToolError):
        await submit_ingest(client, {})

    client.call_tool.assert_awaited_once()
