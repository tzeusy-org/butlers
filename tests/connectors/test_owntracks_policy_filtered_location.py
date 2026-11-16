"""A policy-blocked OwnTracks event keeps no coordinates (bu-s11n0s.73).

A fix the connector's ingestion policy blocks never reaches ``connectors.owntracks_points``, so
no retention grant can name it, and ``connectors.filtered_events`` keeps its row for twelve
months. The row therefore holds no raw body and no coordinate text: only the device, the fix
time and any region names. Without its body it cannot be replayed as the fix it was, so it is
marked not replayable and the replay drain refuses it with that reason.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import pytest

from butlers.connectors.filtered_event_buffer import REPLAY_UNAVAILABLE_KEY, _replay_one
from butlers.connectors.owntracks import POLICY_FILTERED_NOT_REPLAYABLE
from butlers.ingestion_policy import PolicyDecision
from tests.connectors.test_owntracks_device_identity import _make_connector, _stop_all_heartbeats

pytestmark = pytest.mark.unit

_FIX = {
    "_type": "location",
    "tst": 1_700_000_000,
    "tid": "a1",
    "lat": 1.352083,
    "lon": 103.819836,
    "acc": 12,
    "vel": 30,
    "inregions": ["Home"],
    "SSID": "home-wifi",
}


async def _blocked(body: dict[str, Any]) -> dict[str, Any]:
    """Process ``body`` with the device's policy blocking it; return the recorded row."""
    connector = _make_connector()
    try:
        device = await connector._get_or_create_device("owntracks:a1")
        device.ingestion_policy.evaluate = lambda _envelope: PolicyDecision(
            action="block", matched_rule_type="source_filter"
        )
        recorded: list[dict[str, Any]] = []
        device.filtered_event_buffer.record = lambda **row: recorded.append(row)
        await connector._process_webhook_event(body)
        tools = [call.args[0] for call in connector._mcp_client.call_tool.await_args_list]
        assert "ingest" not in tools
        (row,) = recorded
        return row
    finally:
        await _stop_all_heartbeats(connector)


async def test_a_blocked_location_keeps_no_coordinates() -> None:
    row = await _blocked(_FIX)
    stored = row["full_payload"]
    assert stored["payload"]["raw"] is None
    text = stored["payload"]["normalized_text"]
    serialized = json.dumps(row)
    for value in ("1.352", "103.81", "home-wifi", "1.35", "103.8"):
        assert value not in serialized
    assert "Home" in text
    assert row["external_message_id"] == "1700000000:location"
    assert row["sender_identity"] == "owntracks:a1"


async def test_a_blocked_location_is_marked_not_replayable() -> None:
    row = await _blocked(_FIX)
    assert row["full_payload"][REPLAY_UNAVAILABLE_KEY] == POLICY_FILTERED_NOT_REPLAYABLE


_TRANSITION = {
    "_type": "transition",
    "tst": 1_700_000_100,
    "tid": "a1",
    "event": "enter",
    "desc": "Office",
    "lat": 1.352083,
    "lon": 103.819836,
    "acc": 12,
    "wtst": 1_600_000_000,
    "t": "c",
}
_WAYPOINTS = {
    "_type": "waypoints",
    "tst": 1_700_000_200,
    "tid": "a1",
    "waypoints": [
        {"_type": "waypoint", "desc": "Home", "lat": 1.352083, "lon": 103.819836, "rad": 50},
        {"_type": "waypoint", "desc": "Office", "lat": 1.290270, "lon": 103.851959, "rad": 80},
    ],
}


@pytest.mark.parametrize(
    ("body", "external_id", "names"),
    [
        (_TRANSITION, "1700000100:transition:enter", ["Office"]),
        (_WAYPOINTS, "1700000200:waypoints", ["Home", "Office"]),
    ],
    ids=["transition", "waypoints"],
)
async def test_a_blocked_transition_or_waypoints_keeps_no_coordinates(
    body, external_id, names
) -> None:
    row = await _blocked(body)
    stored = row["full_payload"]
    assert stored["payload"]["raw"] is None
    assert stored[REPLAY_UNAVAILABLE_KEY] == POLICY_FILTERED_NOT_REPLAYABLE
    serialized = json.dumps(row)
    for value in ("1.352", "103.81", "1.290", "103.85"):
        assert value not in serialized
    for name in names:
        assert name in stored["payload"]["normalized_text"]
    assert row["external_message_id"] == external_id


async def test_a_blocked_location_without_regions_still_has_text() -> None:
    body = {key: value for key, value in _FIX.items() if key != "inregions"}
    text = (await _blocked(body))["full_payload"]["payload"]["normalized_text"]
    assert text
    assert "1.35" not in text


async def test_the_replay_drain_refuses_a_blocked_location() -> None:
    row = await _blocked(_FIX)
    submitted: list[dict[str, Any]] = []

    async def submit(envelope: dict[str, Any]) -> None:
        submitted.append(envelope)

    claimed = {"id": uuid4(), "full_payload": json.dumps(row["full_payload"])}
    status, detail = await _replay_one(claimed, submit, logging.getLogger(__name__), 5.0)
    assert (status, detail) == (
        "replay_failed",
        f"not replayable: {POLICY_FILTERED_NOT_REPLAYABLE}",
    )
    assert submitted == []
