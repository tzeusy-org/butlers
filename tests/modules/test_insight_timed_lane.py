"""Timed insight lane and the shared delivery claim (bu-jv1moa.8 PR-A).

RFC 0011 Amendment 8: a timed candidate is delivered between deliver_after and
expires_at or not at all; dnd and sleeping always hold it; quiet hours do not
hold a candidate whose premise is a confirmed presence edge under 15 minutes old.

Every case runs against a real core-migrated PostgreSQL clone (the claim is a
row-level compare-and-set, so a mock proves nothing). They run CI-first where
Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

_docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# 12:00 UTC is 20:00 Asia/Singapore: outside the seeded 23:00-08:00 quiet window.
_AWAKE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
# 18:00 UTC is 02:00 Asia/Singapore: inside it.
_QUIET = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
_EDGE = ("home", "departure_still_open")


@pytest.fixture
async def timed_pool(db_pool, monkeypatch):
    from butlers.tools.switchboard.insight import timed
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await create_insight_tables(db_pool)
    await db_pool.execute("TRUNCATE insight_settings")
    await db_pool.execute(
        """
        INSERT INTO public.approvals_policy (id, quiet_start_hour, quiet_end_hour, timezone)
        VALUES (1, 23, 8, 'Asia/Singapore')
        ON CONFLICT (id) DO UPDATE
            SET quiet_start_hour = 23, quiet_end_hour = 8, timezone = 'Asia/Singapore'
        """
    )
    monkeypatch.setattr(timed, "TIMED_INSIGHTS_LIVE", True)
    return db_pool


def _context(*signals: tuple[str, timedelta], now: datetime = _AWAKE) -> AsyncMock:
    from butlers.context_bus import ContextEntry

    return AsyncMock(
        return_value=[
            ContextEntry(
                signal_type=signal,
                value=None,
                set_by_butler="health",
                set_at=now - age,
                expires_at=now + timedelta(hours=24),
                confidence=1.0,
            )
            for signal, age in signals
        ]
    )


def _sent() -> AsyncMock:
    return AsyncMock(return_value={"status": "sent"})


async def _propose_timed(
    pool,
    *,
    now: datetime = _AWAKE,
    after: timedelta = timedelta(minutes=-1),
    ttl: timedelta = timedelta(hours=2),
    key: str = "home:departure:front-door:2026-08-20T11",
    premise: dict[str, Any] | None = None,
    priority: int = 80,
) -> str:
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    result = await propose_insight_candidate(
        pool,
        origin_butler="home",
        priority=priority,
        category="departure",
        dedup_key=key,
        message=f"Timed {key}",
        expires_at=now + ttl,
        lane="timed",
        deliver_after=now + after,
        premise=premise,
        now=now - timedelta(minutes=5),
    )
    assert result["status"] == "accepted", result
    return str(await pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1", key))


async def _propose_digest(pool, *, key: str = "health:refill:abc:2026-w34", now=_AWAKE) -> str:
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    result = await propose_insight_candidate(
        pool,
        origin_butler="health",
        priority=60,
        category="health",
        dedup_key=key,
        message=f"Digest {key}",
        expires_at=now + timedelta(days=3),
        now=now,
    )
    assert result["status"] == "accepted", result
    return str(await pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1", key))


async def _row(pool, candidate_id: str) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT status, claim_id, attempt_started_at, delivery_attempt_count, metadata
        FROM insight_candidates WHERE id = $1::uuid
        """,
        candidate_id,
    )
    out = dict(row)
    if isinstance(out["metadata"], str):
        out["metadata"] = json.loads(out["metadata"])
    out["metadata"] = out["metadata"] or {}
    return out


def _edge_premise(confirmed_at: datetime) -> dict[str, Any]:
    from butlers.core.insight_premise import probe_premise

    return probe_premise(*_EDGE, {"confirmed_at": confirmed_at.isoformat()})


async def _run_timed(pool, notify, *, now=_AWAKE, context=None):
    from butlers.tools.switchboard.insight.timed import timed_delivery_cycle

    with patch("butlers.context_bus.get_active_context", context or _context(now=now)):
        return await timed_delivery_cycle(pool, notify_fn=notify, now=now)


# --- proposals --------------------------------------------------------------


