"""The home:safety sentinel's re-escalation ladder and delivery-marker handling (bu-jv1moa.18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.jobs import home_safety
from butlers.jobs.home_safety import (
    ESCALATION_ADVANCE,
    ESCALATION_GRACE_SECONDS,
    SensorReading,
    WatchEntry,
    reconcile_safety,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
_LEAK = WatchEntry("binary_sensor.kitchen_leak", "leak", "Kitchen leak sensor")


def test_a_live_alarm_resends_after_15_minutes_1_hour_4_hours_then_daily() -> None:
    gaps = [ESCALATION_ADVANCE[level][1] for level in ("L0", "L1", "L2", "L3")]

    assert ESCALATION_GRACE_SECONDS == 15 * 60
    assert gaps == [timedelta(hours=1), timedelta(hours=4), timedelta(days=1), timedelta(days=1)]
    assert [ESCALATION_ADVANCE[level][0] for level in ("L0", "L1", "L2", "L3")] == [
        "L1",
        "L2",
        "L3",
        "L3",
    ]


async def test_every_safety_ledger_write_uses_the_ladder_and_keeps_the_delivery_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butlers.core.condition_ledger import ConditionTransition

    reconcile = AsyncMock(return_value=[])
    monkeypatch.setattr(home_safety, "reconcile_snapshot", reconcile)
    monkeypatch.setattr(home_safety, "get_active_condition", AsyncMock(return_value=None))
    fingerprint = home_safety.safety_fingerprint(_LEAK.entity_id, "leak")
    import uuid

    reconcile.side_effect = [
        [
            ConditionTransition(
                condition_id=uuid.uuid4(),
                source=home_safety.SAFETY_SOURCE,
                fingerprint=fingerprint,
                episode=1,
                state="open",
                transition="opened",
                escalation_level="L0",
                next_reescalate_at=None,
            )
        ],
        [],
    ]

    await reconcile_safety(
        object(),  # type: ignore[arg-type]
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _NOW - timedelta(minutes=5))},
        now=_NOW,
        snapshot_complete=True,
        notify=AsyncMock(return_value=True),
    )

    assert reconcile.await_count == 2  # the reconcile itself, then the delivery marker
    for call in reconcile.await_args_list:
        assert call.kwargs["escalation_advance"] is ESCALATION_ADVANCE
        assert tuple(call.kwargs["preserve_metadata_keys"]) == ("notified_at",)
