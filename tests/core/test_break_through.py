"""The break-through sensor: bounded, recorded once, announced until done, metadata only (bu-s11n0s.8)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core import break_through
from butlers.core.attention_urgency import _current_origin
from butlers.core.break_through import BREAK_THROUGH_EVENT_TYPE, observe_inbound, retry_unpublished

pytestmark = pytest.mark.unit

CLAUSE = uuid.uuid4()
ENTITY = uuid.uuid4()
REQUEST = str(uuid.uuid4())
# Fixtures a leak would show up in. None of these may reach the event or the hit row.
SUBJECT = "Subject: Your flight SQ321 is delayed"
SENDER = "mum@example.test"


CANDIDATE = uuid.uuid4()


class _Pool:
    """A pool whose evaluator and hit table answer from fixtures, recording every call."""

    def __init__(
        self, *, clause: uuid.UUID | None = CLAUSE, fresh: bool = True, stale: bool = False
    ) -> None:
        self.clause = clause
        self.fresh = fresh
        self.stale = stale
        self.proposed = False
        self.evaluated: list[dict[str, Any]] = []
        self.inserted: list[tuple[Any, ...]] = []
        self.stamped: list[tuple[Any, ...]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "dashboard_attention.evaluate" in query:
            self.evaluated.append(dict(args[0]))
            if self.clause is None:
                return json.dumps({"cause": "routine"})
            return json.dumps({"cause": "owner_clause", "ref": str(self.clause)})
        if "INSERT INTO switchboard.break_through_hits" in query:
            self.inserted.append(args)
            return args[0] if self.fresh else None
        if "UPDATE switchboard.break_through_hits SET claimed_at" in query:
            return 1 if self.stale else None
        if "FROM insight_candidates" in query:
            return CANDIDATE if self.proposed else None
        raise AssertionError(query)

    async def execute(self, query: str, *args: Any) -> str:
        assert "published_at = clock_timestamp()" in query
        self.stamped.append(args)
        return "UPDATE 1"


@pytest.fixture(autouse=True)
def _quiet_state():
    break_through._last_fault_log = None
    yield
    break_through._last_fault_log = None


@pytest.fixture
def published():
    pool_holder: dict[str, Any] = {}
    publish = AsyncMock(return_value={"status": "ok"})
    resolve = AsyncMock(return_value=SimpleNamespace(entity_id=ENTITY, name="Mum"))
    origins: list[Any] = []

    async def propose(pool: Any, **kwargs: Any) -> dict[str, Any]:
        origins.append(_current_origin.get())
        pool.proposed = True
        return {"status": "accepted"}

    proposal = AsyncMock(side_effect=propose)
    cycle = AsyncMock(return_value={})
    with (
        patch("butlers.core_tools._domain_events.publish_domain_event", publish),
        patch("butlers.identity.resolve_contact_by_channel", resolve),
        patch("butlers.tools.switchboard.insight.broker.propose_insight_candidate", proposal),
        patch("butlers.tools.switchboard.insight.broker.delivery_cycle", cycle),
        patch("butlers.scheduled_jobs._build_switchboard_insight_notify_fn", MagicMock()),
    ):
        yield SimpleNamespace(
            publish=publish,
            resolve=resolve,
            proposal=proposal,
            cycle=cycle,
            origins=origins,
            holder=pool_holder,
        )


def _published_kwargs(published: Any) -> dict[str, Any]:
    call = published.publish.await_args
    assert call is not None
    return call.kwargs


async def _observe(pool: Any, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "channel": "email",
        "sender_identity": SENDER,
        "normalized_text": SUBJECT,
        "raw": None,
        "source_request_id": REQUEST,
        "thread": "thread-1",
        "received_at": datetime(2026, 11, 16, 9, 0, tzinfo=UTC),
    }
    args.update(overrides)
    return await observe_inbound(pool, **args)


async def test_a_matching_clause_records_one_hit_and_publishes_one_event(published) -> None:
    pool = _Pool()

    hit = await _observe(pool)

    assert hit is not None and hit.clause_id == CLAUSE
    assert pool.inserted[0][:3] == (CLAUSE, uuid.UUID(REQUEST), "email")
    published.publish.assert_awaited_once()
    assert _published_kwargs(published)["event_type"] == BREAK_THROUGH_EVENT_TYPE
    assert _published_kwargs(published)["source_butler"] == "switchboard"


async def test_the_event_payload_is_metadata_only(published) -> None:
    pool = _Pool()

    await _observe(pool, raw={"snippet": "private body text", "payload": {"headers": []}})

    payload = _published_kwargs(published)["payload"]
    assert payload == {
        "clause_id": str(CLAUSE),
        "channel": "email",
        "source_request_id": REQUEST,
        "sender_name": "Mum",
    }
    rendered = json.dumps(payload) + repr(pool.inserted)
    for forbidden in (SUBJECT, "flight", SENDER, "private body text"):
        assert forbidden not in rendered


async def test_an_unresolved_sender_omits_the_name_and_never_falls_back_to_the_address(
    published,
) -> None:
    published.resolve.return_value = None
    pool = _Pool()

    await _observe(pool)

    payload = _published_kwargs(published)["payload"]
    assert "sender_name" not in payload
    assert SENDER not in json.dumps(payload)
    assert SENDER not in published.proposal.await_args.kwargs["message"]


async def test_the_evaluator_sees_the_resolved_entity_and_channel_not_the_text(published) -> None:
    pool = _Pool()

    await _observe(pool)

    (values,) = pool.evaluated
    assert values["entity_id"] == str(ENTITY)
    assert values["channel"] == "email"
    assert values["gate"] == "quiet_hours"
    assert SUBJECT not in json.dumps(values)


async def test_no_matching_clause_records_and_publishes_nothing(published) -> None:
    pool = _Pool(clause=None)

    assert await _observe(pool) is None

    assert pool.inserted == []
    published.publish.assert_not_awaited()
    published.proposal.assert_not_awaited()


async def test_a_replay_of_an_announced_hit_announces_nothing(published) -> None:
    pool = _Pool(fresh=False)

    assert await _observe(pool) is None

    assert len(pool.inserted) == 1
    published.publish.assert_not_awaited()
    published.proposal.assert_not_awaited()


async def test_a_replay_of_an_abandoned_hit_takes_over_and_announces_it(published) -> None:
    pool = _Pool(fresh=False, stale=True)

    hit = await _observe(pool)

    assert hit is not None
    published.publish.assert_awaited_once()
    published.proposal.assert_awaited_once()
    assert len(pool.stamped) == 1


async def test_the_candidate_is_urgent_content_free_and_carries_the_matched_origin(
    published,
) -> None:
    pool = _Pool()

    await _observe(pool)

    kwargs = published.proposal.await_args.kwargs
    assert kwargs["priority"] == 95
    assert kwargs["category"] == "break_through"
    assert kwargs["origin_butler"] == "switchboard"
    assert kwargs["message"] == "Mum messaged you (email) - open"
    assert kwargs["premise"]["probe"] == "inbound_still_unanswered"
    assert kwargs["premise"]["args"]["thread"] == "thread-1"
    for forbidden in (SUBJECT, "flight", SENDER):
        assert forbidden not in json.dumps(kwargs, default=str)
    (origin,) = published.origins
    assert origin is not None and origin.entity_id == ENTITY


async def test_delivery_runs_urgent_and_scoped_to_the_one_candidate(published) -> None:
    pool = _Pool()

    await _observe(pool)

    kwargs = published.cycle.await_args.kwargs
    assert kwargs["urgent_only"] is True
    assert kwargs["candidate_ids"] == [str(CANDIDATE)]


async def test_the_hit_is_stamped_published_only_after_event_and_candidate(published) -> None:
    pool = _Pool()
    published.publish.return_value = {"status": "error"}

    await _observe(pool)

    assert pool.stamped == []
    published.proposal.assert_not_awaited()

    published.publish.return_value = {"status": "ok"}
    await _observe(pool)
    assert len(pool.stamped) == 1


async def test_a_failed_candidate_leaves_the_hit_unstamped_for_the_retry(published) -> None:
    pool = _Pool()
    published.proposal.side_effect = None
    published.proposal.return_value = {"status": "error"}

    await _observe(pool)

    assert pool.stamped == []
    published.cycle.assert_not_awaited()


async def test_a_delivery_failure_does_not_unstamp_or_raise(published) -> None:
    pool = _Pool()
    published.cycle.side_effect = RuntimeError("send blew up")

    assert await _observe(pool) is None

    assert len(pool.stamped) == 1


async def test_retry_unpublished_announces_each_claimed_hit(published) -> None:
    row = {
        "clause_id": CLAUSE,
        "source_request_id": uuid.UUID(REQUEST),
        "channel": "email",
        "sender_name": "Mum",
        "thread": "thread-1",
        "received_at": datetime(2026, 11, 16, 9, 0, tzinfo=UTC),
        "origin": json.dumps({"entity_id": str(ENTITY), "channel": "email"}),
    }
    pool = _Pool()
    pool.fetch = AsyncMock(return_value=[row])  # type: ignore[attr-defined]

    assert await retry_unpublished(pool) == 1

    published.publish.assert_awaited_once()
    assert len(pool.stamped) == 1


async def test_retry_unpublished_never_raises_when_the_store_is_unreadable(published) -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("down"))

    assert await retry_unpublished(pool) == 0


async def test_spawned_observation_returns_before_the_sensor_finishes(published) -> None:
    gate = asyncio.Event()
    original = published.publish.side_effect

    async def slow(*_a: Any, **_k: Any) -> dict[str, str]:
        await gate.wait()
        return {"status": "ok"}

    published.publish.side_effect = slow
    pool = _Pool()
    task = break_through.spawn_observation(
        pool,
        channel="email",
        sender_identity=SENDER,
        normalized_text=SUBJECT,
        raw=None,
        source_request_id=REQUEST,
    )
    await asyncio.sleep(0)
    assert not task.done()
    gate.set()
    await break_through.drain()
    assert task.done() and pool.stamped
    del original


async def test_an_evaluator_that_raises_is_a_miss_and_never_raises(
    published, caplog: pytest.LogCaptureFixture
) -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("function is unavailable: secret detail"))

    with caplog.at_level(logging.WARNING, logger=break_through.logger.name):
        assert await _observe(pool) is None

    published.publish.assert_not_awaited()
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text


async def test_a_dark_evaluator_logs_once_per_interval_not_once_per_message(
    published, caplog: pytest.LogCaptureFixture
) -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=PermissionError("no execute"))

    with caplog.at_level(logging.WARNING, logger=break_through.logger.name):
        for _ in range(5):
            assert await _observe(pool) is None

    assert caplog.text.count("break_through_sensor_unavailable") == 1


async def test_a_slow_evaluator_is_cut_off_at_the_timeout(published) -> None:
    async def never(*_args: Any) -> Any:
        await asyncio.sleep(60)

    pool = MagicMock()
    pool.fetchval = never

    result = await _observe(pool, timeout_s=0.05)
    assert result is None
    published.publish.assert_not_awaited()


async def test_a_malformed_source_request_id_is_a_miss_not_a_crash(published) -> None:
    pool = _Pool()

    assert await _observe(pool, source_request_id="not-a-uuid") is None

    published.publish.assert_not_awaited()


async def test_the_owners_own_message_is_never_a_hit(published) -> None:
    pool = _Pool()

    assert await _observe(pool, owner_sender_identity=f" {SENDER} ") is None

    assert pool.evaluated == []
    published.publish.assert_not_awaited()