async def test_flag_off_refuses_timed_proposals_and_skips_the_cycle(timed_pool, monkeypatch):
    from butlers.tools.switchboard.insight import timed
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    monkeypatch.setattr(timed, "TIMED_INSIGHTS_LIVE", False)
    refused = await propose_insight_candidate(
        timed_pool,
        origin_butler="home",
        priority=80,
        category="departure",
        dedup_key="home:departure:door:t1",
        message="m",
        expires_at=_AWAKE + timedelta(hours=1),
        lane="timed",
        deliver_after=_AWAKE,
        now=_AWAKE,
    )
    notify = _sent()
    result = await _run_timed(timed_pool, notify)
    assert refused["status"] == "error" and "timed lane" in refused["reason"]
    assert result == {**result, "skipped": True, "reason": "timed_lane_dark"}
    notify.assert_not_awaited()


@pytest.mark.parametrize(
    ("lane", "after", "reason"),
    [
        ("timed", None, "deliver_after"),
        ("digest", 0, "deliver_after"),
        ("timed", 120, "before expires_at"),
        ("bogus", None, "lane"),
    ],
    ids=["timed-without-window", "digest-with-window", "window-after-expiry", "unknown-lane"],
)
async def test_proposal_validates_the_timed_window(timed_pool, lane, after, reason):
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    result = await propose_insight_candidate(
        timed_pool,
        origin_butler="home",
        priority=80,
        category="departure",
        dedup_key="home:departure:door:t2",
        message="m",
        expires_at=_AWAKE + timedelta(minutes=60),
        lane=lane,
        deliver_after=None if after is None else _AWAKE + timedelta(minutes=after),
        now=_AWAKE,
    )
    assert result["status"] == "error" and reason in result["reason"]


async def test_second_timed_proposal_for_one_edge_is_a_no_op(timed_pool):
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    await _propose_timed(timed_pool, key="home:departure:door:edge-1")
    again = await propose_insight_candidate(
        timed_pool,
        origin_butler="home",
        priority=80,
        category="departure",
        dedup_key="home:departure:door:edge-1",
        message="again",
        expires_at=_AWAKE + timedelta(hours=1),
        lane="timed",
        deliver_after=_AWAKE,
        now=_AWAKE,
    )
    count = await timed_pool.fetchval("SELECT count(*) FROM insight_candidates")
    assert again["status"] == "duplicate" and count == 1


# --- window ----------------------------------------------------------------


async def test_timed_candidate_waits_for_its_window_then_sends_exactly_once(timed_pool):
    candidate = await _propose_timed(timed_pool, after=timedelta(minutes=10))
    notify = _sent()

    early = await _run_timed(timed_pool, notify)
    due = await _run_timed(timed_pool, notify, now=_AWAKE + timedelta(minutes=11))
    again = await _run_timed(timed_pool, notify, now=_AWAKE + timedelta(minutes=12))

    assert early["delivered"] == [] and again["delivered"] == []
    assert due["delivered"] == [candidate]
    assert notify.await_count == 1
    message, metadata = notify.await_args.args
    assert "Timed" in message and metadata["lane"] == "timed"
    assert (await _row(timed_pool, candidate))["status"] == "delivered"


async def test_timed_candidate_past_expiry_is_never_sent(timed_pool):
    candidate = await _propose_timed(timed_pool, ttl=timedelta(minutes=30))
    notify = _sent()
    result = await _run_timed(timed_pool, notify, now=_AWAKE + timedelta(minutes=31))
    assert result["expired"] == 1
    assert (await _row(timed_pool, candidate))["status"] == "expired"
    notify.assert_not_awaited()


async def test_digest_and_urgent_cycles_never_select_a_timed_candidate(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    timed_id = await _propose_timed(timed_pool, priority=95)
    notify = _sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        digest = await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
        urgent = await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE, urgent_only=True)
    assert digest["delivered"] == [] and urgent["delivered"] == []
    assert (await _row(timed_pool, timed_id))["status"] == "pending"
    notify.assert_not_awaited()


# --- holds -----------------------------------------------------------------


@pytest.mark.parametrize("signal", ["dnd", "sleeping"])
async def test_dnd_and_sleeping_always_hold_even_past_their_max_hold(timed_pool, signal):
    candidate = await _propose_timed(timed_pool, premise=_edge_premise(_AWAKE))
    notify = _sent()
    with patch.dict(
        "butlers.tools.switchboard.insight.premises.PREMISE_PROBES",
        {_EDGE: AsyncMock(return_value=True)},
    ):
        result = await _run_timed(
            timed_pool, notify, context=_context((signal, timedelta(hours=20)))
        )
    row = await _row(timed_pool, candidate)
    assert result["delivered"] == [] and row["status"] == "pending"
    assert row["metadata"]["blocked_by"] == "held_by"
    notify.assert_not_awaited()


