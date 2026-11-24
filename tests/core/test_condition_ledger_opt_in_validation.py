"""Input validation for the ledger's opt-in cadence and preserved metadata (bu-jv1moa.18)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from butlers.core.condition_ledger import (
    ESCALATION_ADVANCE,
    _validated_escalation_advance,
    _validated_preserve_keys,
)
from butlers.core.owner_conditions import Observation, reconcile_snapshot

pytestmark = pytest.mark.unit

_HOUR = timedelta(hours=1)


def test_no_override_means_the_shared_ladder_object() -> None:
    assert _validated_escalation_advance(None) is ESCALATION_ADVANCE
    assert [ESCALATION_ADVANCE[level][1] for level in ("L0", "L1", "L2", "L3")] == [
        timedelta(days=1),
        timedelta(days=3),
        timedelta(days=7),
        timedelta(days=7),
    ]


@pytest.mark.parametrize(
    "ladder",
    [
        {"L0": ("L1", _HOUR), "L1": ("L2", _HOUR), "L2": ("L3", _HOUR)},
        {"L0": ("L1", _HOUR), "L1": ("L2", _HOUR), "L2": ("L3", _HOUR), "L3": ("L9", _HOUR)},
        {"L0": ("L1", _HOUR), "L1": ("L2", _HOUR), "L2": ("L3", _HOUR), "L3": ("L3", timedelta(0))},
    ],
)
def test_a_ladder_that_cannot_drive_every_level_is_refused(ladder) -> None:
    with pytest.raises(ValueError, match="escalation_advance"):
        _validated_escalation_advance(ladder)


@pytest.mark.parametrize("keys", [["resolution_reason"], ["identity_payload"], [""], "notified_at"])
def test_preserved_keys_refuse_ledger_owned_or_malformed_names(keys) -> None:
    with pytest.raises(ValueError, match="preserve_metadata_keys"):
        _validated_preserve_keys(keys)


def test_preserved_keys_are_deduplicated_in_order() -> None:
    assert _validated_preserve_keys(["notified_at", "x", "notified_at"]) == ("notified_at", "x")


async def test_the_facade_refuses_before_touching_the_database() -> None:
    with pytest.raises(ValueError, match="preserve_metadata_keys"):
        await reconcile_snapshot(
            object(),  # type: ignore[arg-type]
            source="test:x",
            observations=[Observation(fingerprint="f")],
            snapshot_complete=False,
            initial_grace_seconds=0,
            preserve_metadata_keys=["resolution_reason"],
        )
