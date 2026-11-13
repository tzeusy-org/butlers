"""The owner mode overlay writer against a scripted connection (PR4b).

The real store, trigger and step-up are proven in
``tests/integration/test_owner_mode_overlay.py`` (CI: needs PostgreSQL). These
pin the writer's own order: dark first, expected version, no store call for a
tightening, and for a loosening begin, write, finish with ``mode:version``.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID

import pytest

from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.mode_overlay import ModeConflict, ModeOverlayWriter
from butlers.perception.modes import (
    MODE_LOOSEN_KIND,
    EditBreakThrough,
    EditDuration,
    EditHolds,
    Exit,
    ModeControlsDark,
    ModeStepUpRequired,
)

pytestmark = pytest.mark.unit

ACTION = UUID("66666666-6666-4666-8666-666666666666")


class _Conn:
    def __init__(self, row=None, consume=("admitted", "completed")):
        self.row = row
        self.consume = list(consume)
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", (query, *args)))
        return self.row

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", (query, *args)))
        if "current_database" in query:
            return "butlers:switchboard"
        return {"state": self.consume.pop(0)}

    async def execute(self, query, *args):
        self.calls.append(("execute", (query, *args)))

    def kinds(self):
        return [
            "consume" if "dashboard_stepup.consume" in call[1][0] else call[0]
            for call in self.calls
            if call[0] != "fetchrow" and "current_database" not in call[1][0]
        ]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)


@pytest.fixture
def proof():
    token = dashboard_stepup_proof.set(DashboardStepUpProof("a" * 64, "n" * 43))
    yield
    dashboard_stepup_proof.reset(token)


async def test_the_writer_is_dark_before_touching_the_store():
    conn = _Conn()
    with pytest.raises(ModeControlsDark):
        await ModeOverlayWriter(conn).edit(ACTION, EditHolds("focus", frozenset()), 0)
    assert conn.calls == []


async def test_only_overlay_edits_are_written(live):
    with pytest.raises(ValueError):
        await ModeOverlayWriter(_Conn()).edit(ACTION, Exit("focus"), 0)


async def test_a_tightening_inserts_version_one_with_no_step_up(live):
    conn = _Conn()
    receipt = await ModeOverlayWriter(conn).edit(
        ACTION, EditHolds("focus", frozenset({"proactive"})), 0
    )
    assert receipt == {"mode": "focus", "version": 1, "verdict": "tighten"}
    assert conn.kinds() == ["execute"]
    query, *args = conn.calls[-1][1]
    assert query.startswith("INSERT INTO switchboard.owner_mode_overlay")
    assert args == ["focus", 1, None, ["proactive"], [], ACTION]


async def test_an_edited_mode_is_updated_from_its_stored_version(live):
    row = {"version": 3, "duration_s": None, "holds": ["proactive"], "break_through": []}
    conn = _Conn(row)
    receipt = await ModeOverlayWriter(conn).edit(
        ACTION, EditHolds("focus", frozenset({"proactive", "reply_to_person"})), 3
    )
    assert receipt["version"] == 4
    query, *args = conn.calls[-1][1]
    assert query.startswith("UPDATE switchboard.owner_mode_overlay")
    assert args == ["focus", 4, None, ["proactive", "reply_to_person"], [], ACTION]
    assert "FOR UPDATE" in conn.calls[0][1][0]


async def test_a_stale_expected_version_conflicts(live, proof):
    with pytest.raises(ModeConflict):
        await ModeOverlayWriter(_Conn()).edit(ACTION, EditHolds("sick", frozenset()), 1)


async def test_an_unchanged_mode_writes_nothing(live):
    conn = _Conn()
    receipt = await ModeOverlayWriter(conn).edit(ACTION, EditDuration("focus", None), 0)
    assert receipt == {"mode": "focus", "version": 0, "verdict": "none"}
    assert conn.kinds() == []


async def test_a_loosening_without_a_proof_never_reaches_the_store(live):
    conn = _Conn()
    with pytest.raises(ModeStepUpRequired):
        await ModeOverlayWriter(conn).edit(ACTION, EditHolds("sick", frozenset()), 0)
    assert conn.kinds() == []


async def test_a_loosening_spends_its_step_up_around_the_write(live, proof):
    conn = _Conn()
    change = EditBreakThrough("asleep", frozenset({"contact:partner"}))
    receipt = await ModeOverlayWriter(conn).edit(ACTION, change, 0)
    assert receipt == {"mode": "asleep", "version": 1, "verdict": "loosen"}
    assert conn.kinds() == ["consume", "execute", "consume"]
    begin, finish = [c[1] for c in conn.calls if "dashboard_stepup.consume" in c[1][0]]
    assert begin[1] == "begin" and begin[4] == MODE_LOOSEN_KIND
    assert begin[5].startswith("asleep:1:") and finish[5] == begin[5]
    assert finish[1] == "finish"
    assert json.loads(finish[8]) == {
        "version": 1,
        "kind": MODE_LOOSEN_KIND,
        "id": str(ACTION),
        "rule_id": "asleep:1",
    }


@pytest.mark.parametrize("state", ["refused", "completed"])
async def test_an_unadmitted_step_up_writes_nothing(live, proof, state):
    conn = _Conn(consume=(state,))
    with pytest.raises(ModeStepUpRequired):
        await ModeOverlayWriter(conn).edit(ACTION, EditDuration("focus", timedelta(hours=1)), 0)
    assert conn.kinds() == ["consume"]


async def test_an_unretained_result_fails_the_transaction(live, proof):
    conn = _Conn(consume=("admitted", "refused"))
    with pytest.raises(RuntimeError):
        await ModeOverlayWriter(conn).edit(ACTION, EditHolds("sick", frozenset()), 0)
