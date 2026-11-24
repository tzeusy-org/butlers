from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import spotify
from butlers.connectors.filtered_event_buffer import REPLAY_UNAVAILABLE_KEY, FilteredEventBuffer
from tests.connectors.test_spotify_connector import (
    _ENDPOINT,
    _NOW,
    _OBSERVED,
    _SPOTIFY_USER_ID,
    _make_closed_session,
)


@pytest.mark.parametrize("kind", ["music", "spoken"])
@pytest.mark.parametrize("disposition", ["filtered", "legacy_preview", "parked", "parked_refused"])
async def test_spotify_replay_cannot_submit_before_evidence(kind, disposition, monkeypatch):
    parked = disposition in {"parked", "parked_refused"}
    connector = spotify.SpotifyConnector(
        spotify.SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._filtered_event_buffer = FilteredEventBuffer("spotify", _ENDPOINT)
    connector._ingestion_policy = SimpleNamespace(
        scope="connector_rule",
        evaluate=lambda _env: SimpleNamespace(
            allowed=parked, action="block", matched_rule_type="sender_domain"
        ),
    )
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

    evidence_committed = False
    submit_observations = []

    async def record_evidence(*_args, **_kwargs):
        nonlocal evidence_committed
        evidence_committed = True
        return 1

    async def observe_submit(*_args, **_kwargs):
        submit_observations.append(evidence_committed)
        if parked and len(submit_observations) == 1:
            raise RuntimeError("synthetic downstream outage")
        if disposition == "parked_refused":
            return {"status": "error", "retryable": False, "error": "Validation failed"}
        return {"status": "accepted"}

    connector._cursor_pool = SimpleNamespace(fetchval=AsyncMock(side_effect=record_evidence))
    connector._mcp_client.call_tool.side_effect = observe_submit
    conn = SimpleNamespace(executemany=AsyncMock(), fetch=AsyncMock(), execute=AsyncMock())

    @asynccontextmanager
    async def acquire():
        yield conn

    connector._db_pool = SimpleNamespace(acquire=acquire, execute=AsyncMock())
    monkeypatch.setattr(FilteredEventBuffer, "_publish_ingestion_event", AsyncMock())

    if kind == "music":
        await connector._emit_session_summary(_make_closed_session(), _OBSERVED)
    else:
        item = {
            "type": "episode",
            "id": "synthetic-replay-episode",
            "name": "Synthetic episode",
            "uri": "spotify:episode:synthetic-replay-episode",
            "duration_ms": 600_000,
            "show": {"id": "synthetic-show", "name": "Synthetic show"},
        }
        await connector._handle_spoken_playback({"item": item}, item, _NOW, _OBSERVED)

    if not parked:
        assert not evidence_committed
        connector._mcp_client.call_tool.assert_not_awaited()
        await connector._flush_filtered_events()
    else:
        assert evidence_committed
        assert submit_observations == [True]

    conn.executemany.assert_awaited_once()
    _, rows = conn.executemany.await_args.args
    [inserted] = rows
    assert inserted[8] == ("error" if parked else "filtered")

    # Model the owner replay request mutating only the row's status. The
    # real drain still claims/rebuilds/submits exactly the stored envelope.
    replay_payload = dict(inserted[9])
    if disposition == "legacy_preview":
        # Pre-marker writers stored the same pruned preview payload.
        assert replay_payload.pop(REPLAY_UNAVAILABLE_KEY)
        assert replay_payload["payload"]["raw"] == {}
        assert "idempotency_key" not in replay_payload.get("control", {})
    conn.fetch.return_value = [
        {
            "id": 1,
            "external_message_id": inserted[3],
            "full_payload": replay_payload,
            "received_at": inserted[0],
        }
    ]
    await connector._drain_replay()

    conn.fetch.assert_awaited_once()
    conn.execute.assert_awaited_once()
    expected_status = "replay_complete" if disposition == "parked" else "replay_failed"
    assert conn.execute.await_args.args[1] == expected_status
    assert submit_observations == ([True, True] if parked else [])


@pytest.mark.parametrize("kind", ["session", "spoken", "ctx", "digest"])
async def test_a_replay_only_reads_evidence_and_makes_no_copy(kind):
    keys = {
        "session": f"spotify:{_ENDPOINT}:session:1",
        "spoken": f"spotify:{_ENDPOINT}:spoken:1:ep",
        "ctx": f"spotify:{_ENDPOINT}:ctx:1:uri",
        "digest": f"spotify:{_ENDPOINT}:digest:1",
    }
    connector = spotify.SpotifyConnector(
        spotify.SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    connector._cursor_pool = SimpleNamespace(
        fetchval=AsyncMock(return_value=1), execute=AsyncMock()
    )
    envelope = {
        "source": {"channel": "spotify", "endpoint_identity": _ENDPOINT},
        "event": {"external_event_id": "e", "observed_at": _OBSERVED},
        "control": {"idempotency_key": keys[kind]},
    }

    await connector._submit_to_ingest_direct(envelope)

    queries = [c.args[0].lstrip() for c in connector._cursor_pool.fetchval.await_args_list]
    assert queries and all(q.startswith("SELECT") for q in queries)
    connector._cursor_pool.execute.assert_not_awaited()
    connector._mcp_client.call_tool.assert_awaited_once()
