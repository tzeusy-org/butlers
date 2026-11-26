"""reconcile_snapshot's ``amend_resolved`` gate: a producer can resolve without amending."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.core import owner_conditions as oc
from butlers.core.condition_ledger import ConditionTransition

pytestmark = pytest.mark.unit


def _transition(kind: str, fp: str) -> ConditionTransition:
    return ConditionTransition(
        condition_id=uuid.uuid4(),
        source="s",
        fingerprint=fp,
        episode=1,
        state="resolved",
        transition=kind,  # type: ignore[arg-type]
        escalation_level="L1",
        next_reescalate_at=None,
    )


async def _run(monkeypatch, transitions, **kwargs):
    amend = AsyncMock(return_value=0)
    monkeypatch.setattr(oc, "enqueue_premise_amendments", amend)

    async def fake(pool, *, post_write, **_kw):
        await post_write(object(), transitions)
        return transitions

    monkeypatch.setattr(oc, "_reconcile_snapshot", fake)
    await oc.reconcile_snapshot(
        object(),
        source="s",
        observations=[],
        snapshot_complete=True,
        initial_grace_seconds=0,
        **kwargs,
    )
    return [t.fingerprint for t in amend.await_args.args[1]]


async def test_without_a_gate_every_resolution_amends(monkeypatch):
    got = await _run(monkeypatch, [_transition("resolved", "a"), _transition("resolved", "b")])
    assert got == ["a", "b"]


async def test_the_gate_decides_per_resolved_transition_and_leaves_others_alone(monkeypatch):
    transitions = [
        _transition("resolved", "revert"),
        _transition("resolved", "decline"),
        _transition("opened", "new"),
    ]
    got = await _run(monkeypatch, transitions, amend_resolved=lambda t: t.fingerprint == "revert")
    assert got == ["revert", "new"]
