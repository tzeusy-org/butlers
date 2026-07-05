"""Condensed Steam connector tests — ingest.v1 contract only.

Verifies:
- ingest.v1 envelope production for all Steam event types
- Idempotency key determinism
- Duration label formatting (branching logic)
- Wire-envelope contract validation and submission/health behavior (bu-a38da)

bu-a38da root cause: every ``build_*_envelope`` function sets ``event.type``
(e.g. "play_session"), which is an internal-only field used locally for
metrics/policy-key derivation. The ``ingest.v1`` wire contract
(``IngestEventV1``, ``extra="forbid"``) does not define it, so every Steam
submission was rejected server-side with ``event.type: Extra inputs are not
permitted`` — the exact gap this module's docstring used to document and
skip ("parse_ingest_envelope validation is skipped for Steam"). The
Switchboard's ``ingest`` tool swallows that as a normal (non-raising)
``{"status": "error", ...}`` response, and ``SteamAccountPoller._submit_envelope``
never checked it, so the connector logged every submission as a success while
zero rows ever landed in ``public.ingestion_events``. Fixed via
``_to_wire_envelope`` (strips ``event.type`` before transmission) +
``_submit_envelope`` now raising on a tool-level error response, matching the
convention every other connector (e.g. ``telegram_bot._submit_to_ingest``)
already follows.

[bu-35fm7] [bu-a38da] [bu-a25j4]
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.metrics import ConnectorMetrics
from butlers.connectors.steam import (
    AccountPollerState,
    SteamAccountPoller,
    SteamCursor,
    _compute_play_delta,
    _to_wire_envelope,
    build_achievement_unlock_envelope,
    build_friend_change_envelope,
    build_game_purchase_envelope,
    build_play_session_envelope,
    build_status_change_envelope,
)
from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

_STEAM_ID = 76561198012345678
_ENDPOINT = f"gaming:steam:{_STEAM_ID}"
_POLL_TS = "2026-03-26T10:00:00+00:00"


def _make_poller(mcp_client: Any) -> SteamAccountPoller:
    """Build a SteamAccountPoller with lightweight fakes — no DB, no network.

    ``db_pool`` is a bare object: every code path that touches it
    (``FilteredEventBuffer.flush``, ``drain_replay_pending``) already catches
    and swallows its own exceptions, so an object with no attributes is safe
    for tests that never assert on persisted filtered_events rows.
    """
    state = AccountPollerState(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        api_key="fake-api-key",
    )
    return SteamAccountPoller(
        state=state,
        db_pool=object(),
        mcp_client=mcp_client,
        metrics=ConnectorMetrics(connector_type="steam", endpoint_identity=_ENDPOINT),
    )


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


def test_play_session_envelope_contract(play_session_envelope: dict[str, Any]) -> None:
    """play_session carries ingest.v1 schema, gaming/steam source, and steam:play event id."""
    assert play_session_envelope["schema_version"] == "ingest.v1"
    assert play_session_envelope["source"]["channel"] == "gaming"
    assert play_session_envelope["source"]["provider"] == "steam"
    assert play_session_envelope["source"]["endpoint_identity"] == _ENDPOINT
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


# ---------------------------------------------------------------------------
# Delta computation regression tests (bu-d0acy)
# ---------------------------------------------------------------------------


def test_compute_play_delta_first_poll_returns_none() -> None:
    """First-ever poll (prev_snapshot=None) must skip all games — no baseline write."""
    result = _compute_play_delta(app_id=730, playtime_2weeks=1200, prev_snapshot=None)
    assert result is None


def test_compute_play_delta_new_game_in_subsequent_poll_returns_none() -> None:
    """A game appearing for the first time in a non-None snapshot must return None.

    Regression guard for bu-d0acy: before the fix, this returned playtime_2weeks
    (1200 minutes ≈ 20h) as the delta, inflating the day's play_history row with
    up to 14 days of cumulative prior play.
    """
    prev_snapshot = {"570": {"playtime_2weeks": 300, "playtime_forever": 10000}}
    result = _compute_play_delta(app_id=730, playtime_2weeks=1200, prev_snapshot=prev_snapshot)
    assert result is None, (
        "New game (not in prev_snapshot) must return None to establish baseline, "
        "not write the full 14-day cumulative as a single delta"
    )


def test_compute_play_delta_known_game_with_increase_returns_delta() -> None:
    """Playtime increase for a known game returns the positive delta."""
    prev_snapshot = {"730": {"playtime_2weeks": 100, "playtime_forever": 5000}}
    result = _compute_play_delta(app_id=730, playtime_2weeks=145, prev_snapshot=prev_snapshot)
    assert result == 45


def test_compute_play_delta_no_increase_returns_none() -> None:
    """No change in playtime returns None (nothing to write)."""
    prev_snapshot = {"730": {"playtime_2weeks": 100, "playtime_forever": 5000}}
    assert _compute_play_delta(730, 100, prev_snapshot) is None


def test_compute_play_delta_decrease_returns_none() -> None:
    """Decrease in playtime (14-day window roll-off) returns None — no write."""
    prev_snapshot = {"730": {"playtime_2weeks": 100, "playtime_forever": 5000}}
    assert _compute_play_delta(730, 90, prev_snapshot) is None


def test_compute_play_delta_app_id_key_is_str_normalized() -> None:
    """Keys in prev_snapshot are strings; app_id int lookup must match."""
    # Simulates post-restart DB restore where JSON keys are strings.
    prev_snapshot = {"730": {"playtime_2weeks": 80, "playtime_forever": 2000}}
    result = _compute_play_delta(app_id=730, playtime_2weeks=100, prev_snapshot=prev_snapshot)
    assert result == 20


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


# ---------------------------------------------------------------------------
# Wire-envelope contract validation (bu-a38da regression guard)
#
# These are the tests the old module docstring said were "skipped" — every
# builder's output, once run through _to_wire_envelope (what actually gets
# transmitted), must validate against the real ingest.v1 Pydantic contract.
# ---------------------------------------------------------------------------

_BUILDER_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "play_session",
        build_play_session_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            playtime_2weeks=120,
            playtime_delta=30,
            poll_ts=_POLL_TS,
            raw={"appid": 730, "name": "Counter-Strike 2"},
        ),
    ),
    (
        "status_change",
        build_status_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            persona_state=1,
            game_extra_info="Counter-Strike 2",
            prev_persona_state=0,
            prev_game_extra_info=None,
            poll_ts=_POLL_TS,
        ),
    ),
    (
        "achievement_unlock",
        build_achievement_unlock_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            achievement_api_name="FIRST_WIN",
            achievement_display_name="First Win",
            achievement_description="Win your first match",
            unlock_time=1708012800,
            poll_ts=_POLL_TS,
        ),
    ),
    (
        "game_purchase",
        build_game_purchase_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            app_id=730,
            game_name="Counter-Strike 2",
            playtime_forever=10,
            poll_ts=_POLL_TS,
        ),
    ),
    (
        "friend_change",
        build_friend_change_envelope(
            steam_id=_STEAM_ID,
            endpoint_identity=_ENDPOINT,
            friend_steam_id="76561198000000001",
            friend_name="A Friend",
            direction="added",
            relationship="friend",
            poll_ts=_POLL_TS,
        ),
    ),
]


@pytest.mark.parametrize("event_type,envelope", _BUILDER_CASES, ids=[c[0] for c in _BUILDER_CASES])
def test_wire_envelope_validates_against_ingest_v1_contract(
    event_type: str, envelope: dict[str, Any]
) -> None:
    """Every Steam event type's wire envelope must satisfy IngestEnvelopeV1.

    Regression guard for bu-a38da: before the fix, ALL FIVE of these raised
    ``pydantic.ValidationError`` on ``event.type`` (extra_forbidden) — the
    reason zero Steam events were ever ingested despite the connector
    reporting healthy submissions.
    """
    assert envelope["event"]["type"] == event_type
    wire = _to_wire_envelope(envelope)
    assert "type" not in wire["event"], "event.type must not reach the wire contract"
    parse_ingest_envelope(wire)  # raises on any contract violation


def test_to_wire_envelope_does_not_mutate_the_original() -> None:
    """_to_wire_envelope must return a copy — callers still read event.type locally."""
    env = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=10,
        playtime_delta=5,
        poll_ts=_POLL_TS,
        raw={},
    )
    _to_wire_envelope(env)
    assert env["event"]["type"] == "play_session"


# ---------------------------------------------------------------------------
# _submit_envelope behavior (bu-a38da)
# ---------------------------------------------------------------------------


async def test_submit_envelope_strips_event_type_and_validates_on_the_wire() -> None:
    """The envelope actually handed to the MCP client must be contract-clean.

    End-to-end proof (not just of the helper in isolation): whatever
    ``_submit_envelope`` passes to ``call_tool`` must itself pass
    ``parse_ingest_envelope`` — this is precisely what the Switchboard does
    server-side before persisting to ``public.ingestion_events``.
    """
    mcp_client = AsyncMock()
    mcp_client.call_tool.return_value = {
        "request_id": "11111111-1111-7111-8111-111111111111",
        "status": "accepted",
        "duplicate": False,
    }
    poller = _make_poller(mcp_client)
    envelope = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=10,
        playtime_delta=5,
        poll_ts=_POLL_TS,
        raw={"appid": 730},
    )

    await poller._submit_envelope(envelope)

    mcp_client.call_tool.assert_awaited_once()
    tool_name, sent_envelope = mcp_client.call_tool.call_args.args
    assert tool_name == "ingest"
    assert "type" not in sent_envelope["event"]
    parse_ingest_envelope(sent_envelope)


async def test_submit_envelope_raises_on_ingest_tool_error_response() -> None:
    """A tool-level {"status": "error"} response must raise, not look like success.

    Regression guard for bu-a38da bug 2: the Switchboard's ``ingest`` tool
    catches envelope-validation ``ValueError`` and returns a normal
    (non-raising) ``{"status": "error", ...}`` dict rather than an MCP-level
    error. ``_submit_envelope`` previously never inspected the response body,
    so a 100%-failing submission path (like the event.type bug above) was
    silently logged as "success" forever.
    """
    mcp_client = AsyncMock()
    mcp_client.call_tool.return_value = {
        "status": "error",
        "error": "Invalid ingest.v1 envelope: event.type extra_forbidden",
    }
    poller = _make_poller(mcp_client)
    envelope = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=10,
        playtime_delta=5,
        poll_ts=_POLL_TS,
        raw={},
    )

    with pytest.raises(RuntimeError, match="extra_forbidden"):
        await poller._submit_envelope(envelope)


async def test_submit_envelope_still_raises_on_transport_failure() -> None:
    """A raw MCP transport failure (e.g. connection error) still propagates."""
    mcp_client = AsyncMock()
    mcp_client.call_tool.side_effect = ConnectionError("switchboard unreachable")
    poller = _make_poller(mcp_client)
    envelope = build_play_session_envelope(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_2weeks=10,
        playtime_delta=5,
        poll_ts=_POLL_TS,
        raw={},
    )

    with pytest.raises(ConnectionError):
        await poller._submit_envelope(envelope)


# ---------------------------------------------------------------------------
# Health honesty: repeated submission failure must degrade reported health
# (bu-a38da bug 3 — the fabricated-calm rule applies to connector heartbeats)
# ---------------------------------------------------------------------------


async def test_poller_loop_degrades_health_on_repeated_submission_failure() -> None:
    """A poll whose event submission keeps failing must not stay 'healthy'.

    Before this fix, the generic ``except Exception`` branch in
    ``_poller_loop`` (the branch reached by ``_submit_envelope`` raising) only
    incremented a metrics counter — ``state.health``/``consecutive_errors``
    were left untouched, so the connector's heartbeat/health report kept
    claiming "healthy" no matter how many consecutive submissions failed.
    """
    mcp_client = AsyncMock()  # unused directly — poll_fn is monkeypatched below
    poller = _make_poller(mcp_client)

    async def _always_fails() -> None:
        raise RuntimeError("simulated persistent ingest submission failure")

    poller._poll_recently_played = _always_fails  # type: ignore[method-assign]

    task = asyncio.ensure_future(poller._poller_loop("recently_played", 0.01))
    try:
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    state = poller._state
    assert state.consecutive_errors.get("recently_played", 0) >= 1
    assert state.health.get("recently_played") == "degraded"
    assert state.effective_health == "degraded"


def test_reason_source_poll_error_is_distinct_from_submission_error() -> None:
    """Poll (fetch-from-Steam) errors and ingest-submission errors must not share a label.

    bu-a38da evidence showed 7 rows labeled "submission_error" that were
    actually Steam Web API poll failures (DNS resolution, HTTP 502 from
    api.steampowered.com) — nothing to do with Switchboard ingest. Conflating
    the two mislabels operational data and misleads incident triage.
    """
    assert FilteredEventBuffer.reason_source_poll_error() != (
        FilteredEventBuffer.reason_submission_error()
    )


# ---------------------------------------------------------------------------
# Partial-batch submission failure must not corrupt play_history or stall the
# cursor (review follow-up to bu-a38da, flagged during PR #2970 review).
# ---------------------------------------------------------------------------


async def test_recently_played_partial_submission_failure_still_persists_and_advances_cursor() -> (
    None
):
    """A mid-batch submission failure must not double-count playtime or stall the cursor.

    Now that ``_submit_envelope`` raises on failure (this PR's fix), naively
    letting that exception abort ``_poll_recently_played`` mid-loop would leave
    the ``recently_played`` cursor stale while ``_upsert_play_history`` — an
    ADDITIVE upsert — had already run for games earlier in the same batch. The
    next poll would then recompute and re-add the SAME delta for those games,
    double-counting playtime_minutes in ``connectors.steam_play_history``.
    Every game in a batch must get exactly one play_history write and the
    cursor must still advance, even when one game's submission fails.
    """
    mcp_client = AsyncMock()
    mcp_client.call_tool.side_effect = [
        ConnectionError("switchboard unreachable"),
        {
            "request_id": "22222222-2222-7222-8222-222222222222",
            "status": "accepted",
            "duplicate": False,
        },
    ]
    poller = _make_poller(mcp_client)
    poller._steam_client.request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "games": [
                {"appid": 730, "name": "CS2", "playtime_2weeks": 120, "playtime_forever": 500},
                {"appid": 440, "name": "TF2", "playtime_2weeks": 90, "playtime_forever": 300},
            ]
        }
    )
    poller._state.cursors["recently_played"] = SteamCursor(
        endpoint_identity=_ENDPOINT,
        data_type="recently_played",
        state_hash="stale-hash-does-not-match-new-state",
        state_snapshot={
            "730": {"playtime_2weeks": 100, "playtime_forever": 480},
            "440": {"playtime_2weeks": 60, "playtime_forever": 270},
        },
    )

    with (
        patch(
            "butlers.connectors.steam._upsert_play_history", new_callable=AsyncMock
        ) as upsert_mock,
        patch(
            "butlers.connectors.steam._save_steam_cursor", new_callable=AsyncMock
        ) as save_cursor_mock,
    ):
        with pytest.raises(RuntimeError, match="1 of 2 submissions failed"):
            await poller._poll_recently_played()

    # Both games get their play_history write — the failed submission for app
    # 730 must not prevent app 440 (or app 730 itself) from being recorded.
    assert upsert_mock.await_count == 2
    submitted_app_ids = {call.kwargs["app_id"] for call in upsert_mock.await_args_list}
    assert submitted_app_ids == {730, 440}

    # Cursor still advances despite the partial failure, so the next poll does
    # not recompute + re-add the same deltas.
    save_cursor_mock.assert_awaited_once()
    assert poller._state.cursors["recently_played"].state_snapshot == {
        "730": {"playtime_2weeks": 120, "playtime_forever": 500},
        "440": {"playtime_2weeks": 90, "playtime_forever": 300},
    }


# ---------------------------------------------------------------------------
# Sibling-poller audit (bu-a25j4, follow-up to the bu-a38da/#2970 review):
# _poll_achievements, _poll_friends, _poll_game_library, and _poll_online_status
# share _poll_recently_played's "loop, then save cursor once at the end" shape.
# A mid-batch submission failure raises before the cursor save, so the next
# poll recomputes the same diff against a stale snapshot.
#
# The consequence differs by event type because it depends on whether
# ``control.idempotency_key`` (what the Switchboard's ``ingest`` tool actually
# dedupes on — see ``_compute_dedupe_key`` in
# roster/switchboard/tools/ingestion/ingest.py) is stable across polls:
#
# - achievement_unlock / friend_change / game_purchase: idempotency_key has no
#   poll_ts component (see the "_stable_across_polls" tests below), so a
#   resubmission of an already-recorded event collides with the same
#   server-side dedupe_key and comes back ``duplicate: True`` — a safe no-op.
#   The hazard is COSMETIC for these three: no duplicate row lands in
#   ``public.ingestion_events``, only a redundant network round-trip and (a
#   pre-existing, separate issue) an inflated ``steam_events_submitted_total``
#   count. No poller-loop code change applied for these three.
# - status_change (_poll_online_status) embeds poll_ts in idempotency_key (see
#   "_varies_by_poll_ts" below), so no downstream dedupe is possible — a
#   resubmission mints a brand-new key every time, or (if state has drifted
#   back to the stale cursor's value) the transition is silently dropped
#   entirely. This hazard is REAL; _poll_online_status is fixed above to mirror
#   _poll_recently_played's isolate-and-persist-regardless shape.
# ---------------------------------------------------------------------------


async def test_online_status_partial_submission_failure_still_persists_and_advances_cursor() -> (
    None
):
    """A submission failure must not stall the online_status cursor.

    status_change's idempotency_key embeds poll_ts (see
    test_status_change_idempotency_key_varies_by_poll_ts below), so — unlike
    achievement_unlock/friend_change/game_purchase — there is no downstream
    dedupe safety net. A stale cursor after a failed submission would either
    resubmit a distinct status_change every subsequent poll, or silently drop
    the transition if persona state happens to revert to the stale cursor's
    value before the next poll succeeds. The cursor must advance regardless of
    submission outcome, and the failure must still surface so _poller_loop
    degrades health/backs off.
    """
    mcp_client = AsyncMock()
    mcp_client.call_tool.side_effect = ConnectionError("switchboard unreachable")
    poller = _make_poller(mcp_client)
    poller._steam_client.get_player_summaries = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"personastate": 1, "gameextrainfo": "Counter-Strike 2"}]
    )
    poller._state.cursors["online_status"] = SteamCursor(
        endpoint_identity=_ENDPOINT,
        data_type="online_status",
        state_hash="stale-hash-does-not-match-new-state",
        state_snapshot={"persona_state": 0, "game_extra_info": None},
    )

    with patch(
        "butlers.connectors.steam._save_steam_cursor", new_callable=AsyncMock
    ) as save_cursor_mock:
        with pytest.raises(RuntimeError, match="submission failed"):
            await poller._poll_online_status()

    save_cursor_mock.assert_awaited_once()
    assert poller._state.cursors["online_status"].state_snapshot == {
        "persona_state": 1,
        "game_extra_info": "Counter-Strike 2",
    }


def test_achievement_unlock_idempotency_key_stable_across_polls() -> None:
    """No poll_ts component — a same-content resubmission on a later poll dedupes safely."""
    kwargs: dict[str, Any] = dict(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        achievement_api_name="FIRST_WIN",
        achievement_display_name="First Win",
        achievement_description="Win your first match",
        unlock_time=1708012800,
    )
    e1 = build_achievement_unlock_envelope(poll_ts="2026-03-26T10:00:00+00:00", **kwargs)
    e2 = build_achievement_unlock_envelope(poll_ts="2026-03-26T10:05:00+00:00", **kwargs)
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_friend_change_idempotency_key_stable_across_polls() -> None:
    """No poll_ts component — a same-content resubmission on a later poll dedupes safely."""
    kwargs: dict[str, Any] = dict(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        friend_steam_id="76561198000000001",
        friend_name="A Friend",
        direction="added",
        relationship="friend",
    )
    e1 = build_friend_change_envelope(poll_ts="2026-03-26T10:00:00+00:00", **kwargs)
    e2 = build_friend_change_envelope(poll_ts="2026-03-26T10:05:00+00:00", **kwargs)
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_game_purchase_idempotency_key_stable_across_polls() -> None:
    """No poll_ts component — a same-content resubmission on a later poll dedupes safely."""
    kwargs: dict[str, Any] = dict(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        app_id=730,
        game_name="CS2",
        playtime_forever=10,
    )
    e1 = build_game_purchase_envelope(poll_ts="2026-03-26T10:00:00+00:00", **kwargs)
    e2 = build_game_purchase_envelope(poll_ts="2026-03-26T10:05:00+00:00", **kwargs)
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_status_change_idempotency_key_varies_by_poll_ts() -> None:
    """poll_ts IS embedded — no downstream dedupe is possible across polls.

    Contrast with the "_stable_across_polls" tests above: this is exactly why
    a mid-item submission failure in _poll_online_status cannot rely on
    ingest-level idempotency dedupe to absorb a resubmission, and must instead
    isolate the failure and persist the cursor unconditionally (see
    test_online_status_partial_submission_failure_still_persists_and_advances_cursor).
    """
    kwargs: dict[str, Any] = dict(
        steam_id=_STEAM_ID,
        endpoint_identity=_ENDPOINT,
        persona_state=1,
        game_extra_info="Counter-Strike 2",
        prev_persona_state=0,
        prev_game_extra_info=None,
    )
    e1 = build_status_change_envelope(poll_ts="2026-03-26T10:00:00+00:00", **kwargs)
    e2 = build_status_change_envelope(poll_ts="2026-03-26T10:05:00+00:00", **kwargs)
    assert e1["control"]["idempotency_key"] != e2["control"]["idempotency_key"]


async def test_submit_envelope_treats_duplicate_response_as_success() -> None:
    """A ``duplicate: True`` response (ingest-level idempotency dedupe) must not raise.

    Evidence for the bu-a25j4 audit: achievement_unlock/friend_change/
    game_purchase envelopes carry a poll_ts-free idempotency_key (see the
    "_stable_across_polls" tests above), so a resubmission of an
    already-recorded event — exactly what happens when
    _poll_achievements/_poll_friends/_poll_game_library retry a batch after a
    mid-batch abort — collides with the same server-side dedupe_key and comes
    back as ``duplicate: True``, not an error. This is why those three
    pollers' mid-batch-abort hazard is cosmetic (a redundant network call, not
    a duplicate ingestion_events row), and why no poller-loop code change was
    applied for them.
    """
    mcp_client = AsyncMock()
    mcp_client.call_tool.return_value = {
        "request_id": "33333333-3333-7333-8333-333333333333",
        "status": "accepted",
        "duplicate": True,
    }
    poller = _make_poller(mcp_client)
    envelope = build_achievement_unlock_envelope(
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

    await poller._submit_envelope(envelope)  # must not raise

    mcp_client.call_tool.assert_awaited_once()
