from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import owntracks
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer


@pytest.mark.parametrize("kind", ["transition", "waypoints"])
async def test_legacy_owntracks_parked_event_gets_journal_row_before_replay(kind):
    connector = owntracks.OwnTracksConnector(
        owntracks.OwnTracksConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp"),
        webhook_token="synthetic-fixture",
    )
    endpoint = "owntracks:XY"
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    body = {"_type": kind, "tid": "XY", "tst": int(observed.timestamp())}
    if kind == "transition":
        body["event"] = "enter"
        envelope = owntracks.build_transition_envelope(
            body, endpoint, observed.isoformat(), "metadata"
        )
    else:
        envelope = owntracks.build_waypoints_envelope(
            body, endpoint, observed.isoformat(), "metadata"
        )

    journal_exists = False
    submit_observations = []

    async def commit_journal(*_args, **_kwargs):
        nonlocal journal_exists
        journal_exists = True
        return 1

    async def observe_submit(*_args, **_kwargs):
        submit_observations.append(journal_exists)
        return {"status": "accepted"}

    connector._mcp_client.call_tool = AsyncMock(side_effect=observe_submit)
    replay_row = {
        "id": 1,
        "received_at": observed,
        "external_message_id": envelope["event"]["external_event_id"],
        "full_payload": FilteredEventBuffer.stored_envelope(envelope),
    }
    conn = SimpleNamespace(
        fetch=AsyncMock(return_value=[replay_row]),
        execute=AsyncMock(return_value="UPDATE 1"),
    )

    @asynccontextmanager
    async def acquire():
        yield conn

    connector._db_pool = SimpleNamespace(
        acquire=acquire,
        fetchval=AsyncMock(side_effect=commit_journal),
    )
    buffer = FilteredEventBuffer("owntracks", endpoint)
    await connector._flush_and_drain(
        SimpleNamespace(endpoint_identity=endpoint, filtered_event_buffer=buffer)
    )

    conn.fetch.assert_awaited_once()
    assert conn.execute.await_args.args[1] == "replay_complete"
    assert submit_observations == [True]
