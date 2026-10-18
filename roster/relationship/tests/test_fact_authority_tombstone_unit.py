"""Merge-tombstone refusal in the fact write lock step, without a database. [bu-gm93xc]"""

from __future__ import annotations

import importlib
import uuid

import pytest

from butlers.core.fact_authority import current_fact_write_context
from butlers.tools.relationship.fact_authority import EntityTombstonedError, locked_report
from butlers.tools.relationship.fact_temporal import TemporalError


class _Conn:
    """Answers the lock and the tombstone re-read; records the order of both."""

    def __init__(self, tombstones: dict[uuid.UUID, str | None]) -> None:
        self.tombstones = tombstones
        self.calls: list[str] = []

    async def execute(self, sql: str, *args):
        self.calls.append("lock")

    async def fetch(self, sql: str, ids):
        self.calls.append("read")
        return [{"id": i, "survivor": self.tombstones[i]} for i in ids if i in self.tombstones]

    async def fetchrow(self, *args):
        return None

    async def fetchval(self, *args):
        return None


async def _lock(conn: _Conn, subject, obj, kind="literal", **kwargs):
    return await locked_report(
        conn,
        current_fact_write_context(),
        subject=subject,
        object=obj,
        object_kind=kind,
        src="test",
        **kwargs,
    )


async def test_tombstoned_subject_is_refused_after_the_lock_and_names_the_survivor() -> None:
    subject, survivor = uuid.uuid4(), uuid.uuid4()
    conn = _Conn({subject: str(survivor)})
    with pytest.raises(EntityTombstonedError) as caught:
        await _lock(conn, subject, "a@x.test", refuse_tombstoned=True)
    assert caught.value.side == "subject" and caught.value.survivor_id == str(survivor)
    assert str(survivor) in str(caught.value) and caught.value.code == "entity_tombstoned"
    assert isinstance(caught.value, TemporalError)
    assert conn.calls == ["lock", "read"]


async def test_tombstoned_edge_object_is_refused() -> None:
    subject, obj, survivor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = _Conn({obj: str(survivor)})
    with pytest.raises(EntityTombstonedError) as caught:
        await _lock(conn, subject, str(obj), "entity", refuse_tombstoned=True)
    assert (caught.value.side, caught.value.entity_id) == ("object", obj)


async def test_subject_is_reported_before_object() -> None:
    subject, obj = uuid.uuid4(), uuid.uuid4()
    conn = _Conn({subject: None, obj: "x"})
    with pytest.raises(EntityTombstonedError) as caught:
        await _lock(conn, subject, str(obj), "entity", refuse_tombstoned=True)
    assert caught.value.side == "subject"


async def test_live_entities_pass() -> None:
    conn = _Conn({})
    await _lock(conn, uuid.uuid4(), str(uuid.uuid4()), "entity", refuse_tombstoned=True)


async def test_non_write_paths_still_reach_a_tombstoned_entity() -> None:
    subject = uuid.uuid4()
    conn = _Conn({subject: "x"})
    await _lock(conn, subject, "a@x.test")  # retract/confirm paths do not set the flag
    assert conn.calls == ["lock"]


async def test_prefers_channel_refuses_a_tombstoned_subject_before_reachability(
    monkeypatch,
) -> None:
    writer = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")

    async def _no_op(*args, **kwargs):
        return None

    async def _unreachable(*args, **kwargs):
        return False  # a merge moved the contact fact to the survivor

    monkeypatch.setattr(writer, "_validate_predicate", _no_op)
    monkeypatch.setattr(writer, "_entity_has_reachability_fact", _unreachable)
    subject, survivor = uuid.uuid4(), uuid.uuid4()
    conn = _Conn({subject: str(survivor)})

    with pytest.raises(EntityTombstonedError) as caught:
        await writer.assert_prefers_channel(None, subject, "email", conn=conn)

    assert caught.value.survivor_id == str(survivor)
    assert conn.calls == ["lock", "read"]
