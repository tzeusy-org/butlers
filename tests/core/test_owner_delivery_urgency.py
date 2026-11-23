"""Derived urgency at the owner-delivery subject boundary (bu-dhloi1.22).

Urgency is computed by the daemon from verified interruption causes and written once with
the subject. A model's priority, an owner-origin goal or an unknown cause never produces it.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest

from butlers.core import owner_delivery as od
from butlers.core.attention_urgency import InterruptionDecision

pytestmark = pytest.mark.unit

_OWNER = uuid.uuid4()


class _Conn:
    """Records the INSERT parameters of open_subject; the first insert wins."""

    def __init__(self, stored: dict[str, Any] | None = None) -> None:
        self.stored = stored
        self.inserts: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if sql.lstrip().startswith("INSERT INTO public.owner_delivery_subjects"):
            self.inserts.append(args)
            if self.stored is not None:
                return None
            self.stored = {
                "id": args[0],
                "kind": args[1],
                "owning_schema": "switchboard",
                "source_ref": args[2],
                "owner_entity": args[4],
                "content_digest": args[5],
                "action_digest": args[6],
                "generation": 1,
                "state": "pending",
                "urgency_class": args[7],
                "urgency_reason": args[8],
            }
            return self.stored
        return self.stored

    async def execute(self, *_: Any) -> str:
        return "INSERT 0 1"


async def _open(conn: _Conn, urgency: od.Urgency | None = None) -> od.Subject:
    kwargs: dict[str, Any] = {} if urgency is None else {"urgency": urgency}
    return await od.open_subject(
        conn,
        kind="approval",
        source_ref="a1",
        owner_entity=_OWNER,
        content_digest="c",
        action_digest="d",
        **kwargs,
    )


@pytest.mark.parametrize("cause", ["owner_creation", "owner_clause", "home_safety"])
def test_a_verified_interruption_cause_derives_an_interrupt_with_its_reason(cause: str) -> None:
    urgency = od.urgency_from_decision(InterruptionDecision(cause, "ref"))
    assert (urgency.cls, urgency.reason) == ("interrupt", cause)


@pytest.mark.parametrize("cause", ["routine", "", "owner", "high", "urgent", "model_priority"])
def test_anything_else_is_routine_with_no_reason(cause: str) -> None:
    urgency = od.urgency_from_decision(InterruptionDecision(cause))
    assert (urgency.cls, urgency.reason) == ("routine", None)


def test_non_decision_input_is_routine() -> None:
    assert od.urgency_from_decision(None) == od.ROUTINE  # type: ignore[arg-type]
    assert od.urgency_from_decision("home_safety") == od.ROUTINE  # type: ignore[arg-type]


def test_an_interrupt_without_a_known_reason_cannot_be_constructed() -> None:
    with pytest.raises(od.OwnerDeliveryRefused):
        od.Urgency("interrupt", None)
    with pytest.raises(od.OwnerDeliveryRefused):
        od.Urgency("interrupt", "model_asked")
    with pytest.raises(od.OwnerDeliveryRefused):
        od.Urgency("routine", "owner_clause")
    with pytest.raises(od.OwnerDeliveryRefused):
        od.Urgency("now", None)


def test_no_caller_can_pass_a_priority_into_the_subject_boundary() -> None:
    params = set(inspect.signature(od.open_subject).parameters)
    assert "urgency" in params
    assert not params & {"priority", "urgency_class", "urgency_reason", "high"}


async def test_a_subject_opens_routine_by_default() -> None:
    conn = _Conn()
    subject = await _open(conn)
    assert (subject.urgency_class, subject.urgency_reason) == ("routine", None)
    assert conn.inserts[0][7:9] == ("routine", None)


async def test_a_derived_interrupt_is_written_with_the_subject() -> None:
    conn = _Conn()
    urgency = od.urgency_from_decision(InterruptionDecision("owner_clause", "r"))
    subject = await _open(conn, urgency)
    assert (subject.urgency_class, subject.urgency_reason) == ("interrupt", "owner_clause")


async def test_a_replay_keeps_the_stored_urgency_whatever_it_derives_now() -> None:
    conn = _Conn()
    first = await _open(conn, od.urgency_from_decision(InterruptionDecision("home_safety")))
    replay = await _open(conn, od.ROUTINE)
    assert (replay.urgency_class, replay.urgency_reason) == ("interrupt", "home_safety")
    assert replay.id == first.id


async def test_a_replay_cannot_raise_a_routine_subject() -> None:
    conn = _Conn()
    await _open(conn)
    replay = await _open(conn, od.urgency_from_decision(InterruptionDecision("home_safety")))
    assert (replay.urgency_class, replay.urgency_reason) == ("routine", None)


class _Savepoint:
    def __init__(self, conn: _Evaluator) -> None:
        self.conn = conn

    async def __aenter__(self) -> None:
        self.conn.entered += 1

    async def __aexit__(self, exc_type: Any, *_: Any) -> None:
        self.conn.rolled_back += exc_type is not None


class _Evaluator:
    def __init__(self, probe_fails: bool = False) -> None:
        self.entered = 0
        self.rolled_back = 0
        self.probe_fails = probe_fails

    def transaction(self) -> _Savepoint:
        return _Savepoint(self)

    async def fetchval(self, *_: Any) -> int:
        if self.probe_fails:
            raise RuntimeError("current transaction is aborted")
        return 1


def _decisions(monkeypatch: pytest.MonkeyPatch, **by_gate: str) -> None:
    from butlers.core import attention_urgency as au

    async def fake(_pool: Any, *, gate: str, origin: Any = None) -> InterruptionDecision:
        return InterruptionDecision(by_gate[gate], "ref")

    monkeypatch.setattr(au, "evaluate_interruption", fake)


async def test_derive_requires_both_gates_to_interrupt(monkeypatch) -> None:
    _decisions(monkeypatch, quiet_hours="owner_clause", context="owner_clause")
    assert await od.derive_urgency(_Evaluator()) == od.Urgency("interrupt", "owner_clause")


@pytest.mark.parametrize(
    ("quiet", "context"),
    [("owner_clause", "routine"), ("routine", "owner_clause"), ("routine", "routine")],
)
async def test_derive_is_routine_unless_both_gates_interrupt(monkeypatch, quiet, context) -> None:
    _decisions(monkeypatch, quiet_hours=quiet, context=context)
    assert await od.derive_urgency(_Evaluator()) == od.ROUTINE


async def test_derive_is_routine_and_rolls_back_when_the_authority_is_unreadable(
    monkeypatch,
) -> None:
    _decisions(monkeypatch, quiet_hours="home_safety", context="home_safety")
    conn = _Evaluator(probe_fails=True)
    assert await od.derive_urgency(conn) == od.ROUTINE
    assert (conn.entered, conn.rolled_back) == (1, 1)


async def test_derive_is_routine_without_a_savepoint_to_contain_it(monkeypatch) -> None:
    _decisions(monkeypatch, quiet_hours="home_safety", context="home_safety")
    assert await od.derive_urgency(object()) == od.ROUTINE
