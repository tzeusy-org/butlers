"""A producer that omits the ledger opt-ins keeps the shared behavior (bu-jv1moa.18)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core import owner_conditions
from butlers.core.condition_ledger import ESCALATION_ADVANCE
from butlers.core.owner_conditions import Observation, reconcile_snapshot

pytestmark = pytest.mark.unit


async def _engine_kwargs(monkeypatch: pytest.MonkeyPatch, **opt_ins) -> dict:
    engine = AsyncMock(return_value=[])
    monkeypatch.setattr(owner_conditions, "_reconcile_snapshot", engine)
    await reconcile_snapshot(
        object(),  # type: ignore[arg-type]
        source="finance:bill-overdue",
        observations=[Observation(fingerprint="f")],
        snapshot_complete=False,
        initial_grace_seconds=0,
        **opt_ins,
    )
    return engine.await_args.kwargs


async def test_the_facade_defaults_to_the_shared_ladder_and_no_preserved_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _engine_kwargs(monkeypatch)

    assert kwargs["escalation_advance"] is None  # engine resolves None to the shared ladder
    assert tuple(kwargs["preserve_metadata_keys"]) == ()


async def test_the_opt_ins_reach_the_engine_only_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _engine_kwargs(
        monkeypatch,
        escalation_advance=ESCALATION_ADVANCE,
        preserve_metadata_keys=("notified_at",),
    )

    assert kwargs["escalation_advance"] is ESCALATION_ADVANCE
    assert tuple(kwargs["preserve_metadata_keys"]) == ("notified_at",)
