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


def test_away_safety_opts_into_its_own_ladder_one_hour_then_4_hours_then_daily() -> None:
    """bu-jv1moa.87: the away sources' cadence is a per-call opt-in, open-relative."""
    from butlers.jobs import home_away_safety as away

    assert away.INITIAL_GRACE_SECONDS == 3600
    assert [away.ESCALATION_ADVANCE[level] for level in ("L0", "L1", "L2", "L3")] == [
        ("L1", timedelta(hours=4)),
        ("L2", timedelta(hours=24)),
        ("L3", timedelta(hours=24)),
        ("L3", timedelta(hours=24)),
    ]
    assert away.MARKERS == ("notified_at", "rung_due_at", "rung_notified_at")


def test_the_away_opt_in_leaves_home_safety_and_every_other_source_unchanged() -> None:
    """bu-jv1moa.87 AC 6, pinned once: the ledger default and home:safety keep their ladders."""
    from butlers.core import condition_ledger
    from butlers.jobs import home_away_safety as away

    assert condition_ledger.ESCALATION_ADVANCE == {
        "L0": ("L1", timedelta(days=1)),
        "L1": ("L2", timedelta(days=3)),
        "L2": ("L3", timedelta(days=7)),
        "L3": ("L3", timedelta(days=7)),
    }
    assert home_safety.SAFETY_SOURCE == "home:safety"
    assert home_safety.SAFETY_SOURCE not in away.SOURCES
    assert away.ESCALATION_ADVANCE is not condition_ledger.ESCALATION_ADVANCE
    assert away.ESCALATION_ADVANCE is not ESCALATION_ADVANCE


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
