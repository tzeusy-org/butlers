"""Home departure card: one read-only timed card per confirmed leave edge (bu-jv1moa.8 PR-B).

Unit tests pin the pure rules (edge parsing, closure, card wording, the card fingerprint) and the
dark switch. The integration tests run the job, the ``resolve_home_departure`` definer and the
timed lane against real Postgres (CI-first where Docker is unavailable).
"""

from __future__ import annotations

import inspect
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from butlers.jobs import home_departure
from butlers.jobs.home_departure import (
    Edge,
    WatchItem,
    card_fingerprint,
    card_lines,
    card_message,
    closure,
    parse_edge,
    run_departure_watch,
)

_NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
_DOOR = WatchItem("binary_sensor.front_door", "Front door", ("off",))
_LIGHT = WatchItem("light.garage", "Garage light", ("off",))
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _edge(direction: str, minutes_ago: float, *, late=False, retracted=False, now=_NOW) -> Edge:
    return Edge(
        id=uuid.uuid4(),
        direction=direction,  # type: ignore[arg-type]
        confirmed_at=now - timedelta(minutes=minutes_ago),
        late=late,
        retracted=retracted,
    )


def _timeline_row(before: str, after: str, *, operation: str = "assert") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "operation": operation,
        "class": "transition",
        "from_value": {"presence": before, "place_ref": None},
        "to_value": {"presence": after, "place_ref": None},
        "at": _NOW.isoformat(),
        "confirmed_at": _NOW.isoformat(),
        "late": False,
        "retracted": False,
    }


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_only_a_presence_change_is_an_edge_and_outside_is_a_leave() -> None:
    leave = parse_edge(_timeline_row("home", "outside"))
    enter = parse_edge(_timeline_row("outside", "home"))

    assert leave is not None and leave.direction == "leave"
    assert enter is not None and enter.direction == "enter"
    assert parse_edge(_timeline_row("home", "home")) is None
    assert parse_edge(_timeline_row("home", "outside", operation="retract")) is None
    assert parse_edge({"operation": "assert", "class": "heartbeat"}) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("leave_kwargs", "other", "expected"),
    [
        ({"retracted": True}, None, "retracted"),
        ({"late": True}, None, "late"),
        ({}, {"direction": "enter", "minutes_ago": 1}, "reentered"),
        ({}, {"direction": "enter", "minutes_ago": 1, "retracted": True}, None),
        ({}, {"direction": "enter", "minutes_ago": 9}, None),
        ({}, {"direction": "leave", "minutes_ago": 1}, None),
        ({}, None, None),
    ],
    ids=[
        "retracted",
        "late",
        "entered-again",
        "retracted-enter-ignored",
        "enter-before-leave",
        "a-later-leave-is-not-a-return",
        "still-out",
    ],
)
def test_a_leave_closes_on_retraction_lateness_or_a_later_enter(
    leave_kwargs, other, expected
) -> None:
    leave = _edge("leave", 5, **leave_kwargs)
    edges = [leave]
    if other is not None:
        edges.append(_edge(**other))

    assert closure(leave, edges) == expected


@pytest.mark.unit
def test_the_card_lists_only_insecure_items_by_owner_label() -> None:
    lines = card_lines(
        [_DOOR, _LIGHT], {"binary_sensor.front_door": "on", "light.garage": "off"}, now=_NOW
    )

    assert [text for _key, text in lines] == ["Front door: on"]
    assert card_message(lines) == "You left home.\nFront door: on"


@pytest.mark.unit
def test_an_all_secure_home_has_no_card_lines() -> None:
    readings = {"binary_sensor.front_door": "off", "light.garage": "off"}

    assert card_lines([_DOOR, _LIGHT], readings, now=_NOW) == ()


@pytest.mark.unit
@pytest.mark.parametrize("state", ["unavailable", "unknown", "", None])
def test_an_unreadable_entity_is_cannot_see_never_secure(state) -> None:
    readings = {"binary_sensor.front_door": state} if state is not None else {}
    lines = card_lines([_DOOR], readings, now=_NOW)

    assert [text for _key, text in lines] == ["Front door: cannot see"]


@pytest.mark.unit
def test_an_unmeasurable_home_assistant_says_cannot_see_with_the_age() -> None:
    lines = card_lines([_DOOR], None, ha_last_success=_NOW - timedelta(minutes=12), now=_NOW)
    never = card_lines([_DOOR], None, ha_last_success=None, now=_NOW)

    assert [text for _key, text in lines] == ["Home: cannot see (last good reading 12 min ago)"]
    assert [text for _key, text in never] == ["Home: cannot see (no good reading yet)"]