async def test_capped_context_signal_holds_only_within_its_max_hold(timed_pool):
    await _propose_timed(timed_pool)
    notify = _sent()
    held = await _run_timed(
        timed_pool, notify, context=_context(("meeting", timedelta(minutes=30)))
    )
    lapsed = await _run_timed(timed_pool, notify, context=_context(("meeting", timedelta(hours=3))))
    assert held["delivered"] == [] and len(lapsed["delivered"]) == 1


@pytest.mark.parametrize(
    ("edge_age_minutes", "sent"),
    [(5, True), (14, True), (16, False)],
    ids=["fresh", "just-fresh", "stale"],
)
async def test_quiet_hours_yield_only_to_a_fresh_confirmed_presence_edge(
    timed_pool, edge_age_minutes, sent
):
    candidate = await _propose_timed(
        timed_pool,
        now=_QUIET,
        premise=_edge_premise(_QUIET - timedelta(minutes=edge_age_minutes)),
    )
    notify = _sent()
    with patch.dict(
        "butlers.tools.switchboard.insight.premises.PREMISE_PROBES",
        {_EDGE: AsyncMock(return_value=True)},
    ):
        result = await _run_timed(timed_pool, notify, now=_QUIET, context=_context(now=_QUIET))
    assert (result["delivered"] == [candidate]) is sent
    assert notify.await_count == int(sent)


async def test_quiet_hours_hold_a_timed_candidate_with_no_presence_premise(timed_pool):
    await _propose_timed(timed_pool, now=_QUIET)
    notify = _sent()
    result = await _run_timed(timed_pool, notify, now=_QUIET, context=_context(now=_QUIET))
    assert result["delivered"] == []
    notify.assert_not_awaited()


async def test_unknown_premise_holds_and_false_premise_withdraws(timed_pool):
    unknown = await _propose_timed(
        timed_pool, key="home:departure:door:unknown", premise=_edge_premise(_AWAKE)
    )
    notify = _sent()
    held = await _run_timed(timed_pool, notify)
    with patch.dict(
        "butlers.tools.switchboard.insight.premises.PREMISE_PROBES",
        {_EDGE: AsyncMock(return_value=False)},
    ):
        withdrawn = await _run_timed(timed_pool, notify)
    assert held["delivered"] == []
    assert held["held"] == {unknown: "premise_unknown"}
    assert withdrawn["withdrawn"] == 1
    assert (await _row(timed_pool, unknown))["status"] == "withdrawn"
    notify.assert_not_awaited()


# --- budget ----------------------------------------------------------------


