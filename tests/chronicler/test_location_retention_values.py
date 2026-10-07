"""Retention value contracts, not SQL or owning-source admission evidence."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from butlers.chronicler.location_retention import PolicyUpdate
from butlers.core.state import (
    state_claim_if_changed,
    state_compare_and_set,
    state_delete,
    state_set,
)
from butlers.location_retention import (
    POLICY_STATE_KEY,
    attempt_status,
    content_digest,
    logical_digest,
    path_increment,
    reduced_summary,
    retention_birth,
    spatial_cell,
    strict_days,
)


def test_policy_and_original_birth_are_strict_and_timezone_explicit():
    """REQ-location-retention-001; no boolean, widening, or replay clock reset."""
    assert strict_days(30) == 30
    assert strict_days(1) == 1
    for value in (True, False, 0, -1, 31, 1.0, "1", None):
        with pytest.raises(ValueError):
            strict_days(value)
    recorded = datetime(2026, 1, 5, tzinfo=UTC)
    assert retention_birth(recorded - timedelta(hours=4), recorded) == recorded - timedelta(hours=4)
    assert retention_birth(recorded + timedelta(hours=4, seconds=1), recorded) == recorded
    assert retention_birth(recorded - timedelta(days=40), recorded) == recorded
    with pytest.raises(ValueError):
        retention_birth(recorded.replace(tzinfo=None), recorded)
    assert content_digest({"b": 2, "a": 1}) == content_digest({"a": 1, "b": 2})
    assert content_digest({"lat": 1}) != content_digest({"lat": 2})
    assert logical_digest("original-key") != logical_digest("new-key")
    with pytest.raises(ValueError):
        content_digest({"lat": float("nan")})


def test_spatial_floor_preserves_metrics_without_exact_or_nested_source_copies():
    """REQ-location-retention-004; coarse summaries survive, raw copies do not."""
    original = {
        "start_lat": 1.31415926,
        "start_lon": 103.81234567,
        "end_lat": 1.32718281,
        "end_lon": 103.89876543,
        "centroid_lat": 1.32222222,
        "centroid_lon": 103.85555555,
        "point_count": 12,
        "path_m": 1827.31,
        "duration_seconds": 900,
        "ssid": "private-network",
        "endpoint_identity": "private-device",
        "accuracy": 2,
        "trigger": "p",
        "raw_payload": {"lat": 1.31415926},
        "bounding_box": [1.31415926],
        "label": "observed-label",
    }
    coarse = reduced_summary(original)
    assert coarse["path_m"] == 1827.31
    assert coarse["point_count"] == 12
    assert coarse["duration_seconds"] == 900
    assert coarse["start_lat"] != original["start_lat"]
    assert coarse["centroid_lon"] != original["centroid_lon"]
    for key in ("ssid", "endpoint_identity", "accuracy", "trigger", "raw_payload", "bounding_box"):
        assert key not in coarse
    assert reduced_summary(coarse) == coarse
    assert "path_m" not in reduced_summary({"point_count": 1})
    # A real bent path must not be replaced by its shorter endpoint distance.
    first, bend, last = (1.0, 103.0), (1.0, 103.01), (1.01, 103.01)
    assert path_increment(first, bend) + path_increment(bend, last) > path_increment(first, last)


def test_geographic_cells_wrap_and_are_idempotent_at_poles_and_boundaries():
    """REQ-location-retention-004; metre-based scheme, no decimal-place fiction."""
    assert spatial_cell(0, -180) == spatial_cell(0, 180)
    assert spatial_cell(0, 181) == spatial_cell(0, -179)
    for latitude, longitude in ((90, 23), (-90, -120), (0, 0), (1.31415926, 103.81234567)):
        cell = spatial_cell(latitude, longitude)
        assert -90 < cell.latitude < 90
        assert -180 <= cell.longitude < 180
        assert spatial_cell(cell.latitude, cell.longitude) == cell
        assert cell.nominal_precision_m == 150
        assert cell.scheme_version == 1
    for lat, lon in ((91, 0), (float("nan"), 0), (0, float("inf")), (True, 0)):
        with pytest.raises(ValueError):
            spatial_cell(lat, lon)


def test_missing_completion_failed_latest_attempt_and_staleness_are_distinct():
    """REQ-location-retention-006; no receipt is not an old successful receipt."""
    start = datetime(2026, 1, 5, tzinfo=UTC)
    arguments = {"started_at": start, "lease_until": start + timedelta(minutes=5)}
    assert attempt_status(**arguments, completed_at=None, outcome=None, now=start) == "pending"
    assert (
        attempt_status(
            **arguments, completed_at=None, outcome=None, now=start + timedelta(minutes=5)
        )
        == "unknown"
    )
    assert attempt_status(**arguments, completed_at=start, outcome="failed", now=start) == "failed"
    assert (
        attempt_status(
            **arguments, completed_at=start, outcome="complete", now=start + timedelta(hours=8)
        )
        == "stale"
    )
    assert (
        attempt_status(
            **arguments, completed_at=start, outcome="complete", now=start - timedelta(seconds=1)
        )
        == "unknown"
    )


def test_policy_wire_rejects_coercion_and_caller_asserted_selectors():
    """REQ-location-retention-001; serialization only, not owner-auth proof."""
    assert PolicyUpdate(days=1, expected_version=2).model_dump() == {
        "days": 1,
        "expected_version": 2,
    }
    for invalid in (
        {"days": True, "expected_version": 1},
        {"days": "1", "expected_version": 1},
        {"days": 1.0, "expected_version": 1},
        {"days": 31, "expected_version": 1},
        {"days": 1, "expected_version": True},
        {"days": 1, "expected_version": 0},
        {"days": 1, "expected_version": 2**63},
        {"days": 1, "expected_version": 1, "actor": "owner"},
        {"days": 1, "expected_version": 1, "cutoff": "yesterday"},
        {"days": 1, "expected_version": 1, "provider": "owntracks"},
    ):
        with pytest.raises(ValidationError):
            PolicyUpdate.model_validate(invalid)


async def test_model_state_mutations_cannot_bypass_owner_policy_control():
    """REQ-location-retention-001; all generic mutations reject before SQL."""
    pool = AsyncMock()
    for mutation, args in (
        (state_set, (POLICY_STATE_KEY, {"days": 1})),
        (state_compare_and_set, (POLICY_STATE_KEY, 1, {"days": 1})),
        (state_claim_if_changed, (POLICY_STATE_KEY, {"days": 1})),
        (state_delete, (POLICY_STATE_KEY,)),
    ):
        with pytest.raises(PermissionError):
            await mutation(pool, *args)
    pool.fetchval.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()
    pool.execute.assert_not_awaited()
    # Ordinary state stays positive; this is not a blanket state-store outage.
    pool.fetchval.return_value = 1
    assert await state_set(pool, "ordinary-state", {"value": 1}) == 1
    assert pool.fetchval.await_args.args[1] == "ordinary-state"


def test_frozen_source_wire_binds_rows_and_rejects_duplicate_generations():
    """REQ-location-retention-003; serialization is not source-authority proof."""
    from uuid import uuid4

    from butlers.connectors.owntracks_forgetting import FrozenRaw, ReadyGrant, frozen_manifest

    cutoff = datetime(2026, 1, 5, tzinfo=UTC)
    decision = uuid4()
    row = FrozenRaw(
        raw_id=uuid4(),
        source_revision=1,
        logical_source_digest="a" * 64,
        content_digest="b" * 64,
        retention_at=cutoff - timedelta(seconds=1),
        accepted_request_id=uuid4(),
        accepted_payload_digest="c" * 64,
        accepted_normalized_digest="d" * 64,
    )
    wire = {
        "grant_id": uuid4(),
        "batch_id": uuid4(),
        "decision_id": decision,
        "policy_version": 1,
        "cutoff": cutoff,
        "lease_version": 1,
        "lease_until": cutoff + timedelta(seconds=120),
        "rows": [row],
        "manifest_digest": frozen_manifest(decision, 1, cutoff, [row]).hex(),
    }
    grant = ReadyGrant.model_validate(wire)
    grant.check_manifest()
    assert isinstance(grant.rows, tuple)
    # Renewal changes liveness only, never the frozen set or policy.
    ReadyGrant.model_validate({**wire, "lease_version": 2}).check_manifest()
    for changed in (
        {"cutoff": cutoff + timedelta(seconds=1)},
        {"policy_version": 2},
        {"rows": [row, row]},
        {"rows": [row.model_copy(update={"source_revision": 2})]},
    ):
        with pytest.raises(ValueError):
            ReadyGrant.model_validate({**wire, **changed}).check_manifest()
    for changed in ({"covered": True}, {"cutoff": cutoff.replace(tzinfo=None)}):
        with pytest.raises(ValidationError):
            ReadyGrant.model_validate({**wire, **changed})


async def test_native_retention_failure_has_separate_completion_and_count_only_conditions(
    monkeypatch,
):
    """REQ-location-retention-006; positioned software failure, no SQL credit."""
    from uuid import uuid4

    from butlers.chronicler import location_retention as retention
    from butlers.core import owner_conditions

    trace = []
    run = uuid4()

    async def start(pool):
        trace.append("durable_start")
        return run

    async def prepare(pool, actual_run):
        assert actual_run == run
        trace.append("business_rollback")
        raise RuntimeError("raw detail that must not appear in condition metadata")

    async def complete(pool, actual_run, **kwargs):
        assert actual_run == run
        trace.append("separate_completion")

    async def status(pool):
        assert trace == ["durable_start", "business_rollback", "separate_completion"]
        return {"status": "failed", "blocked_count": 3}

    calls = []

    async def reconcile(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(retention, "start_attempt", start)
    monkeypatch.setattr(retention, "prepare_batch", prepare)
    monkeypatch.setattr(retention, "fail_attempt", complete)
    monkeypatch.setattr(retention, "retention_status", status)
    monkeypatch.setattr(owner_conditions, "reconcile_snapshot", reconcile)
    result = await retention.run_retention(object())
    assert result["status"] == "failed"
    assert [call["source"] for call in calls] == [
        "chronicler:location-retention-projection-lag",
        "chronicler:location-retention-failure",
    ]
    assert calls[0]["observations"][0].metadata == {"overdue_count": 3}
    assert calls[1]["observations"][0].metadata == {"status": "failed"}
    assert all(call["snapshot_complete"] for call in calls)
    # An unknown frontier must not resolve an earlier standing lag by omission.
    calls.clear()
    await retention.publish_conditions(object(), {"status": "unknown"})
    assert calls[0]["snapshot_complete"] is False


def test_source_copy_readback_binds_exact_committed_holder_and_refuses_partial_ack():
    """REQ-location-retention-005/006; metadata control, not actual MCP/SQL proof."""
    from uuid import uuid4

    from butlers.chronicler.location_retention import source_copy_receipt

    decision, receipt = uuid4(), uuid4()
    manifest = content_digest({"native": "frozen-plan"})
    complete = {
        "decision_id": str(decision),
        "manifest_digest": manifest.hex(),
        "receipt_id": str(receipt),
        "source_kind": "switchboard_skipped",
        "forgotten_count": 2,
        "committed_at": datetime(2026, 1, 5, tzinfo=UTC).isoformat(),
    }
    assert source_copy_receipt(complete, decision, manifest, 2) == receipt
    for mismatch in (
        {"decision_id": str(uuid4())},
        {"manifest_digest": "0" * 64},
        {"source_kind": "all_holders"},
        {"forgotten_count": True},
        {"forgotten_count": 1},
        {"committed_at": "2026-01-05T00:00:00"},
        {"receipt_id": None},
    ):
        with pytest.raises(ValueError, match="unavailable"):
            source_copy_receipt({**complete, **mismatch}, decision, manifest, 2)
    with pytest.raises(ValueError):
        source_copy_receipt({"success": True}, decision, manifest, 2)


async def test_native_malformed_carry_is_held_with_ordinary_legacy_positive(monkeypatch):
    """REQ-location-retention-002; never lose an open carry and invent coverage."""
    from butlers.chronicler import location_projection, storage

    conn = AsyncMock()
    conn.fetchval.return_value = "malformed-private-carry"
    monkeypatch.setattr(location_projection, "native_projection_active", lambda: True)
    with pytest.raises(ValueError, match="unavailable"):
        await storage.get_carryover(conn, "owntracks.points")
    conn.fetchval.return_value = {"raw_ids": ["open-native-point"]}
    assert await storage.get_carryover(conn, "owntracks.points") == {
        "raw_ids": ["open-native-point"]
    }
    monkeypatch.setattr(location_projection, "native_projection_active", lambda: False)
    conn.fetchval.return_value = "malformed-legacy-carry"
    assert await storage.get_carryover(conn, "ordinary.source") == {}
