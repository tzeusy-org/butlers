"""bu-ly3lv5.14: ``submit_ingest`` is the one status-checking path to Switchboard's ingest tool."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.ingest_submit import IngestRejected, submit_ingest

pytestmark = pytest.mark.unit

_ENVELOPE = {"schema_version": "ingest.v1"}


def _client(result):
    client = AsyncMock()
    client.call_tool = AsyncMock(return_value=result)
    return client


@pytest.mark.parametrize(
    ("reply", "duplicate"),
    [
        ({"status": "accepted", "request_id": "r1"}, False),
        ({"status": "queued"}, False),
        ({"status": "duplicate"}, True),
        ({"status": "accepted", "duplicate": True}, True),  # what Switchboard returns today
        ({"status": "accepted", "duplicate": False}, False),
    ],
)
async def test_accepted_outcomes_succeed_with_a_duplicate_flag(reply, duplicate) -> None:
    client = _client(reply)

    outcome = await submit_ingest(client, _ENVELOPE)

    assert (outcome.status, outcome.duplicate, outcome.response) == (
        reply["status"],
        duplicate,
        reply,
    )
    client.call_tool.assert_awaited_once_with("ingest", _ENVELOPE)


async def test_the_request_id_is_exposed_for_callers_that_log_it() -> None:
    outcome = await submit_ingest(_client({"status": "accepted", "request_id": "r1"}), _ENVELOPE)

    assert outcome.request_id == "r1"


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "error", "error": "bad envelope"},
        {"status": "error"},
        {},
        {"request_id": "r1"},  # no status
        {"status": ""},
        {"status": None},
        {"status": "ok"},  # unknown
        {"status": "refused"},
        None,
        "accepted",
        ["accepted"],
    ],
)
async def test_anything_but_an_accepted_outcome_raises(reply) -> None:
    with pytest.raises(IngestRejected):
        await submit_ingest(_client(reply), _ENVELOPE)


async def test_an_error_reply_keeps_the_switchboard_text_and_the_raw_response() -> None:
    reply = {"status": "error", "error": "bad envelope"}

    with pytest.raises(IngestRejected, match="Ingest tool error: bad envelope") as raised:
        await submit_ingest(_client(reply), _ENVELOPE)

    assert raised.value.response == reply


async def test_a_rejection_is_the_runtime_error_callers_already_catch() -> None:
    assert issubclass(IngestRejected, RuntimeError)


async def test_an_exception_from_the_transport_propagates_unchanged() -> None:
    boom = ConnectionError("switchboard down")
    client = AsyncMock()
    client.call_tool = AsyncMock(side_effect=boom)

    with pytest.raises(ConnectionError) as raised:
        await submit_ingest(client, _ENVELOPE)

    assert raised.value is boom
