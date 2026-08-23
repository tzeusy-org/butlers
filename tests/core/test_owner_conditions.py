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

from butlers.core import condition_ledger, owner_conditions
from butlers.core.owner_conditions import (
    ESCALATION_LEVELS,
    RESOLUTION_METADATA_KEYS,
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

    @pytest.mark.parametrize("reserved_key", sorted(RESOLUTION_METADATA_KEYS))
    async def test_rejects_metadata_claiming_a_reserved_resolution_key(self, reserved_key: str):
        """REQ-owner-condition-ledger-006: the resolver's keys are not the producer's.

        Creation-wins (REQ-004) is the right rule for producer evidence and the
        wrong one for the keys resolution writes, where it would silently
        discard the closing evidence in favour of whatever the producer put
        there first. Rejecting here is what keeps the two rules compatible.
        """
        pool = AsyncMock()
        with pytest.raises(ValueError, match=reserved_key):
            await reconcile_snapshot(
                pool,
                source="relationship:commitment",
                observations=[
                    Observation(fingerprint="abc", metadata={reserved_key: "preset"}),
                ],
                snapshot_complete=False,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()

    async def test_reserved_key_rejection_names_the_offending_observation(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="deadbeef"):
            await reconcile_snapshot(
                pool,
                source="relationship:commitment",
                observations=[
                    Observation(fingerprint="clean", metadata={"class": "commitment"}),
                    Observation(fingerprint="deadbeef", metadata={"evidence_closed": {}}),
                ],
                snapshot_complete=False,
                initial_grace_seconds=60,
            )
        pool.acquire.assert_not_called()


class TestExplicitResolutionValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("table", ""),
            ("table", "   "),
            ("table", None),
            ("table", 1),
            ("source", ""),
            ("source", "   "),
            ("source", None),
            ("source", 1),
            ("fingerprint", ""),
            ("fingerprint", "   "),
            ("fingerprint", None),
            ("fingerprint", 1),
        ],
    )
    async def test_req_owner_condition_ledger_004_rejects_empty_identity_before_pool(
        self, field: str, value: object
    ) -> None:
        pool = AsyncMock()
        kwargs = {
            "table": "public.owner_conditions",
            "source": "finance:commitment",
            "fingerprint": "fp-1",
        }
        kwargs[field] = value

        with pytest.raises(ValueError, match=field):
            await condition_ledger.resolve_condition(pool, **kwargs)

        pool.acquire.assert_not_called()

    @pytest.mark.parametrize("metadata", [[], "closed", 1, True, {"bad": object()}])
    async def test_req_owner_condition_ledger_004_rejects_non_object_metadata_before_pool(
        self, metadata: object
    ) -> None:
        pool = AsyncMock()

        with pytest.raises(ValueError, match="resolution_metadata"):
            await condition_ledger.resolve_condition(
                pool,
                table="public.owner_conditions",
                source="finance:commitment",
                fingerprint="fp-1",
                resolution_metadata=metadata,
            )

        pool.acquire.assert_not_called()

    async def test_req_owner_condition_ledger_004_facade_exports_and_binds_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []

        async def fake_resolve(pool, **kwargs):
            calls.append(kwargs)
            return "transition"

        monkeypatch.setattr(owner_conditions, "_resolve_condition", fake_resolve)
        result = await owner_conditions.resolve_condition(
            AsyncMock(),
            source="finance:commitment",
            fingerprint="fp-1",
            resolution_metadata=None,
        )

        assert result == "transition"
        assert calls == [
            {
                "table": "public.owner_conditions",
                "source": "finance:commitment",
                "fingerprint": "fp-1",
                "resolution_metadata": None,
            }
        ]
        assert "resolve_condition" in owner_conditions.__all__


def test_escalation_levels_are_ordered_l0_through_l3():
    assert ESCALATION_LEVELS == ("L0", "L1", "L2", "L3")


def test_reuses_the_same_engine_as_infra_conditions():
    """Both facades must share condition_ledger's engine, not diverge copies."""
    from butlers.core import condition_ledger, infra_conditions, owner_conditions

    assert infra_conditions.ESCALATION_LEVELS is condition_ledger.ESCALATION_LEVELS
    assert owner_conditions.ESCALATION_LEVELS is condition_ledger.ESCALATION_LEVELS
    assert infra_conditions.compute_fingerprint is condition_ledger.compute_fingerprint
    assert owner_conditions.compute_fingerprint is condition_ledger.compute_fingerprint
