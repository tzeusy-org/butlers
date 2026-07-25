"""Unit tests for butlers.core.owner_conditions (bu-ep4ks.6).

Mirrors tests/core/test_infra_conditions.py's split: fingerprinting and
reconcile_snapshot's input validation are covered here without a real
Postgres connection. The actual lifecycle/concurrency/recurrence semantics
are covered against real Postgres in
tests/integration/test_owner_conditions_roundtrip.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core.owner_conditions import (
    ESCALATION_LEVELS,
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
)

pytestmark = pytest.mark.unit


class TestComputeFingerprint:
    def test_same_facts_produce_the_same_fingerprint(self):
        a = compute_fingerprint("finance:bill-overdue", 1, {"bill_id": "abc"})
        b = compute_fingerprint("finance:bill-overdue", 1, {"bill_id": "abc"})
        assert a == b

    def test_different_source_changes_the_fingerprint(self):
        a = compute_fingerprint("finance:bill-overdue", 1, {"id": "abc"})
        b = compute_fingerprint("finance:spending-anomaly", 1, {"id": "abc"})
        assert a != b

    def test_returns_a_sha256_hex_digest(self):
        fp = compute_fingerprint("finance:bill-overdue", 1, {"bill_id": "abc"})
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


class TestReconcileSnapshotValidation:
    async def test_rejects_empty_source(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="source"):
            await reconcile_snapshot(
                pool,
                source="",
                observations=[],
                snapshot_complete=True,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()

    async def test_rejects_negative_grace(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="initial_grace_seconds"):
            await reconcile_snapshot(
                pool,
                source="finance:bill-overdue",
                observations=[],
                snapshot_complete=True,
                initial_grace_seconds=-1,
            )
        pool.acquire.assert_not_called()

    async def test_rejects_duplicate_fingerprint_in_one_batch(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="duplicate fingerprint"):
            await reconcile_snapshot(
                pool,
                source="finance:bill-overdue",
                observations=[
                    Observation(fingerprint="abc"),
                    Observation(fingerprint="abc"),
                ],
                snapshot_complete=True,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()


def test_escalation_levels_are_ordered_l0_through_l3():
    assert ESCALATION_LEVELS == ("L0", "L1", "L2", "L3")


def test_reuses_the_same_engine_as_infra_conditions():
    """Both facades must share condition_ledger's engine, not diverge copies."""
    from butlers.core import condition_ledger, infra_conditions, owner_conditions

    assert infra_conditions.ESCALATION_LEVELS is condition_ledger.ESCALATION_LEVELS
    assert owner_conditions.ESCALATION_LEVELS is condition_ledger.ESCALATION_LEVELS
    assert infra_conditions.compute_fingerprint is condition_ledger.compute_fingerprint
    assert owner_conditions.compute_fingerprint is condition_ledger.compute_fingerprint
