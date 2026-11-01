"""meeting_debrief_answer refuses a counterparty that is no longer active [bu-q7vx1q.69]."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.testing.fake_pool import AsyncCtx
from butlers.tools.relationship.meeting_debrief import meeting_debrief_answer

pytestmark = pytest.mark.unit

_MODULE = sys.modules[meeting_debrief_answer.__module__]
_PERSON = str(uuid.uuid4())


class _Conn:
    def __init__(self) -> None:
        self.row = {
            "id": uuid.uuid4(),
            "event_id": uuid.uuid4(),
            "occurrence_start": datetime(2031, 3, 1, tzinfo=UTC),
            "event_title": "Roadmap sync",
            "attendees": json.dumps([{"email": "sam@example.test", "entity_id": _PERSON}]),
            "state": "pending",
        }

    def transaction(self):
        return AsyncCtx(None)

    async def fetchrow(self, *_args):
        return self.row

    async def fetchval(self, *_args):
        return self.row["id"]


class _Pool:
    def __init__(self) -> None:
        self.conn = _Conn()

    def acquire(self):
        return AsyncCtx(self.conn)


@pytest.fixture
def create(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(_MODULE, "create_commitment", mock)
    return mock


async def test_inactive_counterparty_refuses_with_a_posture_free_reason(monkeypatch, create):
    pool = _Pool()
    seen = []

    async def inactive(executor, ids):
        seen.append(executor)
        return set()

    monkeypatch.setattr(_MODULE, "active_entity_ids", inactive)

    result = await meeting_debrief_answer(
        pool,
        debrief_id=str(pool.conn.row["id"]),
        commitments=[{"summary": "Send the deck", "counterparty_entity_id": _PERSON}],
    )

    assert result == {
        "status": "invalid",
        "reason": "counterparty is not available for commitments",
    }
    for word in ("memorial", "quiet", "no_contact", "posture"):
        assert word not in result["reason"]
    create.assert_not_awaited()
    assert seen == [pool.conn], "posture is read on the answer transaction's connection"


async def test_null_counterparty_does_not_consult_posture(monkeypatch, create):
    pool = _Pool()
    pool.conn.row["attendees"] = json.dumps([])
    probe = AsyncMock(return_value=set())
    monkeypatch.setattr(_MODULE, "active_entity_ids", probe)

    result = await meeting_debrief_answer(pool, debrief_id=str(pool.conn.row["id"]))

    assert result["status"] == "none_agreed"
    probe.assert_not_awaited()
