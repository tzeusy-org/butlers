"""Home departure fixes: exact, owner-approved, cancelled honestly (bu-jv1moa.115).

REQ-home-deterministic-jobs-008 and -009. Unit tests pin the pure rules (declaration refusals, the
leave-edge-bound premise, key parsing, card words) and the fail-closed replay gate. The integration
tests park, cancel and gate real prepared actions against Postgres (CI-first where Docker is
unavailable).
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.jobs import home_departure_fixes as fixes
from butlers.jobs.home_departure import Edge
from butlers.jobs.home_departure_fixes import (
    CANCEL_ACTOR,
    STATUS_WORDS,
    Declaration,
    away_premise,
    cancel_fixes,
    fix_action_id,
    fix_key,
    fixable_entities,
    park_fixes,
    parse_fix_key,
    premise_of,
    refusal,
    replay_refusal,
)
from butlers.jobs.home_prepared_status import prepared_action_status

_NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
_LEAVE = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _live() -> datetime:
    return datetime.now(UTC)  # live-clock: expiry and receipts are compared with Postgres now()


def _edge(direction: str, minutes_ago: float, *, id=None, late=False, retracted=False) -> Edge:
    return Edge(
        id=id or uuid.uuid4(),
        direction=direction,  # type: ignore[arg-type]
        confirmed_at=_NOW - timedelta(minutes=minutes_ago),
        late=late,
        retracted=retracted,
    )


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_fix_key_round_trips_and_a_foreign_key_does_not_parse() -> None:
    key = fix_key(_LEAVE, "light.garage")

    assert key == f"home:departure-fix:{_LEAVE}:light.garage"
    assert parse_fix_key(key) == (_LEAVE, "light.garage")
    assert fix_action_id(key) == fix_action_id(key)
    assert fix_action_id(key) != fix_action_id(fix_key(_LEAVE, "light.porch"))
    assert parse_fix_key("home:departure:abc") is None
    assert parse_fix_key(None) is None
    assert parse_fix_key("home:departure-fix:not-a-uuid:light.a") is None
    assert parse_fix_key(f"home:departure-fix:{_LEAVE}:Light Garage") == (_LEAVE, "")


@pytest.mark.unit
def test_only_entity_lines_of_the_card_are_fixable() -> None:
    keys = ["light.garage=on", "binary_sensor.front_door=unreadable", "ha=unmeasurable"]

    assert fixable_entities(keys) == {"light.garage", "binary_sensor.front_door"}


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("domain", "service", "target", "data", "watched", "expected"),
    [
        ("light", "turn_off", "light.garage", {}, True, None),
        ("light", "turn_on", "light.garage", {"brightness": 100}, True, None),
        ("climate", "set_temperature", "climate.hall", {"temperature": 18}, True, None),
        ("lock", "lock", "lock.front", {}, True, None),
        ("light", "turn_off", "light.garage", {}, False, "not_watched"),
        ("light", "turn_off", "garage", {}, True, "not_explicit_entity"),
        ("light", "turn_off", "light.a, light.b", {}, True, "not_explicit_entity"),
        ("light", "turn_off", "switch.garage", {}, True, "not_explicit_entity"),
        ("light", "toggle", "light.garage", {}, True, "unclassified"),
        ("homeassistant", "update_entity", "light.garage", {}, True, "unclassified"),
        ("alarm_control_panel", "alarm_disarm", "alarm_control_panel.h", {}, True, "unclassified"),
        ("light", "turn_off", "light.garage", {"entity_id": "all"}, True, "data_not_allowed"),
        ("light", "turn_off", "light.garage", {"brightness": 1}, True, "data_not_allowed"),
        ("lock", "lock", "lock.front", {"code": "1234"}, True, "data_not_allowed"),
    ],
)
def test_a_declaration_is_stored_only_for_an_allowlisted_explicit_call(
    domain, service, target, data, watched, expected
) -> None:
    assert refusal(domain, service, target, data, watched=watched) == expected


@pytest.mark.unit
def test_the_class_of_a_fix_is_the_rfc_0028_effective_class() -> None:
    light = Declaration("light.garage", "light", "turn_off", "light.garage", {})
    lock = Declaration("lock.front", "lock", "lock", "lock.front", {})
    wild = Declaration("light.g", "light", "turn_on", "light.g", {"brightness": 999})

    assert fixes.risk_class(light) == "reversible"
    assert fixes.risk_class(lock) == "protected"
    assert fixes.risk_class(wild) == "consequential"
    assert "protected" in fixes.fix_hint(lock)
    assert fixes.fix_hint(None) == ""


# ---------------------------------------------------------------------------
# The premise is bound to the originating leave edge
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("edges", "expected"),
    [
        ([_edge("leave", 10, id=_LEAVE)], "holds"),
        ([_edge("enter", 2), _edge("leave", 10, id=_LEAVE)], "owner_returned"),
        # leave, return, leave again: the old leave is no longer the latest edge.
        (
            [_edge("leave", 1), _edge("enter", 5), _edge("leave", 10, id=_LEAVE)],
            "owner_returned",
        ),
        # a newer confirmed leave whose enter was never seen still supersedes the origin
        ([_edge("leave", 1), _edge("leave", 10, id=_LEAVE)], "owner_returned"),
        ([_edge("leave", 10, id=_LEAVE, retracted=True)], "premise_retracted"),
        ([_edge("leave", 10, id=_LEAVE, late=True)], "premise_retracted"),
        # a retracted or late enter does not end the absence
        (
            [_edge("enter", 2, retracted=True), _edge("enter", 3, late=True)]
            + [_edge("leave", 10, id=_LEAVE)],
            "holds",
        ),
        ([_edge("enter", 2)], "unknown"),
        ([], "unknown"),
    ],
    ids=[
        "still-away",
        "returned",
        "leave-return-leave",
        "newer-leave-only",
        "retracted",
        "late",
        "retracted-or-late-enter",
        "origin-not-in-view",
        "no-edges",
    ],
)
def test_the_premise_holds_only_while_the_originating_leave_is_the_latest_edge(
    edges, expected
) -> None:
    assert premise_of(edges, _LEAVE) == expected


@pytest.mark.unit
async def test_an_unreadable_presence_timeline_is_unknown_never_holds() -> None:
    pool = MagicMock()

    async def raises(*_a, **_k):
        raise OSError("down")

    one = AsyncMock(return_value=uuid.uuid4())
    none = AsyncMock(return_value=None)
    assert (
        await away_premise(pool, _LEAVE, now=_NOW, read_state=none, read_subject=one) == "unknown"
    )
    assert (
        await away_premise(pool, _LEAVE, now=_NOW, read_state=raises, read_subject=one) == "unknown"
    )
    assert (
        await away_premise(pool, _LEAVE, now=_NOW, read_state=none, read_subject=none) == "unknown"
    )
    state = SimpleNamespace(edges=(_edge("leave", 10, id=_LEAVE),))
    got = await away_premise(
        pool, _LEAVE, now=_NOW, read_state=AsyncMock(return_value=state), read_subject=one
    )
    assert got == "holds"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "premise", "expected"),
    [
        (None, "holds", None),
        ("travel:connection-risk:x", "owner_returned", None),
        (f"home:departure-fix:{_LEAVE}:light.garage", "holds", None),
        (f"home:departure-fix:{_LEAVE}:light.garage", "owner_returned", "premise_owner_returned"),
        (
            f"home:departure-fix:{_LEAVE}:light.garage",
            "premise_retracted",
            "premise_premise_retracted",
        ),
        (f"home:departure-fix:{_LEAVE}:light.garage", "unknown", "premise_unknown"),
        ("home:departure-fix:garbage", "holds", "fix_key_unreadable"),
        (f"home:departure-fix:{_LEAVE}:", "holds", "fix_key_unreadable"),
    ],
)
async def test_the_replay_gate_only_ever_refuses_and_unknown_fails_closed(
    monkeypatch, key, premise, expected
) -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=key)
    monkeypatch.setattr(fixes, "away_premise", AsyncMock(return_value=premise))

    assert await replay_refusal(pool, uuid.uuid4(), now=_NOW) == expected


@pytest.mark.unit
def test_no_card_word_claims_an_undone_effect() -> None:
    for word in STATUS_WORDS.values():
        for banned in ("undone", "reverted", "restored", "rolled back", "secure"):
            assert banned not in word.lower()
    assert set(STATUS_WORDS) == {
        "awaiting_approval",
        "approved_not_run",
        "succeeded",
        "unverified",
        "failed",
        "rejected",
        "cancelled",
        "expired",
        "abandoned",
        "unknown",
    }


@pytest.mark.unit
def test_the_job_module_never_calls_home_assistant() -> None:
    import ast

    tree = ast.parse(open(fixes.__file__, encoding="utf-8").read())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert "httpx" not in imported
    assert not {"post", "_call_service", "ha_call_service", "request"} & called


@pytest.mark.unit
async def test_the_dark_departure_job_cancels_and_parks_nothing(monkeypatch) -> None:
    from butlers.jobs import home_departure

    cancel = AsyncMock()
    monkeypatch.setattr(fixes, "cancel_fixes", cancel)
    monkeypatch.setattr(home_departure, "DEPARTURE_CARD_LIVE", True)
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    assert fixes.DEPARTURE_FIX_LIVE is False
    assert await home_departure.run_departure_watch(pool, None, now=_NOW) == {"status": "no_watch"}
    cancel.assert_not_awaited()


# ---------------------------------------------------------------------------
# Real Postgres
# ---------------------------------------------------------------------------


@pytest.fixture
def no_ha_posts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any HTTP POST fails the test: parking and cancelling never actuate."""
    posts: list[str] = []

    async def _post(self, url, *args, **kwargs):  # noqa: ANN001
        posts.append(str(url))
        raise AssertionError(f"unexpected HTTP POST to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return posts


async def _pool(db) -> asyncpg.Pool:
    return await db(
        "core", "home", "approvals", schemas={"home": "home", "approvals": "home"}, schema="home"
    )


async def _watch(pool, entity: str = "light.garage", label: str = "Garage light") -> None:
    await pool.execute(
        "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
        "VALUES ($1, $2, ARRAY['off']) ON CONFLICT DO NOTHING",
        entity,
        label,
    )


async def _declare(pool, entity: str = "light.garage", service: str = "turn_off") -> None:
    await pool.execute(
        "INSERT INTO departure_fixes (entity_id, domain, service, target_entity_id, data) "
        "VALUES ($1, 'light', $2, $1, '{}'::jsonb)",
        entity,
        service,
    )


async def _snapshot(pool, entity: str, state: str) -> None:
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated, last_changed) "
        "VALUES ($1, $2, '{}'::jsonb, now(), now()) ON CONFLICT (entity_id) DO UPDATE "
        "SET state = EXCLUDED.state",
        entity,
        state,
    )
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) "
        "VALUES ('home_assistant', 'healthy', now()) ON CONFLICT (source) DO UPDATE "
        "SET last_success_at = now(), status = 'healthy'"
    )


