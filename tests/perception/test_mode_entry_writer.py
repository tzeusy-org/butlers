"""The owner mode entry writer against a scripted connection (PR5).

The real store, trigger and step-up are proven in
``tests/integration/test_owner_mode_entries.py`` (CI: needs PostgreSQL). These
pin the writer's own order: dark first, the database's ``now``, the expected
version, an ended entry passed as None, no store call for a tightening, and for
a loosening begin, write, finish with ``mode:version``.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest

from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.mode_entries import ModeEntryWriter
from butlers.perception.mode_overlay import ModeConflict
from butlers.perception.modes import (
    MODE_LOOSEN_KIND,
    EditHolds,
    Enter,
    Exit,
    ModeControlsDark,
    ModeEntry,
    ModeStepUpRequired,
    entry_text,
)

pytestmark = pytest.mark.unit

ACTION = UUID("66666666-6666-4666-8666-666666666666")
# The database's clock: the writer never reads the host's.
DB_NOW = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)
SINCE = DB_NOW - timedelta(hours=1)
LATER = DB_NOW + timedelta(hours=8)


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
        if "owner_mode_entries" in query:
            return self.row
        return None  # no overlay row: the mode's default profile

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", (query, *args)))
        if "now()" in query:
            return DB_NOW
        if "current_database" in query:
            return "butlers:switchboard"
        return {"state": self.consume.pop(0)}

    async def execute(self, query, *args):
        self.calls.append(("execute", (query, *args)))

    def kinds(self):
        return [
            "consume" if "dashboard_stepup.consume" in call[1][0] else call[0]
            for call in self.calls
            if call[0] == "execute" or "dashboard_stepup.consume" in call[1][0]
        ]

    def written(self):
        ((query, *args),) = [c[1] for c in self.calls if c[0] == "execute"]
        return query, args


def _row(version=3, until=LATER, exited_at=None, cause="owner_declared"):
    return {
        "version": version,
        "since": SINCE,
        "until": until,
        "cause": cause,
        "exited_at": exited_at,
    }


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)


@pytest.fixture
def proof():
    token = dashboard_stepup_proof.set(DashboardStepUpProof("a" * 64, "n" * 43))
    yield
    dashboard_stepup_proof.reset(token)


def _enter(until, cause="owner_declared"):
    return Enter("focus", until=until, cause=cause)


async def test_the_writer_is_dark_before_touching_the_store():
    conn = _Conn()
    with pytest.raises(ModeControlsDark):
        await ModeEntryWriter(conn).change(ACTION, _enter(LATER), 0)
    assert conn.calls == []


async def test_only_entries_and_exits_are_written(live):
    with pytest.raises(ValueError):
        await ModeEntryWriter(_Conn()).change(ACTION, EditHolds("focus", frozenset()), 0)


async def test_entering_an_unentered_mode_inserts_version_one_from_the_database_now(live):
    conn = _Conn()
    receipt = await ModeEntryWriter(conn).change(ACTION, _enter(LATER), 0)
    assert receipt == {"mode": "focus", "version": 1, "verdict": "tighten"}
    assert conn.kinds() == ["execute"]
    query, args = conn.written()
    assert query.startswith("INSERT INTO switchboard.owner_mode_entries")
    assert args == ["focus", 1, DB_NOW, LATER, "owner_declared", None, ACTION]
    assert "FOR UPDATE" in next(c[1][0] for c in conn.calls if "owner_mode_entries" in c[1][0])


@pytest.mark.parametrize(
    "row",
    [
        _row(until=DB_NOW),  # ended exactly at the database's now
        _row(until=DB_NOW - timedelta(minutes=1)),
        _row(until=None, exited_at=DB_NOW - timedelta(minutes=1)),
    ],
)
async def test_re_entering_an_ended_entry_starts_a_fresh_one_with_no_step_up(live, row):
    conn = _Conn(row)
    receipt = await ModeEntryWriter(conn).change(ACTION, _enter(None), 3)
    assert receipt == {"mode": "focus", "version": 4, "verdict": "tighten"}
    query, args = conn.written()
    assert query.startswith("UPDATE switchboard.owner_mode_entries")
    assert args == ["focus", 4, DB_NOW, None, "owner_declared", None, ACTION]


async def test_extending_an_active_entry_keeps_its_start(live):
    conn = _Conn(_row())
    receipt = await ModeEntryWriter(conn).change(
        ACTION, _enter(LATER + timedelta(hours=1), "owner_confirmed"), 3
    )
    assert receipt["verdict"] == "tighten" and conn.kinds() == ["execute"]
    _, args = conn.written()
    assert args == ["focus", 4, SINCE, LATER + timedelta(hours=1), "owner_confirmed", None, ACTION]


async def test_re_stating_an_active_entry_writes_nothing(live):
    conn = _Conn(_row())
    receipt = await ModeEntryWriter(conn).change(ACTION, _enter(LATER), 3)
    assert receipt == {"mode": "focus", "version": 3, "verdict": "none"}
    assert conn.kinds() == []


async def test_a_stale_expected_version_conflicts(live, proof):
    with pytest.raises(ModeConflict):
        await ModeEntryWriter(_Conn(_row())).change(ACTION, Exit("focus"), 2)


async def test_exiting_a_mode_with_no_active_entry_refuses_before_any_step_up(live, proof):
    for row in (None, _row(until=DB_NOW)):
        conn = _Conn(row)
        with pytest.raises(ValueError):
            await ModeEntryWriter(conn).change(ACTION, Exit("focus"), 0 if row is None else 3)
        assert conn.kinds() == []


async def test_a_loosening_without_a_proof_never_reaches_the_store(live):
    for change in (Exit("focus"), _enter(DB_NOW + timedelta(hours=1))):
        conn = _Conn(_row())
        with pytest.raises(ModeStepUpRequired):
            await ModeEntryWriter(conn).change(ACTION, change, 3)
        assert conn.kinds() == []


def _target(entry, *, exited, version=4):
    digest = hashlib.sha256(entry_text(entry, exited=exited).encode()).hexdigest()[:16]
    return f"focus:{version}:{digest}"


async def test_an_exit_spends_its_step_up_around_the_write(live, proof):
    conn = _Conn(_row())
    receipt = await ModeEntryWriter(conn).change(ACTION, Exit("focus"), 3)
    assert receipt == {"mode": "focus", "version": 4, "verdict": "loosen"}
    assert conn.kinds() == ["consume", "execute", "consume"]
    _, args = conn.written()
    # Exited now; the start, scheduled end and cause are kept.
    assert args == ["focus", 4, SINCE, LATER, "owner_declared", DB_NOW, ACTION]
    begin, finish = [c[1] for c in conn.calls if "dashboard_stepup.consume" in c[1][0]]
    assert begin[1] == "begin" and begin[4] == MODE_LOOSEN_KIND
    active = ModeEntry("focus", SINCE, LATER, "owner_declared")
    assert begin[5] == _target(active, exited=True) and finish[5] == begin[5]
    assert finish[1] == "finish"
    assert json.loads(finish[8]) == {
        "version": 1,
        "kind": MODE_LOOSEN_KIND,
        "id": str(ACTION),
        "rule_id": "focus:4",
    }


async def test_an_early_end_spends_its_step_up_bound_to_the_shortened_entry(live, proof):
    conn = _Conn(_row())
    sooner = DB_NOW + timedelta(hours=1)
    receipt = await ModeEntryWriter(conn).change(ACTION, _enter(sooner), 3)
    assert receipt["verdict"] == "loosen"
    _, args = conn.written()
    assert args == ["focus", 4, SINCE, sooner, "owner_declared", None, ACTION]
    begin = next(c[1] for c in conn.calls if "dashboard_stepup.consume" in c[1][0])
    shortened = ModeEntry("focus", SINCE, sooner, "owner_declared")
    assert begin[5] == _target(shortened, exited=False)


@pytest.mark.parametrize("state", ["refused", "completed"])
async def test_an_unadmitted_step_up_writes_nothing(live, proof, state):
    conn = _Conn(_row(), consume=(state,))
    with pytest.raises(ModeStepUpRequired):
        await ModeEntryWriter(conn).change(ACTION, Exit("focus"), 3)
    assert conn.kinds() == ["consume"]


async def test_an_unretained_result_fails_the_transaction(live, proof):
    conn = _Conn(_row(), consume=("admitted", "refused"))
    with pytest.raises(RuntimeError):
        await ModeEntryWriter(conn).change(ACTION, Exit("focus"), 3)


async def test_a_concurrent_first_entry_is_a_conflict(live):
    class _Raced(_Conn):
        async def execute(self, query, *args):
            raise asyncpg.UniqueViolationError("duplicate key value")

    with pytest.raises(ModeConflict):
        await ModeEntryWriter(_Raced()).change(ACTION, _enter(LATER), 0)


async def test_an_unavailable_store_never_falls_back_to_an_unconfirmed_write(live, proof):
    class _Missing(_Conn):
        async def fetchrow(self, query, *args):
            raise asyncpg.UndefinedTableError("relation does not exist")

    with pytest.raises(ModeStepUpRequired):
        await ModeEntryWriter(_Missing()).change(ACTION, Exit("focus"), 3)
