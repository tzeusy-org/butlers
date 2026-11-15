"""The break-through sensor: shadow-only, bounded, exactly once, metadata only (bu-s11n0s.8 S1)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core import break_through
from butlers.core.break_through import BREAK_THROUGH_EVENT_TYPE, observe_inbound

pytestmark = pytest.mark.unit

CLAUSE = uuid.uuid4()
ENTITY = uuid.uuid4()
REQUEST = str(uuid.uuid4())
# Fixtures a leak would show up in. None of these may reach the event or the hit row.
SUBJECT = "Subject: Your flight SQ321 is delayed"
SENDER = "mum@example.test"


class _Pool:
    """A pool whose evaluator and hit table answer from fixtures, recording every call."""

    def __init__(self, *, clause: uuid.UUID | None = CLAUSE, fresh: bool = True) -> None:
        self.clause = clause
        self.fresh = fresh
        self.evaluated: list[dict[str, Any]] = []
        self.inserted: list[tuple[Any, ...]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "dashboard_attention.evaluate" in query:
            self.evaluated.append(dict(args[0]))
            if self.clause is None:
                return json.dumps({"cause": "routine"})
            return json.dumps({"cause": "owner_clause", "ref": str(self.clause)})
        if "break_through_hits" in query:
            self.inserted.append(args)
            return args[0] if self.fresh else None
        raise AssertionError(query)


@pytest.fixture(autouse=True)
def _quiet_state():
    break_through._last_fault_log = None
    yield
    break_through._last_fault_log = None


@pytest.fixture
def published():
    publish = AsyncMock(return_value={"status": "ok"})
    resolve = AsyncMock(return_value=SimpleNamespace(entity_id=ENTITY, name="Mum"))
    with (
        patch("butlers.core_tools._domain_events.publish_domain_event", publish),
        patch("butlers.identity.resolve_contact_by_channel", resolve),
    ):
        yield SimpleNamespace(publish=publish, resolve=resolve)


def _published_kwargs(published: Any) -> dict[str, Any]:
    call = published.publish.await_args
    assert call is not None
    return call.kwargs


async def _observe(pool: Any, **overrides: Any) -> str | None:
    args: dict[str, Any] = {
        "channel": "email",
        "sender_identity": SENDER,
        "normalized_text": SUBJECT,
        "raw": None,
        "source_request_id": REQUEST,
    }
    args.update(overrides)
    return await observe_inbound(pool, **args)


async def test_a_matching_clause_records_one_hit_and_publishes_one_event(published) -> None:
    pool = _Pool()

    assert await _observe(pool) == str(CLAUSE)

    assert pool.inserted == [(CLAUSE, uuid.UUID(REQUEST), "email")]
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


async def test_a_replayed_request_inserts_nothing_new_and_publishes_nothing(published) -> None:
    pool = _Pool(fresh=False)

    assert await _observe(pool) is None

    assert len(pool.inserted) == 1
    published.publish.assert_not_awaited()


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

    result = await observe_inbound(
        pool,
        channel="email",
        sender_identity=SENDER,
        normalized_text=SUBJECT,
        raw=None,
        source_request_id=REQUEST,
        timeout_s=0.05,
    )
    assert result is None
    published.publish.assert_not_awaited()


async def test_a_publish_failure_is_not_a_hit_announcement_and_never_raises(published) -> None:
    published.publish.return_value = {"status": "error"}
    pool = _Pool()

    assert await _observe(pool) is None

    assert len(pool.inserted) == 1


async def test_a_malformed_source_request_id_is_a_miss_not_a_crash(published) -> None:
    pool = _Pool()

    assert await _observe(pool, source_request_id="not-a-uuid") is None

    published.publish.assert_not_awaited()