def _reader(*edges: Edge):
    async def read_state(now, *, subject, wanted=()):  # noqa: ANN001
        return SimpleNamespace(slot=None, edges=tuple(edges))

    return read_state


_SUBJECT = AsyncMock(return_value=uuid.uuid4())


async def _row(pool, entity: str = "light.garage", leave: uuid.UUID = _LEAVE) -> Any:
    return await pool.fetchrow(
        "SELECT * FROM pending_actions WHERE deduplication_key = $1", fix_key(leave, entity)
    )


async def _park(pool, listed=("light.garage",), labels=None, leave: uuid.UUID = _LEAVE) -> int:
    return await park_fixes(
        pool, leave, listed, labels or {"light.garage": "Garage light"}, now=_live()
    )


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_declared_listed_item_parks_exactly_one_pending_prepared_action(
    db, no_ha_posts
) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)

    assert await _park(pool) == 1
    assert await _park(pool) == 0

    rows = await pool.fetch("SELECT * FROM pending_actions WHERE origin = 'prepared'")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "pending"
    assert row["tool_name"] == "ha_call_service"
    assert row["id"] == fix_action_id(fix_key(_LEAVE, "light.garage"))
    args = row["tool_args"] if isinstance(row["tool_args"], dict) else json.loads(row["tool_args"])
    assert args == {
        "domain": "light",
        "service": "turn_off",
        "target": {"entity_id": "light.garage"},
        "data": {},
    }
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_undeclared_or_unlisted_item_parks_nothing(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _watch(pool, "switch.pump", "Pump")
    await _declare(pool)

    assert await _park(pool, listed=("switch.pump",)) == 0
    assert await _park(pool, listed=()) == 0
    assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 0


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_each_item_is_its_own_action_never_a_secure_all(db) -> None:
    pool = await _pool(db)
    for entity in ("light.garage", "light.porch"):
        await _watch(pool, entity, entity)
        await _declare(pool, entity)

    assert await _park(pool, listed=("light.garage", "light.porch"), labels={}) == 2
    keys = {
        r["deduplication_key"]
        for r in await pool.fetch("SELECT deduplication_key FROM pending_actions")
    }
    assert keys == {fix_key(_LEAVE, "light.garage"), fix_key(_LEAVE, "light.porch")}


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("edges", "reason"),
    [
        ([_edge("enter", 2), _edge("leave", 10, id=_LEAVE)], "owner_returned"),
        ([_edge("leave", 10, id=_LEAVE, retracted=True)], "premise_retracted"),
    ],
)
async def test_return_or_retraction_rejects_a_pending_fix_as_cancelled(
    db, no_ha_posts, edges, reason
) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _snapshot(pool, "light.garage", "on")
    await _park(pool)

    counts = await cancel_fixes(
        pool, now=_live(), read_state=_reader(*edges), read_subject=_SUBJECT
    )

    row = await _row(pool)
    assert counts == {"rejected": 1, "held": 0}
    assert row["status"] == "rejected"
    assert row["decided_by"] == CANCEL_ACTOR
    assert await prepared_action_status(pool, row["id"]) == "cancelled"
    event = await pool.fetchrow(
        "SELECT actor, reason FROM approval_events WHERE action_id = $1 "
        "AND actor = $2 ORDER BY occurred_at DESC LIMIT 1",
        row["id"],
        CANCEL_ACTOR,
    )
    assert event is not None and event["reason"] == reason
    assert no_ha_posts == []
    assert await pool.fetchval("SELECT count(*) FROM ha_command_log") == 0


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_secure_item_or_a_removed_fix_withdraws_the_pending_fix(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _watch(pool, "light.porch", "Porch")
    await _declare(pool)
    await _declare(pool, "light.porch")
    await _snapshot(pool, "light.garage", "off")
    await _snapshot(pool, "light.porch", "on")
    await _park(pool, listed=("light.garage", "light.porch"), labels={})
    await pool.execute("DELETE FROM departure_fixes WHERE entity_id = 'light.porch'")
    still_away = _reader(_edge("leave", 10, id=_LEAVE))

    await cancel_fixes(pool, now=_live(), read_state=still_away, read_subject=_SUBJECT)

    reasons = {
        r["deduplication_key"].rsplit(":", 1)[1]: r["reason"]
        for r in await pool.fetch(
            "SELECT p.deduplication_key, e.reason FROM pending_actions p "
            "JOIN approval_events e ON e.action_id = p.id AND e.actor = $1",
            CANCEL_ACTOR,
        )
    }
    assert reasons == {"light.garage": "already_secure", "light.porch": "watch_removed"}


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_presence_leaves_a_pending_fix_pending(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _snapshot(pool, "light.garage", "on")
    await _park(pool)

    counts = await cancel_fixes(pool, now=_live(), read_state=_reader(), read_subject=_SUBJECT)

    assert counts == {"rejected": 0, "held": 1}
    assert (await _row(pool))["status"] == "pending"


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_approved_and_settled_fixes_are_never_touched_by_cancellation(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _snapshot(pool, "light.garage", "off")
    await _park(pool)
    await pool.execute("UPDATE pending_actions SET status = 'approved', decided_by = 'human:owner'")

    await cancel_fixes(
        pool,
        now=_live(),
        read_state=_reader(_edge("enter", 2), _edge("leave", 10, id=_LEAVE)),
        read_subject=_SUBJECT,
    )

    row = await _row(pool)
    assert (row["status"], row["decided_by"]) == ("approved", "human:owner")
    assert await prepared_action_status(pool, row["id"]) == "approved_not_run"


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_replay_gate_refuses_an_old_approval_after_leave_return_leave(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _park(pool)
    row = await _row(pool)
    other = uuid.uuid4()
    now = _live()
    away = _reader(_edge("leave", 10, id=_LEAVE))
    back_and_gone = _reader(
        _edge("leave", 1, id=other), _edge("enter", 5), _edge("leave", 10, id=_LEAVE)
    )

    assert (
        await replay_refusal(pool, row["id"], now=now, read_state=away, read_subject=_SUBJECT)
        is None
    )
    got = await replay_refusal(
        pool, row["id"], now=now, read_state=back_and_gone, read_subject=_SUBJECT
    )
    assert got == "premise_owner_returned"


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_arrival_lines_follow_the_resolver_status_without_undone_wording(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _park(pool)
    row = await _row(pool)

    (waiting,) = await fixes.fix_lines(pool, _LEAVE)
    await pool.execute("UPDATE pending_actions SET status = 'approved', decided_by = 'human:owner'")
    (approved,) = await fixes.fix_lines(pool, _LEAVE)

    assert waiting == (
        "fix:light.garage=awaiting_approval",
        "Garage light: fix waiting for your approval",
    )
    assert approved[1] == "Garage light: fix approved, not run"
    assert await fixes.fix_lines(pool, uuid.uuid4()) == ()
    assert row is not None


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_departure_job_parks_the_fix_when_it_queues_the_card_and_only_then(
    db, monkeypatch, no_ha_posts
) -> None:
    from butlers.jobs import home_departure
    from butlers.tools.switchboard.insight import timed

    monkeypatch.setattr(home_departure, "DEPARTURE_CARD_LIVE", True)
    monkeypatch.setattr(fixes, "DEPARTURE_FIX_LIVE", True)
    monkeypatch.setattr(timed, "TIMED_INSIGHTS_LIVE", True)
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _snapshot(pool, "light.garage", "on")
    now = _live()
    leave = Edge(_LEAVE, "leave", now - timedelta(minutes=5), False, False)

    async def run() -> dict[str, Any]:
        return await home_departure.run_departure_watch(
            pool, None, read_edges=AsyncMock(return_value=[leave]), now=_live()
        )

    assert (await run())["proposed"] == 1
    assert (await run())["proposed"] == 0

    card = await pool.fetchval(
        "SELECT message FROM public.insight_candidates WHERE category = 'departure'"
    )
    assert "Garage light" in card and "fix is waiting for your approval" in card
    rows = await pool.fetch("SELECT status, origin FROM pending_actions")
    assert [(r["status"], r["origin"]) for r in rows] == [("pending", "prepared")]
    assert no_ha_posts == []
    assert await pool.fetchval("SELECT count(*) FROM ha_command_log") == 0


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_editing_a_declaration_after_parking_never_changes_the_parked_call(db) -> None:
    pool = await _pool(db)
    await _watch(pool)
    await _declare(pool)
    await _park(pool)
    before = (await _row(pool))["tool_args"]

    await pool.execute(
        "UPDATE departure_fixes SET service = 'turn_on', "
        "target_entity_id = 'light.porch', data = '{\"brightness\": 5}'::jsonb"
    )

    assert await _park(pool) == 0
    assert (await _row(pool))["tool_args"] == before
    assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 1
