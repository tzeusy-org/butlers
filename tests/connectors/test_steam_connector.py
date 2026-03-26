"""Unit tests for the Steam connector.

Covers:
- Envelope builders (all 5 event types)
- Delta detection helpers (_state_hash, _redact_steam_id)
- Filter key extraction (_make_ingestion_envelope_for_filter)
- SteamAccountPoller: per-data-type polling with mocked Steam API
  - recently_played: delta detection, first-poll baseline, event emission
  - online_status: state change detection, no-change skipping
  - achievements: newly unlocked detection, first-poll baseline
  - friends: added/removed friend detection
  - game_library: new game detection, first-poll baseline
- SteamConnector: account discovery, health report, idle mode
- Error handling: rate limits, transient errors, privacy errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.steam import (
    AccountPollerState,
    SteamAccountPoller,
    SteamConnector,
    SteamCursor,
    _make_ingestion_envelope_for_filter,
    _redact_steam_id,
    _state_hash,
    build_achievement_unlock_envelope,
    build_friend_change_envelope,
    build_game_purchase_envelope,
    build_play_session_envelope,
    build_status_change_envelope,
)
from butlers.steam.client import SteamAPIError

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_STEAM_ID = 76561198000000000
_ENDPOINT = f"steam:user:{_STEAM_ID}"
_POLL_TS = "2026-03-26T12:00:00+00:00"


def _make_mock_pool() -> AsyncMock:
    """Return a mock asyncpg Pool that no-ops all cursor saves."""
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)
        )
    )
    pool.execute = AsyncMock(return_value=None)
    return pool


def _make_poller_state(
    intervals: dict[str, int] | None = None,
    cursors: dict[str, SteamCursor] | None = None,
    tracked_games: list[str] | None = None,
) -> AccountPollerState:
    return AccountPollerState(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        api_key="test_api_key",
        intervals=intervals or {},
        cursors=cursors or {},
        tracked_games=tracked_games or [],
    )


# ---------------------------------------------------------------------------
# Delta detection helpers
# ---------------------------------------------------------------------------


class TestStateHash:
    def test_same_data_same_hash(self) -> None:
        data = {"games": [{"appid": 730, "playtime_2weeks": 120}]}
        assert _state_hash(data) == _state_hash(data)

    def test_different_data_different_hash(self) -> None:
        a = {"playtime": 100}
        b = {"playtime": 101}
        assert _state_hash(a) != _state_hash(b)

    def test_key_order_does_not_matter(self) -> None:
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        assert _state_hash(a) == _state_hash(b)

    def test_empty_dict(self) -> None:
        h = _state_hash({})
        assert isinstance(h, str) and len(h) == 64  # SHA-256 hex


class TestRedactSteamId:
    def test_typical_64bit_steam_id(self) -> None:
        redacted = _redact_steam_id(76561198000000000)
        assert "***" in redacted
        assert "7656" in redacted
        assert "0000" in redacted

    def test_short_id_returned_as_is(self) -> None:
        assert _redact_steam_id(12345) == "12345"

    def test_string_input(self) -> None:
        redacted = _redact_steam_id("76561198000000000")
        assert "***" in redacted


# ---------------------------------------------------------------------------
# Filter key extraction
# ---------------------------------------------------------------------------


class TestMakeIngestionEnvelopeForFilter:
    def _play_session_envelope(self) -> dict[str, Any]:
        return build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={"appid": 730, "name": "Counter-Strike 2"},
        )

    def test_sets_source_channel(self) -> None:
        envelope = self._play_session_envelope()
        ie = _make_ingestion_envelope_for_filter(envelope)
        assert ie.source_channel == "gaming"

    def test_sets_sender_address(self) -> None:
        envelope = self._play_session_envelope()
        ie = _make_ingestion_envelope_for_filter(envelope)
        assert ie.sender_address == f"steam:{_STEAM_ID}"

    def test_raw_key_contains_event_type(self) -> None:
        envelope = self._play_session_envelope()
        ie = _make_ingestion_envelope_for_filter(envelope)
        assert "play_session" in ie.raw_key

    def test_raw_key_contains_app_id(self) -> None:
        envelope = self._play_session_envelope()
        ie = _make_ingestion_envelope_for_filter(envelope)
        assert "app_id:730" in ie.raw_key


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


class TestBuildPlaySessionEnvelope:
    def test_schema_version(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert env["schema_version"] == "ingest.v1"

    def test_source_fields(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert env["source"]["channel"] == "gaming"
        assert env["source"]["provider"] == "steam"
        assert env["source"]["endpoint_identity"] == _ENDPOINT

    def test_event_type_field(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert env["event"]["type"] == "play_session"

    def test_external_event_id_format(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        eid = env["event"]["external_event_id"]
        assert eid == f"steam:play:{_STEAM_ID}:730:{_POLL_TS}"

    def test_idempotency_key_equals_external_event_id(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert env["control"]["idempotency_key"] == env["event"]["external_event_id"]

    def test_normalized_text_includes_game_name(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            playtime_2weeks=120,
            playtime_delta=90,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert "Counter-Strike 2" in env["payload"]["normalized_text"]

    def test_normalized_text_hours_and_minutes(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=90,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert "1h 30m" in env["payload"]["normalized_text"]

    def test_policy_tier_default(self) -> None:
        env = build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            playtime_2weeks=120,
            playtime_delta=45,
            poll_ts=_POLL_TS,
            raw={},
        )
        assert env["control"]["policy_tier"] == "default"
        assert env["control"]["ingestion_tier"] == "full"


class TestBuildAchievementUnlockEnvelope:
    def test_external_event_id_format(self) -> None:
        env = build_achievement_unlock_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            achievement_api_name="FIRST_BLOOD",
            achievement_display_name="First Blood",
            achievement_description="Get the first kill.",
            unlock_time=1711449600,
            poll_ts=_POLL_TS,
        )
        assert env["event"]["external_event_id"] == (
            f"steam:achievement:{_STEAM_ID}:730:FIRST_BLOOD"
        )

    def test_normalized_text_format(self) -> None:
        env = build_achievement_unlock_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            achievement_api_name="FIRST_BLOOD",
            achievement_display_name="First Blood",
            achievement_description="Get the first kill.",
            unlock_time=1711449600,
            poll_ts=_POLL_TS,
        )
        assert "First Blood" in env["payload"]["normalized_text"]
        assert "Counter-Strike 2" in env["payload"]["normalized_text"]

    def test_event_type(self) -> None:
        env = build_achievement_unlock_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="CS2",
            achievement_api_name="FIRST_BLOOD",
            achievement_display_name="First Blood",
            achievement_description="",
            unlock_time=0,
            poll_ts=_POLL_TS,
        )
        assert env["event"]["type"] == "achievement_unlock"


class TestBuildStatusChangeEnvelope:
    def test_online_to_playing(self) -> None:
        env = build_status_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            persona_state=1,
            game_extra_info="Dota 2",
            prev_persona_state=1,
            prev_game_extra_info=None,
            poll_ts=_POLL_TS,
        )
        assert "Dota 2" in env["payload"]["normalized_text"]
        assert env["event"]["type"] == "status_change"

    def test_went_offline(self) -> None:
        env = build_status_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            persona_state=0,
            game_extra_info=None,
            prev_persona_state=1,
            prev_game_extra_info=None,
            poll_ts=_POLL_TS,
        )
        assert "offline" in env["payload"]["normalized_text"].lower()

    def test_stopped_playing(self) -> None:
        env = build_status_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            persona_state=1,
            game_extra_info=None,
            prev_persona_state=1,
            prev_game_extra_info="CS2",
            poll_ts=_POLL_TS,
        )
        assert "CS2" in env["payload"]["normalized_text"]

    def test_external_event_id_format(self) -> None:
        env = build_status_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            persona_state=1,
            game_extra_info=None,
            prev_persona_state=0,
            prev_game_extra_info=None,
            poll_ts=_POLL_TS,
        )
        assert env["event"]["external_event_id"] == f"steam:status:{_STEAM_ID}:{_POLL_TS}"


class TestBuildGamePurchaseEnvelope:
    def test_fields(self) -> None:
        env = build_game_purchase_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=1245620,
            game_name="Elden Ring",
            playtime_forever=0,
            poll_ts=_POLL_TS,
        )
        assert env["event"]["type"] == "game_purchase"
        assert env["event"]["external_event_id"] == f"steam:purchase:{_STEAM_ID}:1245620"
        assert "Elden Ring" in env["payload"]["normalized_text"]


class TestBuildFriendChangeEnvelope:
    def test_added_friend(self) -> None:
        env = build_friend_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            friend_steam_id="76561198111111111",
            friend_name="CoolGamer",
            direction="added",
            relationship="friend",
            poll_ts=_POLL_TS,
        )
        assert env["event"]["type"] == "friend_change"
        assert "added" in env["event"]["external_event_id"]
        assert "CoolGamer" in env["payload"]["normalized_text"]

    def test_removed_friend(self) -> None:
        env = build_friend_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            friend_steam_id="76561198111111111",
            friend_name="SomePlayer",
            direction="removed",
            relationship="friend",
            poll_ts=_POLL_TS,
        )
        assert "removed" in env["event"]["external_event_id"]
        normalized = env["payload"]["normalized_text"]
        assert "Removed" in normalized or "removed" in normalized.lower()

    def test_no_friend_name_uses_id(self) -> None:
        env = build_friend_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            friend_steam_id="76561198111111111",
            friend_name=None,
            direction="added",
            relationship="friend",
            poll_ts=_POLL_TS,
        )
        assert "76561198111111111" in env["payload"]["normalized_text"]


# ---------------------------------------------------------------------------
# SteamAccountPoller — recently_played delta detection
# ---------------------------------------------------------------------------


class TestSteamAccountPollerRecentlyPlayed:
    """Tests for the recently_played poller."""

    def _make_poller(
        self,
        cursors: dict | None = None,
        tracked_games: list | None = None,
    ) -> tuple[SteamAccountPoller, AsyncMock]:
        state = _make_poller_state(cursors=cursors, tracked_games=tracked_games)
        pool = _make_mock_pool()
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        metrics = MagicMock()
        metrics.record_ingest_submission = MagicMock()
        metrics.record_source_api_call = MagicMock()
        poller = SteamAccountPoller(
            state=state,
            db_pool=pool,
            mcp_client=mcp_client,
            metrics=metrics,
        )
        return poller, mcp_client

    async def test_first_poll_establishes_baseline_no_events(self) -> None:
        """First poll (no cursor) establishes baseline without emitting events."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_2weeks": 120, "playtime_forever": 500}
                ]
            }
        )

        await poller._poll_recently_played()

        # No events should be submitted (first poll = baseline)
        mcp.call_tool.assert_not_called()

        # Cursor should be saved
        cursor = poller._state.cursors.get("recently_played")
        assert cursor is not None
        assert cursor.state_snapshot is not None
        assert 730 in cursor.state_snapshot

    async def test_no_change_emits_no_events(self) -> None:
        """Second poll with same state should not emit events."""
        prev_state = {730: {"playtime_2weeks": 120, "playtime_forever": 500}}
        prev_hash = _state_hash(prev_state)
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="recently_played",
            state_hash=prev_hash,
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"recently_played": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_2weeks": 120, "playtime_forever": 500}
                ]
            }
        )

        await poller._poll_recently_played()

        # State unchanged → no events
        mcp.call_tool.assert_not_called()

    async def test_playtime_increase_emits_play_session_event(self) -> None:
        """Increased playtime_2weeks emits a play_session event."""
        prev_state = {730: {"playtime_2weeks": 100, "playtime_forever": 500}}
        prev_hash = _state_hash(prev_state)
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="recently_played",
            state_hash=prev_hash,
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"recently_played": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_2weeks": 145, "playtime_forever": 545}
                ]
            }
        )

        await poller._poll_recently_played()

        # Should emit one event
        mcp.call_tool.assert_called_once()
        call_args = mcp.call_tool.call_args[0]
        envelope = call_args[1]
        assert envelope["event"]["type"] == "play_session"
        assert envelope["payload"]["raw"]["app_id"] == 730
        assert envelope["payload"]["raw"]["playtime_delta_minutes"] == 45

    async def test_new_game_in_recently_played_emits_event(self) -> None:
        """A new game appearing in recently_played emits a play_session event."""
        prev_state = {730: {"playtime_2weeks": 100, "playtime_forever": 500}}
        prev_hash = _state_hash(prev_state)
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="recently_played",
            state_hash=prev_hash,
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"recently_played": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_2weeks": 100, "playtime_forever": 500},
                    {"appid": 570, "name": "Dota 2", "playtime_2weeks": 60, "playtime_forever": 60},
                ]
            }
        )

        await poller._poll_recently_played()

        # Dota 2 is new → one event for it
        assert mcp.call_tool.call_count == 1
        envelope = mcp.call_tool.call_args[0][1]
        assert envelope["payload"]["raw"]["app_id"] == 570

    async def test_empty_games_list_skips_gracefully(self) -> None:
        """Empty recently played (privacy/none) is not treated as an error."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(return_value={"games": []})

        await poller._poll_recently_played()

        mcp.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# SteamAccountPoller — online_status
# ---------------------------------------------------------------------------


class TestSteamAccountPollerOnlineStatus:
    def _make_poller(self, cursors: dict | None = None) -> tuple[SteamAccountPoller, AsyncMock]:
        state = _make_poller_state(cursors=cursors)
        pool = _make_mock_pool()
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        metrics = MagicMock()
        metrics.record_ingest_submission = MagicMock()
        metrics.record_source_api_call = MagicMock()
        poller = SteamAccountPoller(
            state=state, db_pool=pool, mcp_client=mcp_client, metrics=metrics
        )
        return poller, mcp_client

    async def test_first_poll_no_events(self) -> None:
        """First status poll establishes baseline without emitting."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": str(_STEAM_ID), "personastate": 1, "gameextrainfo": None}]
        )

        await poller._poll_online_status()
        mcp.call_tool.assert_not_called()
        cursor = poller._state.cursors.get("online_status")
        assert cursor is not None

    async def test_status_change_emits_event(self) -> None:
        """Change in persona_state emits a status_change event."""
        prev_state = {"persona_state": 0, "game_extra_info": None}
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="online_status",
            state_hash=_state_hash(prev_state),
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"online_status": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": str(_STEAM_ID), "personastate": 1, "gameextrainfo": "Dota 2"}]
        )

        await poller._poll_online_status()

        mcp.call_tool.assert_called_once()
        envelope = mcp.call_tool.call_args[0][1]
        assert envelope["event"]["type"] == "status_change"
        assert "Dota 2" in envelope["payload"]["normalized_text"]

    async def test_no_change_skips(self) -> None:
        """No change in status → no event emitted."""
        prev_state = {"persona_state": 1, "game_extra_info": "CS2"}
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="online_status",
            state_hash=_state_hash(prev_state),
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"online_status": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": str(_STEAM_ID), "personastate": 1, "gameextrainfo": "CS2"}]
        )

        await poller._poll_online_status()
        mcp.call_tool.assert_not_called()

    async def test_empty_summary_private_profile(self) -> None:
        """Empty player summary (private profile) is not an error."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.get_player_summaries = AsyncMock(return_value=[])

        await poller._poll_online_status()
        mcp.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# SteamAccountPoller — achievements
# ---------------------------------------------------------------------------


class TestSteamAccountPollerAchievements:
    def _make_poller(
        self,
        cursors: dict | None = None,
        tracked_games: list[str] | None = None,
    ) -> tuple[SteamAccountPoller, AsyncMock]:
        state = _make_poller_state(cursors=cursors, tracked_games=tracked_games)
        pool = _make_mock_pool()
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        metrics = MagicMock()
        metrics.record_ingest_submission = MagicMock()
        metrics.record_source_api_call = MagicMock()
        poller = SteamAccountPoller(
            state=state, db_pool=pool, mcp_client=mcp_client, metrics=metrics
        )
        return poller, mcp_client

    async def test_no_tracked_games_skips(self) -> None:
        """No tracked games → no API calls."""
        poller, mcp = self._make_poller(tracked_games=[])
        steam_mock = AsyncMock()
        steam_mock.request = AsyncMock()
        poller._steam_client = steam_mock

        await poller._poll_achievements()

        steam_mock.request.assert_not_called()
        mcp.call_tool.assert_not_called()

    async def test_first_poll_establishes_baseline(self) -> None:
        """First achievement poll establishes baseline without emitting."""
        poller, mcp = self._make_poller(tracked_games=["730"])
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "playerstats": {
                    "steamID": str(_STEAM_ID),
                    "gameName": "CS2",
                    "achievements": [
                        {"apiname": "FIRST_BLOOD", "achieved": 1, "unlocktime": 1711449600},
                        {"apiname": "NOOB", "achieved": 0, "unlocktime": 0},
                    ],
                }
            }
        )

        await poller._poll_achievements()

        mcp.call_tool.assert_not_called()

    async def test_new_achievement_emits_event(self) -> None:
        """A newly unlocked achievement emits an achievement_unlock event."""
        prev_state = {"FIRST_BLOOD": 1711449600}
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="achievements:730",
            state_hash=_state_hash(prev_state),
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(
            cursors={"achievements:730": cursor},
            tracked_games=["730"],
        )
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "playerstats": {
                    "steamID": str(_STEAM_ID),
                    "gameName": "CS2",
                    "achievements": [
                        {
                            "apiname": "FIRST_BLOOD",
                            "achieved": 1,
                            "unlocktime": 1711449600,
                            "name": "First Blood",
                            "description": "Get the first kill",
                        },
                        {
                            "apiname": "SHARPSHOOTER",
                            "achieved": 1,
                            "unlocktime": 1711460000,
                            "name": "Sharpshooter",
                            "description": "",
                        },
                    ],
                }
            }
        )

        await poller._poll_achievements()

        # Only SHARPSHOOTER is new
        mcp.call_tool.assert_called_once()
        envelope = mcp.call_tool.call_args[0][1]
        assert envelope["event"]["type"] == "achievement_unlock"
        assert "SHARPSHOOTER" in envelope["event"]["external_event_id"]


# ---------------------------------------------------------------------------
# SteamAccountPoller — friends
# ---------------------------------------------------------------------------


class TestSteamAccountPollerFriends:
    def _make_poller(self, cursors: dict | None = None) -> tuple[SteamAccountPoller, AsyncMock]:
        state = _make_poller_state(cursors=cursors)
        pool = _make_mock_pool()
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        metrics = MagicMock()
        metrics.record_ingest_submission = MagicMock()
        metrics.record_source_api_call = MagicMock()
        poller = SteamAccountPoller(
            state=state, db_pool=pool, mcp_client=mcp_client, metrics=metrics
        )
        return poller, mcp_client

    async def test_first_poll_establishes_baseline(self) -> None:
        """First friends poll establishes baseline without emitting."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [
                        {"steamid": "111", "relationship": "friend"},
                        {"steamid": "222", "relationship": "friend"},
                    ]
                }
            }
        )

        await poller._poll_friends()
        mcp.call_tool.assert_not_called()

    async def test_added_friend_emits_event(self) -> None:
        """A new friend emits a friend_change (added) event."""
        prev = {"111": "friend", "222": "friend"}
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="friends",
            state_hash=_state_hash(prev),
            state_snapshot=prev,
        )
        poller, mcp = self._make_poller(cursors={"friends": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [
                        {"steamid": "111", "relationship": "friend"},
                        {"steamid": "222", "relationship": "friend"},
                        {"steamid": "333", "relationship": "friend"},  # new
                    ]
                }
            }
        )

        await poller._poll_friends()

        mcp.call_tool.assert_called_once()
        envelope = mcp.call_tool.call_args[0][1]
        assert envelope["event"]["type"] == "friend_change"
        assert "added" in envelope["event"]["external_event_id"]
        assert "333" in envelope["event"]["external_event_id"]

    async def test_removed_friend_emits_event(self) -> None:
        """A removed friend emits a friend_change (removed) event."""
        prev = {"111": "friend", "222": "friend"}
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="friends",
            state_hash=_state_hash(prev),
            state_snapshot=prev,
        )
        poller, mcp = self._make_poller(cursors={"friends": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [
                        {"steamid": "111", "relationship": "friend"},
                        # 222 removed
                    ]
                }
            }
        )

        await poller._poll_friends()

        mcp.call_tool.assert_called_once()
        envelope = mcp.call_tool.call_args[0][1]
        assert "removed" in envelope["event"]["external_event_id"]

    async def test_private_profile_skips_gracefully(self) -> None:
        """Private friend list (SteamAPIError) is not treated as a hard error."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            side_effect=SteamAPIError(status_code=401, body="Forbidden")
        )

        # Should not raise
        await poller._poll_friends()
        mcp.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# SteamAccountPoller — game_library
# ---------------------------------------------------------------------------


class TestSteamAccountPollerGameLibrary:
    def _make_poller(self, cursors: dict | None = None) -> tuple[SteamAccountPoller, AsyncMock]:
        state = _make_poller_state(cursors=cursors)
        pool = _make_mock_pool()
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        metrics = MagicMock()
        metrics.record_ingest_submission = MagicMock()
        metrics.record_source_api_call = MagicMock()
        poller = SteamAccountPoller(
            state=state, db_pool=pool, mcp_client=mcp_client, metrics=metrics
        )
        return poller, mcp_client

    async def test_first_poll_no_events(self) -> None:
        """First library poll establishes baseline without emitting."""
        poller, mcp = self._make_poller()
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_forever": 500},
                    {"appid": 570, "name": "Dota 2", "playtime_forever": 1000},
                ]
            }
        )

        await poller._poll_game_library()
        mcp.call_tool.assert_not_called()

    async def test_new_game_emits_purchase_event(self) -> None:
        """A new app_id in the library emits a game_purchase event."""
        prev_state = {
            "730": {"name": "CS2", "playtime_forever": 500},
        }
        cursor = SteamCursor(
            endpoint_identity=_ENDPOINT,
            data_type="game_library",
            state_hash=_state_hash(prev_state),
            state_snapshot=prev_state,
        )
        poller, mcp = self._make_poller(cursors={"game_library": cursor})
        poller._steam_client = AsyncMock()
        poller._steam_client.request = AsyncMock(
            return_value={
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_forever": 500},
                    {"appid": 1245620, "name": "Elden Ring", "playtime_forever": 0},
                ]
            }
        )

        await poller._poll_game_library()

        mcp.call_tool.assert_called_once()
        envelope = mcp.call_tool.call_args[0][1]
        assert envelope["event"]["type"] == "game_purchase"
        assert "Elden Ring" in envelope["payload"]["normalized_text"]


# ---------------------------------------------------------------------------
# SteamConnector — health report
# ---------------------------------------------------------------------------


class TestSteamConnectorHealthReport:
    def _make_connector(self) -> SteamConnector:
        pool = _make_mock_pool()
        return SteamConnector(
            switchboard_mcp_url="http://localhost:41100/sse",
            db_pool=pool,
        )

    def test_no_accounts_degraded(self) -> None:
        """Connector with no active accounts reports degraded."""
        connector = self._make_connector()
        status, msg = connector._get_health_state()
        assert status == "degraded"
        assert msg is not None

    def test_health_report_structure(self) -> None:
        """Health report has required fields."""
        connector = self._make_connector()
        report = connector.get_health_report()
        assert "status" in report
        assert "uptime_seconds" in report
        assert "active_accounts" in report
        assert "account_health" in report
        assert report["active_accounts"] == 0

    def test_aggregated_health_healthy(self) -> None:
        """All healthy accounts → healthy overall."""
        connector = self._make_connector()
        state = _make_poller_state()
        state.health = {"recently_played": "healthy", "online_status": "healthy"}
        state.account_health = "healthy"
        connector._poller_states["steam:user:123"] = state
        connector._pollers["steam:user:123"] = MagicMock()

        health, _ = connector._get_health_state()
        assert health == "healthy"

    def test_one_error_account_propagates(self) -> None:
        """One account in error → overall error."""
        connector = self._make_connector()
        state = _make_poller_state()
        state.health = {"recently_played": "error"}
        connector._poller_states["steam:user:123"] = state
        connector._pollers["steam:user:123"] = MagicMock()

        health, _ = connector._get_health_state()
        assert health == "error"

    def test_steam_id_is_redacted_in_report(self) -> None:
        """Health report must not expose full SteamIDs."""
        connector = self._make_connector()
        state = _make_poller_state()
        connector._poller_states[_ENDPOINT] = state
        connector._pollers[_ENDPOINT] = MagicMock()

        report = connector.get_health_report()
        for acct in report["account_health"]:
            steam_id_field = acct["steam_id"]
            # Should not be the full integer as a clean string
            assert "***" in steam_id_field
