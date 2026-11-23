"""Owner mode holds and owner law at the owner-delivery boundary (bu-dhloi1.22.1).

A hold may only hold a subject's push. It never raises urgency, an unknown posture holds
nothing, and the overlay is read without a row lock.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.core import owner_delivery as od
from butlers.core import owner_delivery_holds as holds
from butlers.perception import flags

pytestmark = pytest.mark.unit

_OWNER = uuid.uuid4()
_NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


class _Conn:
    """A stateful fake: tracks the active role, serves owner.mode and overlay reads."""

    def __init__(
        self,
        *,
        modes: list[str] | None = None,
        state: str = "known",
        fresh_until: datetime | None = _NOW + timedelta(hours=1),
        overlay: dict[str, Any] | None = None,
        overlay_error: bool = False,
        role_error: bool = False,
        read_error: bool = False,
        law: list[dict[str, Any]] | None = None,
    ) -> None:
        self.role = "butler_rw"
        self.modes = modes if modes is not None else []
        self.state = state
        self.fresh_until = fresh_until
        self.overlay = overlay or {}
        self.overlay_error = overlay_error
        self.role_error = role_error
        self.read_error = read_error
        self.law = law or []
        self.sql: list[str] = []
        self.recorded: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def transaction(self):
        saved = self.role
        try:
            yield
        except Exception:
            self.role = saved
            raise

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if sql == "SELECT current_user":
            return self.role
        if sql == "SELECT session_user":
            return "butler_rw"
        if sql == "SELECT clock_timestamp()":
            return _NOW
        if "perception_read" in sql:
            if self.read_error:
                raise RuntimeError("unreadable")
            known = self.state == "known"
            return {
                "kind": "owner.mode",
                "state": self.state,
                "value": self.modes if known else None,
                "stale_last_value": None,
                "source": "context.mode",
                "observed_at": _NOW.isoformat(),
                "fresh_until": self.fresh_until.isoformat() if self.fresh_until else None,
                "age_seconds": 1.0,
                "labels": {},
                "availability_reason": "ok",
                "generation": 7,
            }
        raise AssertionError(sql)

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql.append(sql)
        if sql.startswith("SET LOCAL ROLE"):
            if self.role_error:
                raise RuntimeError("permission denied")
            self.role = sql.split()[-1].strip('"')
        elif sql == "RESET ROLE":
            self.role = "butler_rw"
        elif sql.startswith("UPDATE public.owner_delivery_subjects SET holding_reason"):
            self.recorded.append(args)
        return "UPDATE 1"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "owner_mode_overlay" in sql:
            if self.overlay_error:
                raise RuntimeError("permission denied")
            mode = args[0]
            if mode not in self.overlay:
                return None
            edit = self.overlay[mode]
            holds, through = edit if isinstance(edit, tuple) else (edit, [])
            return {"version": 1, "duration_s": None, "holds": holds, "break_through": through}
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql.append(sql)
        if "owner_policy_holds" in sql:
            return self.law
        raise AssertionError(sql)


def _subject(urgency: od.Urgency = od.ROUTINE) -> od.Subject:
    return od.Subject(
        uuid.uuid4(), "approval", "general", "a1", _OWNER, "d", 1, "pending", urgency.cls,
        urgency.reason,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags, "OWNER_DELIVERY_MODE_HOLDS_LIVE", True)


async def test_an_active_mode_that_holds_proactive_holds_a_routine_subject() -> None:
    conn = _Conn(modes=["sick"])  # sick's recorded default holds proactive items
    hold = await holds.mode_hold(conn, _subject(), "record")
    assert hold is not None
    assert hold.receipt() == {
        "stage": "owner_mode",
        "generation": 7,
        "terms": [{"mode": "sick", "obligation_class": "proactive"}],
    }


async def test_a_mode_that_holds_nothing_does_not_hold() -> None:
    assert await holds.mode_hold(_Conn(modes=["focus"]), _subject(), "record") is None


async def test_an_overlay_edit_replaces_the_recorded_default() -> None:
    assert (
        await holds.mode_hold(_Conn(modes=["sick"], overlay={"sick": []}), _subject(), "record")
        is None
    )
    held = await holds.mode_hold(
        _Conn(modes=["focus"], overlay={"focus": ["proactive"]}), _subject(), "record"
    )
    assert held is not None and held.modes == ("focus",)


async def test_home_safety_is_pinned_and_never_mode_held() -> None:
    subject = _subject(od.Urgency("interrupt", "home_safety"))
    assert await holds.mode_hold(_Conn(modes=["sick", "asleep"]), subject, "record") is None


@pytest.mark.parametrize("cause", ["owner_creation", "owner_clause"])
async def test_an_owner_interrupt_is_held_unless_the_mode_lets_it_break_through(
    cause: str,
) -> None:
    subject = _subject(od.Urgency("interrupt", cause))
    held = await holds.mode_hold(_Conn(modes=["sick"]), subject, "record")
    assert held is not None and held.modes == ("sick",)
    through = {"sick": (["proactive"], [cause])}
    assert await holds.mode_hold(_Conn(modes=["sick"], overlay=through), subject, "record") is None


@pytest.mark.parametrize("effect", ["transact", "communicate", "actuate", "administer", None])
async def test_bills_replies_and_undetermined_effects_are_never_mode_held(
    effect: str | None,
) -> None:
    conn = _Conn(modes=["sick"])
    assert await holds.mode_hold(conn, _subject(), effect) is None
    assert not any("perception_read" in sql for sql in conn.sql)


async def test_a_hold_never_changes_the_subjects_urgency() -> None:
    conn = _Conn(modes=["sick"])
    subject = _subject()
    await holds.consume(
        conn, subject, action_id=uuid.uuid4(), effect_class="record", defers_push=True
    )
    assert (subject.urgency_class, subject.urgency_reason) == ("routine", None)
    assert not any("urgency" in sql for sql in conn.sql)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": "unknown", "modes": []},
        {"state": "known", "modes": ["sick"], "fresh_until": _NOW - timedelta(seconds=1)},
        {"state": "known", "modes": ["sick"], "fresh_until": None},
        {"modes": ["not_a_mode"]},
        {"modes": ["sick"], "role_error": True},
        {"modes": ["sick"], "read_error": True},
        {"modes": ["sick"], "overlay_error": True},
    ],
)
async def test_an_unknown_reading_is_recorded_unreadable_and_delivers(
    kwargs: dict[str, Any],
) -> None:
    verdict = await holds.mode_hold(_Conn(**kwargs), _subject(), "record")
    assert verdict is not None and verdict.unreadable and not verdict.defers
    assert verdict.receipt()["terms"] == [{"unreadable": True}]


async def test_an_unreadable_posture_is_recorded_never_passed() -> None:
    conn = _Conn(modes=["sick"], role_error=True)
    hold = await holds.consume(
        conn, _subject(), action_id=uuid.uuid4(), effect_class="record", defers_push=True
    )
    assert hold is None  # delivers while the default awaits an owner decision
    assert len(conn.recorded) == 1 and '"unreadable": true' in conn.recorded[0][1]


async def test_the_unknown_default_is_one_named_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(holds, "HOLD_WHEN_POSTURE_UNKNOWN", True)
    verdict = await holds.mode_hold(_Conn(read_error=True), _subject(), "record")
    assert verdict is not None and verdict.defers


async def test_a_digest_is_deferred_only_when_every_member_is_held() -> None:
    sick = _Conn(modes=["sick"])
    assert await holds.digest_held(sick, _OWNER, ["record", "read"]) is True
    assert await holds.digest_held(_Conn(modes=["sick"]), _OWNER, ["record", "transact"]) is False
    assert await holds.digest_held(_Conn(modes=["sick"]), _OWNER, ["record", None]) is False
    assert await holds.digest_held(_Conn(modes=["sick"]), _OWNER, []) is False
    assert await holds.digest_held(_Conn(modes=["focus"]), _OWNER, ["record"]) is False


async def test_the_overlay_is_read_without_a_row_lock() -> None:
    conn = _Conn(modes=["sick", "focus"])
    await holds.mode_hold(conn, _subject(), "record")
    reads = [sql for sql in conn.sql if "owner_mode_overlay" in sql]
    assert reads
    assert not any(" FOR " in sql.upper() for sql in reads)


async def test_the_reader_role_is_released_before_the_overlay_is_read() -> None:
    conn = _Conn(modes=["sick"])
    await holds.mode_hold(conn, _subject(), "record")
    assert conn.role == "butler_rw"
    set_at = next(i for i, s in enumerate(conn.sql) if s.startswith("SET LOCAL ROLE"))
    reset_at = conn.sql.index("RESET ROLE")
    overlay_at = next(i for i, s in enumerate(conn.sql) if "owner_mode_overlay" in s)
    assert set_at < reset_at < overlay_at


async def test_dark_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags, "OWNER_DELIVERY_MODE_HOLDS_LIVE", False)
    conn = _Conn(modes=["sick"])
    assert await holds.mode_hold(conn, _subject(), "record") is None
    assert conn.sql == []
    assert (
        await holds.consume(
            conn, _subject(), action_id=uuid.uuid4(), effect_class="record", defers_push=True
        )
        is None
    )
    assert conn.sql == [] and conn.recorded == []  # not even the owner-law receipt


async def test_the_push_is_held_and_the_receipt_recorded() -> None:
    conn = _Conn(modes=["sick"])
    subject = _subject()
    hold = await holds.consume(
        conn, subject, action_id=uuid.uuid4(), effect_class="record", defers_push=True
    )
    assert hold is not None
    assert len(conn.recorded) == 1 and '"owner_mode"' in conn.recorded[0][1]


async def test_the_dashboard_is_annotated_never_held() -> None:
    conn = _Conn(modes=["sick"])
    hold = await holds.consume(
        conn, _subject(), action_id=uuid.uuid4(), effect_class="record", defers_push=False
    )
    assert hold is None
    assert len(conn.recorded) == 1 and '"owner_mode"' in conn.recorded[0][1]


async def test_the_owner_law_receipt_is_attached_without_holding_the_card() -> None:
    receipt = {"stage": "owner_law", "constraint": "always_ask", "generation": 3, "terms": []}
    row = {
        "disposition": "parked",
        "capability": "a__b",
        "invocation_id": uuid.uuid4(),
        "effect_id": uuid.uuid4(),
        "refusal_id": None,
        "term": "always_ask",
        "generation": 3,
        "terms": [],
        "created_at": _NOW,
    }
    conn = _Conn(modes=[], law=[row])
    hold = await holds.consume(
        conn, _subject(), action_id=uuid.uuid4(), effect_class="record", defers_push=True
    )
    assert hold is None
    assert len(conn.recorded) == 1 and '"owner_law"' in conn.recorded[0][1]
    assert receipt["stage"] == "owner_law"