async def test_timed_send_counts_against_the_daily_budget(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    digest_id = await _propose_digest(timed_pool)
    notify = _sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    timed_id = await _propose_timed(timed_pool)
    result = await _run_timed(timed_pool, notify)
    assert (await _row(timed_pool, digest_id))["status"] == "delivered"
    assert result["held"] == {timed_id: "budget"}
    assert (await _row(timed_pool, timed_id))["metadata"]["blocked_by"] == "budget"


async def test_digest_budget_is_reduced_by_timed_sends_today(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    await _propose_timed(timed_pool)
    notify = _sent()
    await _run_timed(timed_pool, notify)
    await _propose_digest(timed_pool)
    with patch("butlers.context_bus.get_active_context", _context()):
        digest = await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    assert digest["delivered"] == [] and digest["effective_budget"] == 0
    assert notify.await_count == 1


async def test_timed_lane_caps_at_six_sends_a_day(timed_pool):
    # 10 is the largest budget chk_insight_settings_custom_budget allows: the lane cap binds first.
    await timed_pool.execute("INSERT INTO insight_settings (id, custom_budget) VALUES (1, 10)")
    notify = _sent()
    ids = []
    for index in range(7):
        ids.append(await _propose_timed(timed_pool, key=f"home:departure:door:cap-{index}"))
    result = await _run_timed(timed_pool, notify)
    assert len(result["delivered"]) == 6 and notify.await_count == 6
    assert list(result["held"].values()) == ["budget"]


# --- the shared claim --------------------------------------------------------


def _slow_sent(delay: float = 0.2) -> AsyncMock:
    async def send(message: str, metadata: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(delay)
        return {"status": "sent"}

    return AsyncMock(side_effect=send)


async def test_overlapping_digest_cycles_send_a_candidate_once(timed_pool):
    """Regression: two digest cycles that both select one candidate send it once."""
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    candidate = await _propose_digest(timed_pool)
    notify = _slow_sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        first, second = await asyncio.gather(
            delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE),
            delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE),
        )
    assert notify.await_count == 1
    assert sorted([first["delivered"], second["delivered"]]) == [[], [candidate]]
    assert (await _row(timed_pool, candidate))["status"] == "delivered"


async def test_claim_is_all_or_none_and_a_held_row_is_contended(timed_pool):
    from butlers.tools.switchboard.insight.broker import claim_for_delivery

    first = await _propose_digest(timed_pool, key="health:refill:a:2026-w34")
    second = await _propose_digest(timed_pool, key="health:refill:b:2026-w34")
    held, _ = await claim_for_delivery(timed_pool, [first], now=_AWAKE)
    both, refusal = await claim_for_delivery(timed_pool, [first, second], now=_AWAKE)
    assert held is not None and both is None and refusal == "claim_contended"
    assert (await _row(timed_pool, first))["claim_id"] == held
    assert (await _row(timed_pool, second))["claim_id"] is None


async def test_concurrent_timed_urgent_and_digest_cycles_send_each_candidate_once(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle
    from butlers.tools.switchboard.insight.timed import timed_delivery_cycle

    await timed_pool.execute("INSERT INTO insight_settings (id, custom_budget) VALUES (1, 5)")
    timed_id = await _propose_timed(timed_pool)
    digest_id = await _propose_digest(timed_pool)
    notify = _slow_sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        await asyncio.gather(
            timed_delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE),
            timed_delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE),
            delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE),
            delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE, urgent_only=True),
        )
    messages = [call.args[0] for call in notify.await_args_list]
    assert sum("Timed" in m for m in messages) == 1
    assert sum("Digest" in m for m in messages) == 1
    assert (await _row(timed_pool, timed_id))["status"] == "delivered"
    assert (await _row(timed_pool, digest_id))["status"] == "delivered"


async def test_failed_timed_transport_releases_the_claim_for_a_retry(timed_pool):
    candidate = await _propose_timed(timed_pool)
    failing = AsyncMock(return_value={"status": "error", "error": "no chat id"})
    failed = await _run_timed(timed_pool, failing)
    row = await _row(timed_pool, candidate)
    retried = await _run_timed(timed_pool, _sent())
    assert failed["delivered"] == []
    assert row["status"] == "pending" and row["claim_id"] is None
    assert row["attempt_started_at"] is None and row["delivery_attempt_count"] == 1
    assert retried["delivered"] == [candidate]


async def test_raising_timed_transport_is_ambiguous_and_never_resent(timed_pool):
    candidate = await _propose_timed(timed_pool)
    raising = AsyncMock(side_effect=TimeoutError("read timeout after send"))
    await _run_timed(timed_pool, raising)
    notify = _sent()
    await _run_timed(timed_pool, notify, now=_AWAKE + timedelta(minutes=1))
    row = await _row(timed_pool, candidate)
    assert row["status"] == "expired" and row["metadata"]["delivery_ambiguous"] is True
    notify.assert_not_awaited()


@pytest.mark.parametrize(
    ("attempt_started", "status", "resent"),
    [(False, "delivered", True), (True, "expired", False)],
    ids=["never-reached-transport", "may-have-sent"],
)
async def test_stale_timed_claim(timed_pool, attempt_started, status, resent):
    candidate = await _propose_timed(timed_pool)
    claimed_at = _AWAKE - timedelta(minutes=30)
    await timed_pool.execute(
        """
        UPDATE insight_candidates
        SET claim_id = gen_random_uuid(), claimed_at = $2::timestamptz,
            attempt_started_at = CASE WHEN $3::boolean THEN $2::timestamptz ELSE NULL END
        WHERE id = $1::uuid
        """,
        candidate,
        claimed_at,
        attempt_started,
    )
    notify = _sent()
    result = await _run_timed(timed_pool, notify)
    row = await _row(timed_pool, candidate)
    assert row["status"] == status
    assert notify.await_count == int(resent)
    assert result["ambiguous"] == int(not resent)


