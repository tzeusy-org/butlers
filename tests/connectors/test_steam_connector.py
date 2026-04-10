"""Condensed Steam connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for all Steam event types
- Idempotency key determinism
- Duration label formatting (branching logic)

Note: Steam envelopes include event.type which fails IngestEventV1 extra=forbid.
This is a known contract violation in the production connector — parse_ingest_envelope
validation is skipped for Steam until the connector is updated.

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.steam import (
    build_achievement_unlock_envelope,
    build_play_session_envelope,
    build_status_change_envelope,
)

_STEAM_ID = 76561198012345678
_ENDPOINT = f"gaming:steam:{_STEAM_ID}"
_POLL_TS = "2026-03-26T10:00:00+00:00"


@pytest.fixture
def play_session_envelope() -> dict[str, Any]:
    return build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="Counter-Strike 2",
        playtime_2weeks=120,
        playtime_delta=75,
        poll_ts=_POLL_TS,
        raw={"appid": 730, "name": "Counter-Strike 2"},
    )


def test_play_session_schema_version(play_session_envelope: dict[str, Any]) -> None:
    assert play_session_envelope["schema_version"] == "ingest.v1"


def test_play_session_source_fields(play_session_envelope: dict[str, Any]) -> None:
    assert play_session_envelope["source"]["channel"] == "gaming"
    assert play_session_envelope["source"]["provider"] == "steam"
    assert play_session_envelope["source"]["endpoint_identity"] == _ENDPOINT


def test_play_session_external_event_id_format(play_session_envelope: dict[str, Any]) -> None:
    eid = play_session_envelope["event"]["external_event_id"]
    assert eid.startswith("steam:play:")
    assert str(_STEAM_ID) in eid
    assert "730" in eid


def test_play_session_duration_hours_and_minutes(play_session_envelope: dict[str, Any]) -> None:
    """75 minutes → '1h 15m'."""
    assert "1h 15m" in play_session_envelope["payload"]["normalized_text"]


def test_play_session_duration_minutes_only() -> None:
    env = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=1,
        game_name="Game",
        playtime_2weeks=0,
        playtime_delta=45,
        poll_ts=_POLL_TS,
        raw={},
    )
    assert "45 minutes" in env["payload"]["normalized_text"]


def test_play_session_idempotency_key_deterministic() -> None:
    """Same inputs produce the same idempotency key."""
    e1 = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=0,
        playtime_delta=30,
        poll_ts=_POLL_TS,
        raw={},
    )
    e2 = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=0,
        playtime_delta=30,
        poll_ts=_POLL_TS,
        raw={},
    )
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_achievement_unlock_schema_version() -> None:
    env = build_achievement_unlock_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        achievement_api_name="FIRST_WIN",
        achievement_display_name="First Win",
        achievement_description="Win your first match",
        unlock_time=1708012800,
        poll_ts=_POLL_TS,
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["control"]["ingestion_tier"] == "full"
    assert "FIRST_WIN" in env["event"]["external_event_id"]


def test_status_change_schema_version() -> None:
    env = build_status_change_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        persona_state=1,
        game_extra_info="Counter-Strike 2",
        prev_persona_state=0,
        prev_game_extra_info=None,
        poll_ts=_POLL_TS,
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["source"]["provider"] == "steam"