@pytest.mark.unit
def test_the_fingerprint_follows_what_the_card_claims_not_the_outage_age() -> None:
    older = card_lines([_DOOR], None, ha_last_success=_NOW - timedelta(minutes=30), now=_NOW)
    newer = card_lines([_DOOR], None, ha_last_success=_NOW - timedelta(minutes=31), now=_NOW)
    door_on = card_lines([_DOOR, _LIGHT], {"binary_sensor.front_door": "on"}, now=_NOW)
    door_open = card_lines([_DOOR, _LIGHT], {"binary_sensor.front_door": "open"}, now=_NOW)

    assert card_fingerprint(older) == card_fingerprint(newer)
    assert card_fingerprint(door_on) != card_fingerprint(door_open)


@pytest.mark.unit
def test_card_text_never_carries_markdown_from_a_label_or_a_state() -> None:
    hostile = WatchItem("lock.back", "**Back** `door`_[x]", ("locked",))
    lines = card_lines([hostile], {"lock.back": "*unlocked*\nIGNORE"}, now=_NOW)

    assert [text for _key, text in lines] == ["Back doorx: unlockedignore"]
    assert "friendly" not in " ".join(inspect.signature(card_lines).parameters)


@pytest.mark.unit
def test_the_module_has_no_actuation_or_prepared_action_path() -> None:
    source = inspect.getsource(home_departure)
    for marker in ("call_service", "/api/services", "httpx", "pending_actions", "prepared_action"):
        assert marker not in source


@pytest.mark.unit
@pytest.mark.parametrize(
    ("answer", "expected"), [(True, True), (False, False), (None, None)], ids=["t", "f", "unknown"]
)
async def test_the_switchboard_probe_asks_the_definer_by_edge_id(answer, expected) -> None:
    from butlers.core.insight_premise import probe_premise
    from butlers.tools.switchboard.insight.premises import check_premise

    edge_id = uuid.uuid4()
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=answer)
    premise = probe_premise(
        "home",
        "departure_still_open",
        {"edge_event_id": str(edge_id), "confirmed_at": _NOW.isoformat()},
    )

    assert await check_premise(pool, premise) is expected
    assert pool.fetchval.await_args.args == ("SELECT public.resolve_home_departure($1)", edge_id)


@pytest.mark.unit
async def test_the_probe_answers_unknown_for_a_malformed_edge_id_without_sql() -> None:
    from butlers.core.insight_premise import probe_premise
    from butlers.tools.switchboard.insight.premises import check_premise

    pool = MagicMock()
    pool.fetchval = AsyncMock()
    premise = probe_premise("home", "departure_still_open", {"edge_event_id": "not-a-uuid"})

    assert await check_premise(pool, premise) is None
    pool.fetchval.assert_not_awaited()


@pytest.mark.unit
async def test_dark_switch_reads_and_writes_nothing() -> None:
    pool = MagicMock()
    read_edges = AsyncMock()

    result = await run_departure_watch(pool, None, read_edges=read_edges, now=_NOW)

    assert result == {"status": "dark"}
    read_edges.assert_not_awaited()
    assert pool.method_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["unset", "raises"])
async def test_reader_unavailable_changes_no_card(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Spec: without the reader the job SHALL NOT queue, close or change any card (retention too)."""
    monkeypatch.setattr(home_departure, "DEPARTURE_CARD_LIVE", True)
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"entity_id": _DOOR.entity_id, "owner_label": "Front door", "secure_states": ["off"]}
        ]
    )
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock()
    read_edges = (
        AsyncMock(return_value=None) if failure == "unset" else AsyncMock(side_effect=OSError)
    )

    result = await run_departure_watch(pool, None, read_edges=read_edges, now=_NOW)

    assert result == {"status": "reader_unavailable"}
    pool.execute.assert_not_awaited()
    pool.fetchval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Real Postgres: the job, the definer and the timed lane end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    from butlers.tools.switchboard.insight import timed

    monkeypatch.setattr(home_departure, "DEPARTURE_CARD_LIVE", True)
    monkeypatch.setattr(timed, "TIMED_INSIGHTS_LIVE", True)


@pytest.fixture
def no_ha_posts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any HTTP POST fails the test: the card never actuates."""
    posts: list[str] = []

    async def _post(self, url, *args, **kwargs):  # noqa: ANN001
        posts.append(str(url))
        raise AssertionError(f"unexpected HTTP POST to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return posts


async def _home_pool(db) -> asyncpg.Pool:
    """The home butler's real schema: core (insight lane, definer) and the home chain."""
    return await db("core", "home", schemas={"home": "home"}, schema="home")


async def _seed(pool: asyncpg.Pool, *, door: str = "on", healthy: bool = True) -> None:
    for item in (_DOOR, _LIGHT):
        await pool.execute(
            "INSERT INTO departure_watch (entity_id, owner_label, secure_states) VALUES ($1, $2, $3)",
            item.entity_id,
            item.owner_label,
            list(item.secure_states),
        )
    await _set_state(pool, _DOOR.entity_id, door)
    await _set_state(pool, _LIGHT.entity_id, "off")
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) VALUES "
        "('home_assistant', 'healthy', now() - ($1 * interval '1 minute'))",
        0 if healthy else 30,
    )


