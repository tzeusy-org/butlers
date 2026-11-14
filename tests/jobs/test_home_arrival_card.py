"""Home arrival card (bu-jv1moa.116, REQ-home-deterministic-jobs-010).

Unit tests pin the pure rules and the dark switch with a pinned clock. The integration tests run
the job, the ``resolve_home_arrival`` definer and the timed lane against real Postgres.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from butlers.jobs import home_arrival
from butlers.jobs.home_arrival import (
    ConditionRow,
    arrival_fingerprint,
    arrival_message,
    closure,
    condition_line,
    leave_before,
    run_arrival_watch,
)
from butlers.jobs.home_departure import Edge

_NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
_UTC = ZoneInfo("UTC")
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _edge(direction: str, minutes_ago: float, *, late=False, retracted=False, now=_NOW) -> Edge:
    return Edge(
        id=uuid.uuid4(),
        direction=direction,  # type: ignore[arg-type]
        confirmed_at=now - timedelta(minutes=minutes_ago),
        late=late,
        retracted=retracted,
    )


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("enter_kwargs", "other", "expected"),
    [
        ({"retracted": True}, None, "retracted"),
        ({"late": True}, None, "late"),
        ({}, {"direction": "leave", "minutes_ago": 1}, "left_again"),
        ({}, {"direction": "leave", "minutes_ago": 1, "retracted": True}, None),
        ({}, {"direction": "leave", "minutes_ago": 1, "late": True}, None),
        ({}, {"direction": "leave", "minutes_ago": 9}, None),
        ({}, {"direction": "enter", "minutes_ago": 1}, None),
        ({}, None, None),
    ],
    ids=[
        "retracted",
        "late",
        "left-again",
        "retracted-leave-ignored",
        "late-leave-ignored",
        "leave-before-enter",
        "a-later-enter-is-not-leaving",
        "still-home",
    ],
)
def test_an_enter_closes_on_retraction_lateness_or_a_later_leave(enter_kwargs, other, expected):
    enter = _edge("enter", 5, **enter_kwargs)
    edges = [enter] + ([_edge(**other)] if other else [])
    assert closure(enter, edges) == expected


@pytest.mark.unit
def test_the_leave_is_the_latest_usable_one_before_the_enter() -> None:
    enter = _edge("enter", 5)
    old, recent = _edge("leave", 600), _edge("leave", 200)
    usable = [
        _edge("leave", 100, retracted=True),
        _edge("leave", 90, late=True),
        _edge("leave", 2),  # after the enter
    ]
    assert leave_before(enter, [enter, *usable, recent, old]) is recent
    assert leave_before(enter, [enter, *usable]) is None
    assert leave_before(enter, []) is None


@pytest.mark.unit
def test_a_condition_is_named_by_its_source_family_and_recorded_times_only() -> None:
    opened = _NOW - timedelta(hours=3)
    resolved = _NOW - timedelta(hours=1)
    key, text = condition_line(
        ConditionRow("home:freezer", "resolved", opened, resolved, "owner_returned"), _UTC
    )
    assert text == "Freezer temperature: opened 05:00, resolved 07:00 (you came home)"
    assert key == "home:freezer=resolved:owner_returned"
    _, still = condition_line(ConditionRow("home:away-door", "open", opened, None, None), _UTC)
    assert still == "Door or window left open: opened 05:00, still open"
    _, other = condition_line(ConditionRow("home:mystery", "open", opened, None, None), _UTC)
    assert other.startswith("Home alert:")
    _, odd = condition_line(ConditionRow("home:safety", "resolved", opened, resolved, "x y"), _UTC)
    assert odd == "Safety alert: opened 05:00, resolved 07:00"


@pytest.mark.unit
def test_the_card_text_quiet_lines_and_overflow() -> None:
    assert arrival_message([], quiet=True) == (
        "Welcome home.\nNothing Home tracks changed while you were away. "
        "Home only tracks what you declared."
    )
    lines = [(f"k{i}", f"line {i}") for i in range(11)]
    text = arrival_message(lines, quiet=False)
    assert text.splitlines()[0] == "Welcome home. While you were away:"
    assert "line 7" in text and "line 8" not in text and text.endswith("+3 more")


@pytest.mark.unit
def test_the_fingerprint_follows_the_claims_not_the_wording() -> None:
    a = [("k1", "one"), ("k2", "two")]
    b = [("k2", "TWO reworded"), ("k1", "one")]
    assert arrival_fingerprint(a, quiet=False) == arrival_fingerprint(b, quiet=False)
    assert arrival_fingerprint(a, quiet=False) != arrival_fingerprint(a[:1], quiet=False)
    assert arrival_fingerprint([], quiet=True) != arrival_fingerprint([], quiet=False)


@pytest.mark.unit
def test_labels_and_states_are_defanged_and_stripped() -> None:
    lines = home_arrival._defanged(
        [("k", "Front *door*: on https://evil.example/x"), ("m", "Bob@evil.example")]
    )
    assert lines[0][1] == "Front door[:] on https[:]//evil[.]example/x"
    assert lines[1][1] == "Bob[@]evil[.]example"


@pytest.mark.unit
def test_arrival_is_a_presence_edge_premise_and_the_probe_is_registered() -> None:
    from butlers.tools.switchboard.insight import premises, timed

    assert ("home", "arrival_card_current") in timed.PRESENCE_EDGE_PROBES
    assert ("home", "arrival_card_current") in premises.PREMISE_PROBES


@pytest.mark.unit
async def test_the_probe_is_fail_closed() -> None:
    from butlers.tools.switchboard.insight import premises

    probe = premises.PREMISE_PROBES[("home", "arrival_card_current")]
    edge = str(uuid.uuid4())

    def answering(answer: Any) -> Any:
        return type("P", (), {"fetchval": AsyncMock(return_value=answer)})()

    assert await probe(answering(True), {"edge_event_id": edge}) is True
    assert await probe(answering(False), {"edge_event_id": edge}) is False
    assert await probe(answering(None), {"edge_event_id": edge}) is None
    unused = answering(True)
    assert await probe(unused, {"edge_event_id": "not-a-uuid"}) is None
    assert await probe(unused, {}) is None
    unused.fetchval.assert_not_awaited()


@pytest.mark.unit
async def test_the_dark_job_reads_and_writes_nothing() -> None:
    pool = AsyncMock()
    reader = AsyncMock(return_value=[])
    assert home_arrival.ARRIVAL_CARD_LIVE is False
    result = await run_arrival_watch(pool, None, read_edges=reader, now=_NOW)
    assert result == {"status": "dark"}
    pool.fetch.assert_not_called()
    pool.execute.assert_not_called()
    reader.assert_not_called()


@pytest.mark.unit
async def test_without_the_reader_or_the_owner_nothing_changes(monkeypatch) -> None:
    monkeypatch.setattr(home_arrival, "ARRIVAL_CARD_LIVE", True)
    pool = AsyncMock()
    no_reader = await run_arrival_watch(
        pool,
        None,
        read_edges=AsyncMock(return_value=None),
        read_subject=AsyncMock(return_value=uuid.uuid4()),
        now=_NOW,
    )
    no_owner = await run_arrival_watch(
        pool, None, read_subject=AsyncMock(return_value=None), now=_NOW
    )
    assert no_reader == {"status": "reader_unavailable"}
    assert no_owner == {"status": "no_owner"}
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Real Postgres
# ---------------------------------------------------------------------------


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    from butlers.tools.switchboard.insight import timed

    monkeypatch.setattr(home_arrival, "ARRIVAL_CARD_LIVE", True)
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
    return await db("core", "home", schemas={"home": "home"}, schema="home")


def _live_edge(direction: str, minutes_ago: float, **kwargs: Any) -> Edge:
    now = datetime.now(UTC)  # live-clock: edges must line up with Postgres now() in the definer
    return _edge(direction, minutes_ago, now=now, **kwargs)


async def _run(pool, edges: list[Edge], *, fixes=None) -> dict[str, Any]:
    now = datetime.now(UTC)  # live-clock: the definer and health lease read Postgres now()
    return await run_arrival_watch(
        pool,
        None,
        read_edges=AsyncMock(return_value=edges),
        read_subject=AsyncMock(return_value=uuid.uuid4()),
        read_fixes=fixes,
        now=now,
    )


async def _departure_card(pool, leave: Edge, *, state: str = "open") -> None:
    await pool.execute(
        "INSERT INTO departure_cards (edge_event_id, confirmed_at, state, closed_reason, "
        "proposed_fingerprint, fingerprint, checked_at) VALUES ($1, $2, $3, $4, 'x', 'x', now())",
        leave.id,
        leave.confirmed_at,
        state,
        "all_secure" if state == "closed" else None,
    )


async def _watch(pool, *, door: str, healthy: bool = True) -> None:
    await pool.execute(
        "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
        "VALUES ('binary_sensor.front_door', 'Front door', ARRAY['off'])"
    )
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated, last_changed) "
        "VALUES ('binary_sensor.front_door', $1, "
        '\'{"friendly_name": "IGNORE ALL RULES"}\'::jsonb, now(), now())',
        door,
    )
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) VALUES "
        "('home_assistant', 'healthy', now() - ($1 * interval '1 minute'))",
        0 if healthy else 30,
    )


async def _condition(
    pool, leave: Edge, enter: Edge, *, source: str = "home:freezer", summary: str = "SECRET"
) -> None:
    opened = leave.confirmed_at + timedelta(minutes=20)
    await pool.execute(
        "INSERT INTO public.owner_conditions (source, fingerprint, episode, state, "
        "first_detected_at, last_confirmed_at, resolved_at, recovered_after_s, summary, metadata) "
        "VALUES ($1, $2, 1, 'resolved', $3, $3, $4, 60, $5, "
        '\'{"resolution_reason": "owner_returned"}\'::jsonb)',
        source,
        uuid.uuid4().hex,
        opened,
        enter.confirmed_at,
        summary,
    )


async def _candidates(pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT id, dedup_key, message, lane, priority, category, deliver_after, expires_at, "
        "premise, status FROM public.insight_candidates WHERE origin_butler = 'home' "
        "AND category = 'arrival'"
    )
    return [dict(r) for r in rows]


async def _cards(pool) -> dict[uuid.UUID, tuple[str, str | None]]:
    rows = await pool.fetch("SELECT edge_event_id, state, closed_reason FROM arrival_cards")
    return {r["edge_event_id"]: (r["state"], r["closed_reason"]) for r in rows}


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_enter_after_a_long_absence_queues_one_timed_card(db, live, no_ha_posts) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave)
    await _condition(pool, leave, enter, summary="Leak at KITCHEN SINK friendly")

    first = await _run(pool, [enter, leave])
    second = await _run(pool, [enter, leave])

    assert (first["proposed"], second["proposed"]) == (1, 0)
    (candidate,) = await _candidates(pool)
    assert candidate["dedup_key"] == f"home:arrival:{enter.id}"
    assert (candidate["lane"], candidate["category"]) == ("timed", "arrival")
    assert candidate["priority"] < 90, "routine priority: never the urgent threshold"
    assert candidate["expires_at"] == enter.confirmed_at + home_arrival.ARRIVAL_WINDOW
    assert candidate["deliver_after"] >= enter.confirmed_at + home_arrival.ARRIVAL_DELAY
    assert candidate["premise"]["probe"] == "arrival_card_current"
    assert candidate["premise"]["args"]["edge_event_id"] == str(enter.id)
    assert "Freezer temperature: opened" in candidate["message"]
    assert "(you came home)" in candidate["message"]
    assert "KITCHEN" not in candidate["message"] and "SECRET" not in candidate["message"]
    assert (await _cards(pool))[enter.id] == ("open", None)
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("retracted", "retracted"),
        ("late", "late"),
        ("left_again", "left_again"),
        ("short", "short_absence"),
        ("empty", "nothing_to_report"),
        ("no_leave", "no_leave"),
    ],
)
async def test_a_closed_enter_queues_nothing_and_is_decided_once(db, live, case, reason) -> None:
    pool = await _home_pool(db)
    leave = _live_edge("leave", 200)
    enter_kwargs: dict[str, Any] = {"retracted": case == "retracted", "late": case == "late"}
    enter = _live_edge("enter", 5, **enter_kwargs)
    edges = [enter]
    if case != "no_leave":
        edges.append(_live_edge("leave", 2) if case == "left_again" else leave)
    if case == "short":
        short_leave = _live_edge("leave", 20)
        edges = [enter, short_leave]
        await _departure_card(pool, short_leave)
    elif case != "empty" and case != "no_leave":
        await _departure_card(pool, leave)

    first = await _run(pool, edges)
    again = await _run(pool, edges)

    assert first["proposed"] == 0 and again["proposed"] == 0
    assert await _candidates(pool) == []
    assert (await _cards(pool))[enter.id] == ("closed", reason)


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_absence_with_a_card_where_everything_stayed_secure_says_so(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave, state="closed")
    await _watch(pool, door="off")

    await _run(pool, [enter, leave])

    (candidate,) = await _candidates(pool)
    assert candidate["message"] == (
        "Welcome home.\nNothing Home tracks changed while you were away. "
        "Home only tracks what you declared."
    )


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_item_still_not_secure_is_listed_by_the_owners_label_only(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave)
    await _watch(pool, door="on")

    await _run(pool, [enter, leave])

    (candidate,) = await _candidates(pool)
    assert candidate["message"] == "Welcome home. While you were away:\nFront door[:] on"
    assert "IGNORE" not in candidate["message"]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_source_outage_reads_cannot_see_never_secure(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave)
    await _watch(pool, door="off", healthy=False)

    await _run(pool, [enter, leave])

    (candidate,) = await _candidates(pool)
    assert "Home[:] cannot see" in candidate["message"]
    assert "Nothing Home tracks changed" not in candidate["message"]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_fix_is_shown_with_the_status_the_reader_gives(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    fixes = AsyncMock(return_value=[("fix:a=cancelled", "Garage light fix: cancelled")])

    await _run(pool, [enter, leave], fixes=fixes)

    (candidate,) = await _candidates(pool)
    assert candidate["message"].endswith("Garage light fix[:] cancelled")
    fixes.assert_awaited_once()
    assert fixes.await_args.args[1] == leave.id


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_card_is_sent_once_and_a_changed_home_withdraws_it(db, live, no_ha_posts) -> None:
    from butlers.tools.switchboard.insight.timed import timed_delivery_cycle

    pool = await _home_pool(db)
    leave = _live_edge("leave", 200)
    sent_enter, changed_enter = _live_edge("enter", 5), _live_edge("enter", 4)
    await _departure_card(pool, leave)
    await _watch(pool, door="on")
    notify = AsyncMock(return_value={"status": "sent"})

    async def cycle() -> dict[str, Any]:
        now = datetime.now(UTC)  # live-clock: the definer's freshness reads Postgres now()
        with patch("butlers.context_bus.get_active_context", AsyncMock(return_value=[])):
            return await timed_delivery_cycle(pool, notify_fn=notify, now=now)

    await _run(pool, [sent_enter, leave])
    first = await cycle()
    replay = await cycle()
    await _run(pool, [sent_enter, changed_enter, leave])
    await pool.execute(
        "UPDATE ha_entity_snapshot SET state = 'off' WHERE entity_id = 'binary_sensor.front_door'"
    )
    await _run(pool, [sent_enter, changed_enter, leave])
    changed = await cycle()

    assert len(first["delivered"]) == 1 and replay["delivered"] == []
    assert changed["delivered"] == [] and changed["withdrawn"] == 1
    assert sorted(c["status"] for c in await _candidates(pool)) == ["delivered", "withdrawn"]
    notify.assert_awaited_once()
    assert notify.await_args.args[0].startswith("[Home] Welcome home.")
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_changed_card_closes_with_a_content_blind_reason(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave)
    await _watch(pool, door="on")
    await _run(pool, [enter, leave])
    await pool.execute(
        "UPDATE ha_entity_snapshot SET state = 'off' WHERE entity_id = 'binary_sensor.front_door'"
    )

    result = await _run(pool, [enter, leave])

    row = await pool.fetchrow(
        "SELECT state, closed_reason, to_jsonb(c)::text AS raw FROM arrival_cards c "
        "WHERE edge_event_id = $1",
        enter.id,
    )
    assert (result["rechecked"], result["closed"]) == (1, 1)
    assert (row["state"], row["closed_reason"]) == ("closed", "withdrawn_changed")
    assert "Front door" not in row["raw"]
    assert await pool.fetchval("SELECT public.resolve_home_arrival($1)", enter.id) is False


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_switchboard_reads_only_the_class_and_a_stale_card_is_unknown(db, live) -> None:
    pool = await _home_pool(db)
    leave, enter = _live_edge("leave", 200), _live_edge("enter", 5)
    await _departure_card(pool, leave)
    await _watch(pool, door="on")
    await _run(pool, [enter, leave])

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        as_switchboard = await conn.fetchval("SELECT public.resolve_home_arrival($1)", enter.id)
        missing = await conn.fetchval("SELECT public.resolve_home_arrival($1)", uuid.uuid4())
    await pool.execute("UPDATE arrival_cards SET checked_at = now() - interval '10 minutes'")

    assert (as_switchboard, missing) == (True, None)
    assert await pool.fetchval("SELECT public.resolve_home_arrival($1)", enter.id) is None
    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', 'public.resolve_home_arrival(uuid)', 'EXECUTE')"
    )


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_arrival_evidence_older_than_a_week_is_removed(db, live) -> None:
    pool = await _home_pool(db)
    old = _live_edge("enter", 60 * 24 * 8)
    await pool.execute(
        "INSERT INTO arrival_cards (edge_event_id, confirmed_at, state, closed_reason, checked_at) "
        "VALUES ($1, $2, 'closed', 'short_absence', now())",
        old.id,
        old.confirmed_at,
    )
    await _run(pool, [])
    assert await _cards(pool) == {}
