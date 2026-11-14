"""The owner.mode projection of the owner's mode entries (bu-dhloi1.21 PR6).

``project_modes`` is pure: the active set is the owner's active entries under
the same rule as SQL ``owner_mode_entry_active`` (one ending exactly now has
ended), and the effective end is the earliest finite end among them, never
later than ``now + max_age``, so an open-ended entry still projects a finite
span. ``ModeProjector`` is dark, refuses while its source profile is
unconfigured, writes only as ``perception_context_writer`` through
``PerceptionStore``, and reads nothing but ``switchboard.owner_mode_entries``.
The real store is proven in ``test_mode_projection_postgres.py`` (CI).
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest

from butlers.perception import flags, mode_projection
from butlers.perception.mode_projection import (
    ModeProjection,
    ModeProjectionConflict,
    ModeProjectionDark,
    ModeProjectionProfile,
    ModeProjectionUnavailable,
    ModeProjectionUnconfigured,
    ModeProjector,
    project_modes,
)
from butlers.perception.model import EventRequest
from butlers.perception.modes import active_entry

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=6)
OWNER = UUID("00000000-0000-4000-8000-000000000001")
EVIDENCE = UUID("00000000-0000-4000-8000-000000000002")
PROFILE = ModeProjectionProfile(subject=OWNER, evidence_ref=EVIDENCE, max_age=MAX_AGE)


def _row(mode, until=None, *, exited_at=None, cause="owner_declared", since=None):
    return {
        "mode": mode,
        "since": since or NOW - timedelta(hours=1),
        "until": until,
        "cause": cause,
        "exited_at": exited_at,
    }


def _project(*rows, now=NOW):
    return project_modes(rows, now, max_age=MAX_AGE)


# -- the effective end ------------------------------------------------------------------


def test_the_end_is_the_earliest_finite_until_when_sooner_than_max_age():
    rows = (
        _row("focus", NOW + timedelta(hours=2)),
        _row("asleep", NOW + timedelta(hours=1)),
        _row("travel"),
    )
    assert _project(*rows) == ModeProjection(
        frozenset({"focus", "asleep", "travel"}), NOW + timedelta(hours=1)
    )


def test_the_end_is_now_plus_max_age_when_every_until_is_later():
    rows = (_row("focus", NOW + MAX_AGE + timedelta(seconds=1)), _row("hosting"))
    assert _project(*rows).end == NOW + MAX_AGE


def test_an_until_exactly_at_max_age_ties_to_the_same_end():
    assert _project(_row("focus", NOW + MAX_AGE)).end == NOW + MAX_AGE


def test_open_ended_entries_still_project_a_finite_span():
    projection = _project(_row("travel"), _row("sick"))
    assert projection.modes == frozenset({"travel", "sick"})
    assert projection.end == NOW + MAX_AGE


def test_no_active_entry_projects_the_empty_set_for_max_age():
    assert _project() == ModeProjection(frozenset(), NOW + MAX_AGE)


def test_an_ended_entry_never_shortens_the_span():
    rows = (_row("focus", NOW - timedelta(minutes=5)), _row("travel"))
    assert _project(*rows) == ModeProjection(frozenset({"travel"}), NOW + MAX_AGE)


# -- the active rule is the SQL rule ----------------------------------------------------

_RULE = [
    (None, None),
    (NOW + timedelta(microseconds=1), None),
    (NOW, None),
    (NOW - timedelta(microseconds=1), None),
    (None, NOW - timedelta(minutes=1)),
    (NOW + timedelta(hours=1), NOW - timedelta(minutes=1)),
]


@pytest.mark.parametrize(("until", "exited_at"), _RULE)
def test_exited_and_expired_entries_are_excluded_by_the_shared_rule(until, exited_at):
    row = _row("focus", until, exited_at=exited_at)
    # The same table sw_077's parity test checks against SQL owner_mode_entry_active.
    expected = active_entry("focus", row, NOW) is not None
    assert ("focus" in _project(row).modes) is expected
    assert expected is (exited_at is None and (until is None or until > NOW))


# -- only owner entries, only the eight modes -------------------------------------------


@pytest.mark.parametrize(
    "rows",
    [
        (_row("focus", cause="inferred"),),
        (_row("focus", cause="m7_rule"),),
        (_row("in_flight"),),
        (_row("focus"), _row("focus")),
    ],
)
def test_anything_but_one_owner_entry_per_mode_refuses(rows):
    with pytest.raises(ValueError):
        project_modes(rows, NOW, max_age=MAX_AGE)


def test_an_inferred_entry_refuses_even_when_ended():
    with pytest.raises(ValueError):
        _project(_row("focus", NOW - timedelta(hours=1), cause="inferred"))


@pytest.mark.parametrize(
    "max_age",
    [timedelta(0), timedelta(seconds=-1), timedelta(seconds=1.5), timedelta(seconds=34560001)],
)
def test_a_profile_freshness_the_store_cannot_hold_refuses(max_age):
    with pytest.raises(ValueError):
        ModeProjectionProfile(subject=OWNER, evidence_ref=EVIDENCE, max_age=max_age)


def test_a_naive_now_refuses():
    with pytest.raises(ValueError):
        project_modes((), NOW.replace(tzinfo=None), max_age=MAX_AGE)


# -- the projector ------------------------------------------------------------------------


class _Conn:
    def __init__(self, rows=(), *, role="perception_context_writer", receipts=None):
        self.rows = [dict(r) for r in rows]
        self.role = role
        self.receipts = list(receipts or [{"event_id": "e", "status": "applied"}])
        self.calls: list[tuple[str, str, tuple]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        if "current_setting('role'" in query:
            return self.role
        if "now()" in query:
            return NOW
        if "perception_write" in query:
            receipt = self.receipts.pop(0)
            if isinstance(receipt, Exception):
                raise receipt
            return receipt
        raise AssertionError(f"unexpected query: {query}")

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self.rows

    def writes(self):
        return [EventRequest(c[2][0]).payload() for c in self.calls if "perception_write" in c[1]]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_PROJECTION_LIVE", True)


async def test_the_projector_is_dark_before_touching_the_store():
    assert flags.MODE_PROJECTION_LIVE is False
    conn = _Conn()
    with pytest.raises(ModeProjectionDark):
        await ModeProjector(conn, PROFILE).project()
    assert conn.calls == []


async def test_an_unconfigured_profile_refuses_before_the_store(live):
    conn = _Conn()
    with pytest.raises(ModeProjectionUnconfigured):
        await ModeProjector(conn, None).project()
    assert conn.calls == []


async def test_it_writes_only_as_the_context_writer(live):
    for role in ("butler_switchboard_rw", "perception_owner", "perception_dots_writer"):
        conn = _Conn([_row("focus")], role=role)
        with pytest.raises(ModeProjectionUnavailable):
            await ModeProjector(conn, PROFILE).project()
        assert [c[0] for c in conn.calls] == ["fetchval"]


async def test_it_projects_the_active_entries_as_one_owner_mode_event(live):
    rows = [
        _row("travel"),
        _row("focus", NOW + timedelta(hours=2)),
        _row("asleep", NOW - timedelta(minutes=1)),
        _row("sick", exited_at=NOW - timedelta(minutes=1)),
    ]
    conn = _Conn(rows)
    receipt = await ModeProjector(conn, PROFILE).project(3)
    assert receipt == {"event_id": "e", "status": "applied"}
    (body,) = conn.writes()
    end = (NOW + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert body["kind"] == "owner.mode" and body["source"] == "context.mode"
    assert body["subject"] == str(OWNER) and body["expected_generation"] == 3
    assert body["typed_value_or_ref"] == ["focus", "travel"]
    assert body["effective_span"] == {"start": "2026-05-04T08:00:00Z", "end": end}
    assert body["evidence_refs"] == [
        {"ref": str(EVIDENCE), "source": "context.mode", "expires_at": end}
    ]


async def test_it_reads_nothing_but_the_owner_mode_entries(live):
    conn = _Conn([_row("focus")])
    await ModeProjector(conn, PROFILE).project()
    (read,) = [c for c in conn.calls if c[0] == "fetch"]
    assert read[1].strip() == (
        "SELECT mode, since, until, cause, exited_at FROM switchboard.owner_mode_entries"
    )
    others = [c[1] for c in conn.calls if c[0] == "fetchval"]
    assert len(others) == 3
    assert "current_setting('role'" in others[0] and others[1] == "SELECT now()"
    assert "public.perception_write" in others[2]


def test_the_projection_names_no_other_perception_source():
    source = inspect.getsource(mode_projection)
    for other in (
        "owner.place",
        "owner.activity",
        "owner.clock",
        "situation_slots",
        "perception_read",
        "perception_timeline",
        "owner_mode_overlay",
    ):
        assert other not in source, other


async def test_a_generation_conflict_is_retried_once_at_the_current_generation(live):
    conn = _Conn(
        [_row("focus")],
        receipts=[{"status": "conflict", "generation": 7}, {"event_id": "e"}],
    )
    assert await ModeProjector(conn, PROFILE).project(0) == {"event_id": "e"}
    first, second = conn.writes()
    assert (first["expected_generation"], second["expected_generation"]) == (0, 7)
    assert {**first, "expected_generation": 7} == second


async def test_a_second_conflict_is_raised(live):
    conflict = {"status": "conflict", "generation": 8}
    conn = _Conn([_row("focus")], receipts=[conflict, conflict])
    with pytest.raises(ModeProjectionConflict):
        await ModeProjector(conn, PROFILE).project(0)


async def test_a_store_refusing_the_profile_is_unconfigured(live):
    refused = asyncpg.InsufficientPrivilegeError("perception source authority/profile required")
    conn = _Conn([_row("focus")], receipts=[refused])
    with pytest.raises(ModeProjectionUnconfigured):
        await ModeProjector(conn, PROFILE).project()


async def test_an_unreachable_entries_table_is_unavailable(live):
    class _Missing(_Conn):
        async def fetch(self, query, *args):
            raise asyncpg.InsufficientPrivilegeError("permission denied for table")

    with pytest.raises(ModeProjectionUnavailable):
        await ModeProjector(_Missing(), PROFILE).project()