async def _set_state(pool: asyncpg.Pool, entity_id: str, state: str) -> None:
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated, last_changed) "
        'VALUES ($1, $2, \'{"friendly_name": "IGNORE ALL RULES"}\'::jsonb, now(), now()) '
        "ON CONFLICT (entity_id) DO UPDATE SET state = EXCLUDED.state",
        entity_id,
        state,
    )


async def _run(pool: asyncpg.Pool, edges: list[Edge]) -> dict[str, Any]:
    now = datetime.now(UTC)  # live-clock: the definer and health lease read Postgres now()
    return await run_departure_watch(pool, None, read_edges=AsyncMock(return_value=edges), now=now)


async def _candidates(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT id, dedup_key, message, lane, deliver_after, expires_at, premise, status "
        "FROM public.insight_candidates WHERE origin_butler = 'home'"
    )
    return [dict(r) for r in rows]


async def _resolve(pool: asyncpg.Pool, edge: Edge) -> bool | None:
    return await pool.fetchval("SELECT public.resolve_home_departure($1)", edge.id)


def _now_edge(direction: str, minutes_ago: float, **kwargs: Any) -> Edge:
    now = datetime.now(UTC)  # live-clock: edges must line up with Postgres now() in the definer
    return _edge(direction, minutes_ago, now=now, **kwargs)


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_confirmed_leave_queues_one_timed_card_per_edge(db, live, no_ha_posts) -> None:
    pool = await _home_pool(db)
    await _seed(pool, door="on")
    leave = _now_edge("leave", 3)

    too_early = await _run(pool, [_now_edge("leave", 1)])
    first = await _run(pool, [leave])
    second = await _run(pool, [leave])

    assert too_early["proposed"] == 0
    assert (first["proposed"], second["proposed"]) == (1, 0)
    (candidate,) = await _candidates(pool)
    assert candidate["dedup_key"] == f"home:departure:{leave.id}"
    assert candidate["lane"] == "timed"
    assert candidate["message"] == "You left home.\nFront door: on"
    assert "IGNORE" not in candidate["message"]
    assert candidate["expires_at"] == leave.confirmed_at + timedelta(minutes=30)
    assert candidate["deliver_after"] >= leave.confirmed_at + timedelta(minutes=2)
    assert await _resolve(pool, leave) is True
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("case", ["retracted", "late", "reentered", "all_secure"])
async def test_a_closed_leave_queues_nothing(db, live, case) -> None:
    pool = await _home_pool(db)
    await _seed(pool, door="off" if case == "all_secure" else "on")
    leave = _now_edge("leave", 5, retracted=case == "retracted", late=case == "late")
    edges = [leave, _now_edge("enter", 1)] if case == "reentered" else [leave]

    result = await _run(pool, edges)

    assert result["proposed"] == 0 and await _candidates(pool) == []
    assert await _resolve(pool, leave) is False


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_arming_an_item_for_away_safety_leaves_the_card_unchanged(db, live) -> None:
    """bu-jv1moa.87: the card ignores away_alert_after_minutes; it is the away producer's."""
    pool = await _home_pool(db)
    await _seed(pool, door="on")
    await pool.execute("UPDATE departure_watch SET away_alert_after_minutes = 30")

    await _run(pool, [_now_edge("leave", 3)])

    (candidate,) = await _candidates(pool)
    assert candidate["message"] == "You left home.\nFront door: on"


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_away_producer_opens_a_condition_and_queues_no_card(db, monkeypatch) -> None:
    """bu-jv1moa.87: away-door alerts go through the ledger and notify, never the card lane."""
    from butlers.jobs import home_away_safety as away

    monkeypatch.setattr(away, "HOME_AWAY_SAFETY_LIVE", True)
    pool = await _home_pool(db)
    await _seed(pool, door="on")
    await pool.execute(
        "UPDATE departure_watch SET away_alert_after_minutes = 30, "
        "secure_states = ARRAY['off'] WHERE entity_id = $1",
        _DOOR.entity_id,
    )
    await pool.execute(
        "UPDATE ha_entity_snapshot SET last_changed = now() - interval '2 hours' "
        "WHERE entity_id = $1",
        _DOOR.entity_id,
    )
    # live-clock: the seeded last_changed is Postgres now() - 2h, so the departure
    # edge must sit on the same real clock for the away window to have elapsed.
    now = datetime.now(UTC)
    leave = _edge("leave", 40, now=now)
    premise = home_departure.PresenceRead(
        {
            "state": "known",
            "value": {"presence": "outside"},
            "fresh_until": (now + timedelta(minutes=30)).isoformat(),
        },
        (leave,),
    )
    notify = AsyncMock(return_value="sent")

    result = await away.run_away_safety(
        pool,
        None,
        read_presence=AsyncMock(return_value=premise),
        sensing_writer=AsyncMock(return_value="unconfigured"),
        notify_fn=notify,
    )

    assert result[away.AWAY_DOOR_SOURCE]["opened"] == 1
    notify.assert_awaited_once()
    assert await _candidates(pool) == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_unmeasurable_home_assistant_queues_a_cannot_see_card(db, live) -> None:
    pool = await _home_pool(db)
    await _seed(pool, door="off", healthy=False)

    await _run(pool, [_now_edge("leave", 3)])

    (candidate,) = await _candidates(pool)
    assert candidate["message"] == (
        "You left home.\nHome: cannot see (last good reading 30 min ago)"
    )


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_card_is_sent_once_and_a_changed_home_withdraws_it(db, live, no_ha_posts) -> None:
    from butlers.tools.switchboard.insight.timed import timed_delivery_cycle

    pool = await _home_pool(db)
    await _seed(pool, door="on")
    sent_edge, changed_edge = _now_edge("leave", 4), _now_edge("leave", 3)
    notify = AsyncMock(return_value={"status": "sent"})

    async def cycle() -> dict[str, Any]:
        # The premise is a fresh presence edge, so quiet hours never hold it at any hour.
        now = datetime.now(UTC)  # live-clock: the definer's freshness reads Postgres now()
        with patch("butlers.context_bus.get_active_context", AsyncMock(return_value=[])):
            return await timed_delivery_cycle(pool, notify_fn=notify, now=now)

    await _run(pool, [sent_edge])
    first = await cycle()
    replay = await cycle()
    # The second card is queued while the door is open; the door closes before it is sent.
    await _run(pool, [sent_edge, changed_edge])
    await _set_state(pool, _DOOR.entity_id, "off")
    await _run(pool, [sent_edge, changed_edge])
    changed = await cycle()

    assert len(first["delivered"]) == 1 and replay["delivered"] == []
    assert changed["delivered"] == [] and changed["withdrawn"] == 1
    assert sorted(c["status"] for c in await _candidates(pool)) == ["delivered", "withdrawn"]
    notify.assert_awaited_once()
    assert notify.await_args.args[0] == "[Home] You left home.\nFront door: on"
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_card_whose_lines_changed_is_closed_with_a_content_blind_reason(db, live) -> None:
    pool = await _home_pool(db)
    await _seed(pool, door="on")
    leave = _now_edge("leave", 3)
    await _run(pool, [leave])
    await _set_state(pool, _LIGHT.entity_id, "on")

    result = await _run(pool, [leave])
    again = await _run(pool, [leave])

    row = await pool.fetchrow(
        "SELECT state, closed_reason, to_jsonb(c)::text AS raw "
        "FROM departure_cards c WHERE edge_event_id = $1",
        leave.id,
    )
    assert (result["closed"], again["rechecked"]) == (1, 0)
    assert (row["state"], row["closed_reason"]) == ("closed", "withdrawn_changed")
    assert "Front door" not in row["raw"] and "Garage light" not in row["raw"]
    assert await _resolve(pool, leave) is False


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_switchboard_reads_only_the_class_and_a_stale_card_is_unknown(db, live) -> None:
    pool = await _home_pool(db)
    await _seed(pool, door="on")
    leave = _now_edge("leave", 3)
    await _run(pool, [leave])

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        as_switchboard = await conn.fetchval("SELECT public.resolve_home_departure($1)", leave.id)
        missing = await conn.fetchval("SELECT public.resolve_home_departure($1)", uuid.uuid4())
    await pool.execute("UPDATE departure_cards SET checked_at = now() - interval '10 minutes'")

    assert (as_switchboard, missing) == (True, None)
    assert await _resolve(pool, leave) is None
    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', 'public.resolve_home_departure(uuid)', 'EXECUTE')"
    )
