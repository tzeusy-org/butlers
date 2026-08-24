"""Unit tests for butlers.core.infra_conditions (bu-27dxl.6.2).

Covers what is meaningfully testable without a real Postgres connection:
canonical fingerprint computation (Decision #1) and reconcile_snapshot's
input validation, which must reject before ever touching the pool. The
actual lifecycle/concurrency/recurrence semantics (AC1-4) are transaction-
and advisory-lock-dependent and are covered against real Postgres in
tests/integration/test_infra_conditions_roundtrip.py — mocking asyncpg's
acquire/transaction chaining here would test the mock, not the contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core.infra_conditions import (
    ESCALATION_LEVELS,
    RESOLUTION_METADATA_KEYS,
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
)

pytestmark = pytest.mark.unit


class TestComputeFingerprint:
    def test_same_facts_produce_the_same_fingerprint(self):
        a = compute_fingerprint("deploy_drift", 1, {"chain": "core", "expected": "core_182"})
        b = compute_fingerprint("deploy_drift", 1, {"chain": "core", "expected": "core_182"})
        assert a == b

    def test_key_order_does_not_affect_the_fingerprint(self):
        a = compute_fingerprint("deploy_drift", 1, {"chain": "core", "expected": "core_182"})
        b = compute_fingerprint("deploy_drift", 1, {"expected": "core_182", "chain": "core"})
        assert a == b

    def test_set_valued_facts_are_order_independent(self):
        a = compute_fingerprint("calendar_sync_deadman", 1, {"stale_ids": {"c", "a", "b"}})
        b = compute_fingerprint("calendar_sync_deadman", 1, {"stale_ids": {"b", "c", "a"}})
        assert a == b

    def test_different_source_changes_the_fingerprint(self):
        a = compute_fingerprint("deploy_drift", 1, {"chain": "core"})
        b = compute_fingerprint("external_deadman", 1, {"chain": "core"})
        assert a != b

    def test_version_bump_changes_the_fingerprint_for_identical_facts(self):
        """Decision #1: a version bump computes a NEW fingerprint even when the
        stable facts are otherwise unchanged — it never reinterprets the prior
        episode's identity (see infra_conditions module docstring re bu-rxo0l)."""
        v1 = compute_fingerprint("deploy_drift", 1, {"chain": "core"})
        v2 = compute_fingerprint("deploy_drift", 2, {"chain": "core"})
        assert v1 != v2

    def test_mutable_evidence_does_not_belong_in_identity_facts(self):
        """Sanity check on the documented contract: two observations of the
        same underlying condition with different mutable evidence (e.g. an
        updated revision string) must be computed from the SAME facts dict by
        the caller to land on the same identity — this module cannot enforce
        that, but confirms nothing here silently varies with wall-clock time
        or object identity."""
        facts = {"schema": "public", "table": "infra_conditions"}
        a = compute_fingerprint("deploy_drift", 1, facts)
        b = compute_fingerprint("deploy_drift", 1, dict(facts))
        assert a == b

    def test_returns_a_sha256_hex_digest(self):
        fp = compute_fingerprint("deploy_drift", 1, {"chain": "core"})
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
                source="deploy_drift",
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
                source="deploy_drift",
                observations=[
                    Observation(fingerprint="abc"),
                    Observation(fingerprint="abc"),
                ],
                snapshot_complete=True,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()

    @pytest.mark.parametrize("reserved_key", sorted(RESOLUTION_METADATA_KEYS))
    async def test_rejects_metadata_claiming_a_reserved_resolution_key(self, reserved_key: str):
        """bu-o4i4j: the reservation covers this ledger too, not just owner_conditions.

        The supersede path resolves infra episodes and writes the terminal
        reason to top-level ``metadata.resolution_reason`` through the same
        creation-wins merge an explicit resolution uses. A producer that had
        already claimed the key would win that merge and the terminal reason
        would vanish with no error, so the key is refused here instead.
        """
        pool = AsyncMock()
        with pytest.raises(ValueError, match=reserved_key):
            await reconcile_snapshot(
                pool,
                source="deploy_drift",
                observations=[Observation(fingerprint="abc", metadata={reserved_key: "preset"})],
                snapshot_complete=True,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()


def test_escalation_levels_are_ordered_l0_through_l3():
    assert ESCALATION_LEVELS == ("L0", "L1", "L2", "L3")