async def test_stale_digest_claim_is_released_for_a_retry(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    candidate = await _propose_digest(timed_pool)
    await timed_pool.execute(
        """
        UPDATE insight_candidates
        SET claim_id = gen_random_uuid(), claimed_at = $2, attempt_started_at = $2
        WHERE id = $1::uuid
        """,
        candidate,
        _AWAKE - timedelta(hours=1),
    )
    notify = _sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        fresh = await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    assert fresh["delivered"] == [candidate] and notify.await_count == 1


def _retaking_sent(pool, candidate: str, other: list[Any]) -> AsyncMock:
    """Transport during which the claim is released and re-taken by another cycle."""

    async def send(message: str, metadata: dict[str, Any]) -> dict[str, Any]:
        other.append(
            await pool.fetchval(
                "UPDATE insight_candidates SET claim_id = gen_random_uuid() "
                "WHERE id = $1::uuid RETURNING claim_id",
                candidate,
            )
        )
        return {"status": "sent"}

    return AsyncMock(side_effect=send)


@pytest.mark.parametrize("lane", ["digest", "timed"])
async def test_stale_holder_never_settles_a_re_taken_claim(timed_pool, lane):
    """bu-jv1moa.8 review: only the current claim holder writes the disposition."""
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    other: list[Any] = []
    if lane == "digest":
        candidate = await _propose_digest(timed_pool)
        notify = _retaking_sent(timed_pool, candidate, other)
        with patch("butlers.context_bus.get_active_context", _context()):
            await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    else:
        candidate = await _propose_timed(timed_pool)
        notify = _retaking_sent(timed_pool, candidate, other)
        await _run_timed(timed_pool, notify)
    row = await _row(timed_pool, candidate)
    assert notify.await_count == 1
    assert row["status"] == "pending" and row["claim_id"] == other[0]


async def test_timed_claim_refuses_a_window_closed_by_the_send_instant(timed_pool):
    from butlers.tools.switchboard.insight.broker import claim_for_delivery

    candidate = await _propose_timed(timed_pool, ttl=timedelta(minutes=10))
    late = _AWAKE + timedelta(minutes=10)
    claim, refusal = await claim_for_delivery(timed_pool, [candidate], now=late, unexpired_at=late)
    assert claim is None and refusal == "claim_contended"
    assert (await _row(timed_pool, candidate))["claim_id"] is None


async def test_false_premise_never_withdraws_a_row_another_cycle_holds(timed_pool):
    from butlers.tools.switchboard.insight.broker import (
        delivery_cycle,
        propose_insight_candidate,
    )

    key = "home:departure:door:held"
    result = await propose_insight_candidate(
        timed_pool,
        origin_butler="home",
        priority=60,
        category="departure",
        dedup_key=key,
        message=f"Digest {key}",
        expires_at=_AWAKE + timedelta(days=1),
        premise=_edge_premise(_AWAKE),
        now=_AWAKE,
    )
    assert result["status"] == "accepted", result
    candidate = str(
        await timed_pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1", key)
    )
    holder = await timed_pool.fetchval(
        "UPDATE insight_candidates SET claim_id = gen_random_uuid(), claimed_at = $2 "
        "WHERE id = $1::uuid RETURNING claim_id",
        candidate,
        _AWAKE - timedelta(minutes=1),
    )
    notify = _sent()
    with (
        patch.dict(
            "butlers.tools.switchboard.insight.premises.PREMISE_PROBES",
            {_EDGE: AsyncMock(return_value=False)},
        ),
        patch("butlers.context_bus.get_active_context", _context()),
    ):
        await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    row = await _row(timed_pool, candidate)
    assert row["status"] == "pending" and row["claim_id"] == holder
    notify.assert_not_awaited()


async def test_fresh_digest_claim_is_left_to_its_holder(timed_pool):
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    candidate = await _propose_digest(timed_pool)
    await timed_pool.execute(
        "UPDATE insight_candidates SET claim_id = gen_random_uuid(), claimed_at = $2 "
        "WHERE id = $1::uuid",
        candidate,
        _AWAKE - timedelta(minutes=1),
    )
    notify = _sent()
    with patch("butlers.context_bus.get_active_context", _context()):
        result = await delivery_cycle(timed_pool, notify_fn=notify, now=_AWAKE)
    assert result["delivered"] == [] and (await _row(timed_pool, candidate))["status"] == "pending"
    notify.assert_not_awaited()


async def _propose_card(pool, *, key: str, category: str = "meeting-card", declare=True):
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    result = await propose_insight_candidate(
        pool,
        origin_butler="relationship",
        priority=70,
        category=category,
        dedup_key=key,
        message=f"Card {key}",
        expires_at=_AWAKE + timedelta(minutes=20),
        lane="timed",
        deliver_after=_AWAKE,
        metadata={"passes_context": ["meeting", "focused"]} if declare else None,
        now=_AWAKE,
    )
    assert result["status"] == "accepted", result
    return str(await pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1", key))


@pytest.mark.parametrize("signal", ["meeting", "focused"])
async def test_a_meeting_card_passes_meeting_and_focused_but_another_candidate_is_held(
    timed_pool, signal
):
    """bu-jv1moa.9: the declared exemption is per candidate; nothing else changes."""
    card = await _propose_card(timed_pool, key="relationship:meeting-card:e1:100")
    other = await _propose_card(
        timed_pool, key="relationship:other:e1:100", category="other", declare=True
    )
    plain = await _propose_timed(timed_pool)
    notify = _sent()
    result = await _run_timed(timed_pool, notify, context=_context((signal, timedelta(minutes=30))))
    assert result["delivered"] == [card]
    assert str(other) in result["held"] and plain in result["held"]
    assert (await _row(timed_pool, other))["status"] == "pending"
    assert (await _row(timed_pool, plain))["status"] == "pending"


@pytest.mark.parametrize("signal", ["dnd", "sleeping"])
async def test_a_meeting_card_is_still_held_by_dnd_and_sleeping(timed_pool, signal):
    card = await _propose_card(timed_pool, key="relationship:meeting-card:e2:100")
    notify = _sent()
    result = await _run_timed(timed_pool, notify, context=_context((signal, timedelta(hours=1))))
    assert result["delivered"] == [] and card in result["held"]
    notify.assert_not_awaited()


async def test_withdraw_pending_candidates_withdraws_only_pending_unclaimed_named_keys(timed_pool):
    from butlers.tools.switchboard.insight.broker import withdraw_pending_candidates

    stale = await _propose_card(timed_pool, key="relationship:meeting-card:e3:100")
    keep = await _propose_card(timed_pool, key="relationship:meeting-card:e3:200")
    sent = await _propose_card(timed_pool, key="relationship:meeting-card:e3:300")
    await timed_pool.execute(
        "UPDATE insight_candidates SET status = 'delivered' WHERE id = $1", sent
    )

    count = await withdraw_pending_candidates(
        timed_pool,
        origin_butler="relationship",
        dedup_keys=[
            "relationship:meeting-card:e3:100",
            "relationship:meeting-card:e3:300",
            "relationship:meeting-card:e3:100",
        ],
        reason="meeting_changed",
        now=_AWAKE,
    )

    assert count == 1
    assert (await _row(timed_pool, stale))["status"] == "withdrawn"
    assert (await _row(timed_pool, stale))["metadata"]["withdrawn_reason"] == "meeting_changed"
    assert (await _row(timed_pool, keep))["status"] == "pending"
    assert (await _row(timed_pool, sent))["status"] == "delivered"


async def test_withdraw_pending_candidates_leaves_other_origins_and_the_digest_lane(timed_pool):
    from butlers.tools.switchboard.insight.broker import withdraw_pending_candidates

    foreign = await _propose_card(timed_pool, key="relationship:meeting-card:e4:100")
    await timed_pool.execute(
        "UPDATE insight_candidates SET origin_butler = 'home' WHERE id = $1", foreign
    )
    digest = await _propose_digest(timed_pool, key="relationship:meeting-card:e4:200")
    await timed_pool.execute(
        "UPDATE insight_candidates SET origin_butler = 'relationship' WHERE id = $1", digest
    )

    count = await withdraw_pending_candidates(
        timed_pool,
        origin_butler="relationship",
        dedup_keys=["relationship:meeting-card:e4:100", "relationship:meeting-card:e4:200"],
        reason="meeting_changed",
        now=_AWAKE,
    )

    assert count == 0
    assert (await _row(timed_pool, foreign))["status"] == "pending"
    assert (await _row(timed_pool, digest))["status"] == "pending"


async def test_a_malformed_context_signal_skips_the_cycle_instead_of_raising(timed_pool):
    await _propose_card(timed_pool, key="relationship:meeting-card:e5:100")
    bad = AsyncMock(return_value=[object()])
    result = await _run_timed(timed_pool, _sent(), context=bad)
    assert result["skipped"] is True and result["reason"] == "attention_store_unavailable"
