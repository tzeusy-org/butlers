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

    from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
    from butlers.connectors.owntracks_copy_retention import (
        FilteredCopyPlan,
        NativeFilteredCopyBuffer,
        filtered_location_binding,
        filtered_row_digest,
        reduced_filtered_row,
    )

    copy_wire = {
        key: wire[key]
        for key in ("decision_id", "policy_version", "cutoff", "rows", "manifest_digest")
    }
    FilteredCopyPlan.model_validate(copy_wire).check_manifest()
    for changed in (
        {"rows": [row, row]},
        {"policy_version": 2},
        {"cutoff": cutoff + timedelta(seconds=1)},
    ):
        with pytest.raises(ValueError):
            FilteredCopyPlan.model_validate({**copy_wire, **changed}).check_manifest()
    for field in ("ready", "grant_id", "actor", "endpoint"):
        with pytest.raises(ValidationError):
            FilteredCopyPlan.model_validate({**copy_wire, field: "synthetic"})
    endpoint = "owntracks:synthetic"
    payload = {"_type": "location", "tst": 1767225600, "lat": 1.31415926, "lon": 103.81234567}
    buffer = NativeFilteredCopyBuffer(endpoint)
    fields = {
        "external_message_id": "1767225600:location",
        "source_channel": "owntracks",
        "sender_identity": endpoint,
        "subject_or_preview": "synthetic copied precision",
        "filter_reason": "synthetic",
        "error_detail": "synthetic copied error",
        "full_payload": FilteredEventBuffer.full_payload(
            channel="owntracks",
            provider="owntracks",
            endpoint_identity=endpoint,
            external_event_id="1767225600:location",
            external_thread_id="owntracks:synthetic",
            observed_at=cutoff.isoformat(),
            sender_identity=endpoint,
            raw=payload,
        ),
    }
    buffer.record(**fields)
    native_row = buffer.buffer._rows[0]
    assert filtered_location_binding(endpoint, native_row) == (
        logical_digest(f"owntracks:{endpoint}:1767225600:location"),
        content_digest(payload),
    )
    original = filtered_row_digest(native_row)
    for index in (2, 3, 5, 6, 7, 8, 10):
        altered = list(native_row)
        altered[index] = "synthetic changed field"
        assert filtered_row_digest(tuple(altered)) != original
    reduced = reduced_filtered_row(native_row)
    assert reduced[9] == {} and reduced[10] is None
    assert all("synthetic" not in str(value) for value in reduced[1:])
    assert reduced_filtered_row(reduced) == reduced
    assert filtered_row_digest(reduced) != original
    for index in (1, 2, 3, 4):
        altered = list(native_row)
        altered[index] = "forged"
        with pytest.raises(ValueError):
            filtered_location_binding(endpoint, tuple(altered))
    before = len(buffer)
    with pytest.raises(ValueError):
        buffer.record(**{**fields, "external_message_id": "forged"})
    assert len(buffer) == before
    buffer.closed_sources.add(logical_digest(f"owntracks:{endpoint}:1767225600:location"))
    buffer.record(**fields)
    assert len(buffer) == before  # Actual native floor cache refuses the late copy.
    independent = NativeFilteredCopyBuffer("owntracks:independent")
    independent_fields = {
        **fields,
        "full_payload": FilteredEventBuffer.full_payload(
            channel="owntracks",
            provider="owntracks",
            endpoint_identity="owntracks:independent",
            external_event_id="1767225600:location",
            external_thread_id="owntracks:synthetic",
            observed_at=cutoff.isoformat(),
            sender_identity="owntracks:independent",
            raw=payload,
        ),
    }
    independent.record(**independent_fields)
    assert len(independent) == 1  # Another actual source is preserved.
    # Preparation may remove an older row while flush I/O yields, and a new
    # independent record can arrive before that snapshot is acknowledged.
    # Completing the old snapshot must preserve the genuinely newer copy.
    older = independent.buffer._rows[0]
    independent.buffer._rows.clear()
    independent.record(**independent_fields)
    newer = independent.buffer._rows[0]
    assert newer is not older
    independent._discard_snapshot([older])
    assert independent.buffer._rows == [newer]
    independent._discard_snapshot([newer])
    assert len(independent) == 0
    finite_float = {**payload, "tst": 1767225600.5}
    float_buffer = NativeFilteredCopyBuffer(endpoint)
    float_buffer.record(
        **{
            **fields,
            "external_message_id": "1767225600.5:location",
            "full_payload": FilteredEventBuffer.full_payload(
                channel="owntracks",
                provider="owntracks",
                endpoint_identity=endpoint,
                external_event_id="1767225600.5:location",
                external_thread_id=endpoint,
                observed_at=cutoff.isoformat(),
                sender_identity=endpoint,
                raw=finite_float,
            ),
        }
    )
    assert filtered_location_binding(endpoint, float_buffer.buffer._rows[0]) == (
        logical_digest(f"owntracks:{endpoint}:1767225600:location"),
        content_digest(finite_float),
    )


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

    monkeypatch.setattr(retention, "reconcile_raw_batches", AsyncMock())
    monkeypatch.setattr(retention, "classify_legacy_caches", AsyncMock())
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
    calls.clear()
    await retention.publish_conditions(object(), {"status": "pending", "blocked_count": 0})
    assert calls[0]["snapshot_complete"] is False
    assert calls[1]["observations"][0].metadata == {"status": "pending"}
    calls.clear()
    await retention.publish_conditions(
        object(),
        {"status": "complete", "blocked_count": 0, "unknown_count": 0, "holder_pending_count": 0},
    )
    assert calls[0]["snapshot_complete"] is True and calls[0]["observations"] == []

    # The real deterministic entry must reach the actual completion seam.
    # This software call-order control makes no role/SQL/receipt claim.
    class EntryPool:
        async def fetch(self, query):
            assert "state='holder_pending'" in query
            return []

        async def fetchval(self, query):
            assert "count(*) FROM location_projection_privacy_transitions" in query
            return 0

    async def completed_entry(pool, actual_run):
        assert actual_run == run
        trace.append("native_completion")

    async def completed_status(pool):
        assert trace == ["durable_start", "native_completion"]
        return {"status": "complete", "unknown_count": 0, "holder_pending_count": 0}

    trace.clear()
    monkeypatch.setattr(retention, "prepare_batch", AsyncMock(return_value=None))
    monkeypatch.setattr(retention, "complete_native_attempt", completed_entry)
    monkeypatch.setattr(retention, "retention_status", completed_status)
    assert (await retention.run_retention(EntryPool()))["status"] == "complete"

    # Actual full keyset predicate, not a first-page/empty success. These
    # doubles prove paging/refusal software only; actual source/roles below
    # stay in the existing migrated integration species.
    from uuid import UUID

    class Cohort:
        def __init__(self, size):
            self.plans = [{"decision_id": UUID(int=i + 1)} for i in range(size)]
            self.pages = []

        async def fetch(self, query, cursor):
            assert "ORDER BY decision_id LIMIT 64" in query
            self.pages.append(cursor)
            return [p for p in self.plans if cursor is None or p["decision_id"] > cursor][:64]

    absent = None
    invalid = None

    async def frontier(conn, plan):
        return (
            None if plan["decision_id"] == absent else {"frontier_generation": plan["decision_id"]}
        )

    async def receipt(conn, plan, actual_frontier):
        assert actual_frontier["frontier_generation"] == plan["decision_id"]
        return plan["decision_id"] != invalid

    monkeypatch.setattr(retention, "_all_committed_holders", frontier)
    monkeypatch.setattr(retention, "_committed_raw_plan", receipt)
    assert not await retention._complete_attempt_cohort(Cohort(0))
    cohort = Cohort(65)
    assert await retention._complete_attempt_cohort(cohort)
    assert cohort.pages == [None, UUID(int=64)]
    absent = UUID(int=65)
    assert not await retention._complete_attempt_cohort(Cohort(65))
    absent, invalid = None, UUID(int=65)
    assert not await retention._complete_attempt_cohort(Cohort(65))
    invalid = None
    assert await retention._complete_attempt_cohort(Cohort(65))


async def test_source_copy_readback_binds_exact_committed_holder_and_refuses_partial_ack():
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
    await _assert_native_no_dispatch_source_copy()


async def test_native_malformed_carry_is_held_with_ordinary_legacy_positive(monkeypatch, caplog):
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

    # Genuine optional unavailable and successful empty sources stay distinct.
    # Supplied connection inputs are software-only, not real SQL evidence.
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from butlers.chronicler.adapters.owntracks_ssid import OwnTracksSsidPresenceAdapter

    @asynccontextmanager
    async def acquired():
        yield conn

    @asynccontextmanager
    async def transaction():
        yield

    conn = AsyncMock()
    conn.transaction = transaction
    conn.fetchrow.side_effect = lambda query, *args: (
        {"version": 1} if "location_retention_policy" in query else None
    )
    conn.fetchval.return_value = True
    conn.fetch.return_value = []
    pool = MagicMock()
    pool.acquire = acquired
    pool.execute = AsyncMock()
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={})
    monkeypatch.setattr(location_projection, "get_checkpoint", AsyncMock(return_value=None))
    actual_carry_reader = storage.get_carryover
    monkeypatch.setattr(storage, "get_carryover", AsyncMock(return_value={}))
    # The adapter imports its own storage binding; patch the actual read seam.
    from butlers.chronicler.adapters import owntracks_ssid

    monkeypatch.setattr(owntracks_ssid, "get_carryover", AsyncMock(return_value={}))
    adapter._fetch_points = AsyncMock(return_value=None)
    active = AsyncMock()
    checkpoint = AsyncMock()
    monkeypatch.setattr(location_projection, "mark_source_active", active)
    monkeypatch.setattr(location_projection, "upsert_checkpoint", checkpoint)
    skipped = await location_projection.run_projection(adapter, chronicler_pool=pool)
    assert skipped.skipped and skipped.error is None
    assert active.await_args.kwargs["active"] is False
    checkpoint.assert_not_awaited()
    adapter._fetch_points = AsyncMock(return_value=[])
    successful = await location_projection.run_projection(adapter, chronicler_pool=pool)
    assert successful.error is None and not successful.skipped
    assert active.await_args.kwargs["active"] is True
    assert checkpoint.await_args.kwargs["success"] is True

    # Failure diagnostics never publish source/SSID/error strings and never
    # convert the original failed projection into success. Fixed SQLSTATE is
    # diagnosis only, not proof that real PostgreSQL executed this software.
    import asyncpg

    caplog.clear()
    conn.fetchrow.side_effect = asyncpg.UndefinedColumnError("synthetic-private-ssid-coordinate")
    failure = await location_projection.run_projection(adapter, chronicler_pool=pool)
    assert failure.error == "location_projection_failed"
    assert "stage=policy_lock category=postgres sqlstate=42703" in caplog.text
    assert "synthetic-private" not in caplog.text
    assert checkpoint.await_args.kwargs["success"] is False
    conn.fetchrow.side_effect = lambda query, *args: (
        {"version": 1} if "location_retention_policy" in query else None
    )
    assert (await location_projection.run_projection(adapter, chronicler_pool=pool)).error is None

    # Original generation must match before the legitimate privacy transition.
    from uuid import uuid4

    from butlers.chronicler import location_retention

    # A non-Postgres preparation exception still publishes only closed labels,
    # and the same original error escapes; no missing-object remedy is inferred.
    primary = ValueError("synthetic-private-preparation-body")
    with monkeypatch.context() as narrow:
        narrow.setattr(location_retention, "_prepare_batch", AsyncMock(side_effect=primary))
        caplog.clear()
        with pytest.raises(ValueError) as failed:
            await location_retention.prepare_batch(pool, uuid4())
        assert failed.value is primary
        assert "stage=preparation category=native sqlstate=unknown class=value_error" in caplog.text
        assert "synthetic-private-preparation-body" not in caplog.text
        narrow.setattr(location_retention, "_prepare_batch", AsyncMock(return_value=uuid4()))
        assert await location_retention.prepare_batch(pool, uuid4()) is not None

    key, raw_id, decision = uuid4(), uuid4(), uuid4()
    original = {
        "id": key,
        "payload": {"start_lat": 1.31415926, "start_lon": 103.81234567, "path_m": 25},
        "title": "synthetic precise location",
    }
    native = AsyncMock()
    native.fetchrow.return_value = original
    digest = await location_projection.output_digest(native, {("episode", key)})
    contributor = {
        "raw_id": raw_id,
        "source_revision": 1,
        "adapter_name": "owntracks.points",
        "mapping_revision": b"x" * 32,
        "output_revision": digest,
        "original_output_revision": digest,
    }
    native.fetch.side_effect = lambda query, *args: (
        [contributor] if "SELECT c.*" in query else [{"output_kind": "episode", "output_id": key}]
    )
    cohort = await location_retention._output_generation_cohort(native, [key], [])
    assert cohort == [contributor]
    native.fetchrow.return_value = {
        **original,
        "title": "Location summary",
        "payload": reduced_summary(original["payload"]),
    }
    reduced = await location_projection.output_digest(native, {("episode", key)})
    assert reduced != digest
    native.fetchval.return_value = raw_id
    await location_retention._commit_privacy_generations(native, decision, cohort)
    assert native.execute.await_args.args[-2:] == (digest, reduced)
    assert native.fetchval.await_args.args[-2:] == (reduced, digest)
    assert contributor["original_output_revision"] == digest
    with pytest.raises(location_retention.PolicyUnavailableError, match="generation changed"):
        await location_retention._output_generation_cohort(native, [key], [])

    # Actual native point disposal advances to a minimal stored tombstone.
    # Its adopted BYTEA logical-source binding must survive canonical JSON
    # hashing, not disappear or become an arbitrary object/string fallback.
    from datetime import UTC, datetime

    event = uuid4()
    tombstone = {
        "event_id": event,
        "raw_id": raw_id,
        "source_revision": 1,
        "logical_source_digest": b"p" * 32,
        "decision_id": decision,
        "spatial_precision_m": 150,
        "spatial_scheme_version": 1,
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "privacy": "normal",
        "purged_at": datetime(2026, 2, 1, tzinfo=UTC),
    }
    observed_queries = []

    async def diminished(query, *args):
        observed_queries.append(query)
        assert args == (event,)
        if query == "SELECT * FROM point_events WHERE id=$1":
            return None
        assert query == "SELECT * FROM location_evidence_tombstones WHERE event_id=$1"
        return dict(tombstone)

    native.fetchrow.side_effect = diminished
    diminished_digest = await location_projection.output_digest(native, {("point_event", event)})
    assert observed_queries == [
        "SELECT * FROM point_events WHERE id=$1",
        "SELECT * FROM location_evidence_tombstones WHERE event_id=$1",
    ]
    assert (
        await location_projection.output_digest(native, {("point_event", event)})
        == diminished_digest
    )
    tombstone["logical_source_digest"] = b"q" * 32
    assert (
        await location_projection.output_digest(native, {("point_event", event)})
        != diminished_digest
    )
    tombstone["logical_source_digest"] = (b"p" * 32).hex()
    assert (
        await location_projection.output_digest(native, {("point_event", event)})
        != diminished_digest
    )
    tombstone["logical_source_digest"] = b"p" * 32
    tombstone["privacy"] = "different stored body"
    assert (
        await location_projection.output_digest(native, {("point_event", event)})
        != diminished_digest
    )
    tombstone["privacy"] = "normal"
    point_contributor = {**contributor, "output_revision": digest}
    native.fetch.side_effect = lambda query, *args: [
        {"output_kind": "point_event", "output_id": event}
    ]
    await location_retention._commit_privacy_generations(
        native, decision, [point_contributor], phase="dispose"
    )
    assert native.execute.await_args.args[-3:] == ("dispose", digest, diminished_digest)
    assert native.fetchval.await_args.args[-2:] == (diminished_digest, digest)
    assert contributor["original_output_revision"] == digest

    # Expired descriptors carry a real minimal tombstone, never exact old title.
    from butlers.chronicler.location_evidence import expired_evidence_links

    reader = AsyncMock()
    reader.fetchval.return_value = True
    reader.fetch.return_value = [
        {
            "event_id": key,
            "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
            "privacy": "sensitive",
            "decision_id": decision,
            "spatial_precision_m": 150,
            "relation": "supports",
        }
    ]
    expired = await expired_evidence_links(reader, raw_id)
    assert expired[0]["retention_state"] == "forgotten"
    assert expired[0]["retention_receipt"] == str(decision)
    assert expired[0]["spatial_precision_m"] == 150
    assert expired[0]["descriptor"] == "Exact location evidence forgotten"
    reader.fetchval.return_value = False
    assert await expired_evidence_links(reader, raw_id) == []
    reader.fetchval.return_value = True
    reader.fetch.side_effect = RuntimeError("installed schema changed")
    with pytest.raises(RuntimeError, match="installed schema changed"):
        await expired_evidence_links(reader, raw_id)

    from butlers.chronicler.adapters.owntracks_ssid import (
        _SOURCE_CURSOR_KEY,
        OwnTracksSsidPresenceAdapter,
    )
    from butlers.chronicler.models import ProjectionCheckpoint

    monkeypatch.setattr(owntracks_ssid, "get_carryover", actual_carry_reader)
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={})
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    checkpoint = ProjectionCheckpoint(source_name=adapter.source_name, watermark=stamp)
    conn.fetchval.return_value = {}
    assert await adapter.retention_replay_required(checkpoint, conn) is True
    conn.fetchval.return_value = {
        _SOURCE_CURSOR_KEY: {
            "watermark": stamp.isoformat(),
            "uuid": str(uuid4()),
        }
    }
    assert await adapter.retention_replay_required(checkpoint, conn) is False
    assert await adapter.retention_replay_required(None, conn) is False


async def test_native_inline_fixture_uses_complete_owning_retention_dependencies():
    """REQ-location-retention-008: source producer parity, not real SQL proof."""
    import runpy
    from contextlib import asynccontextmanager
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    statements = []

    class Capture:
        async def execute(self, statement):
            statements.append(str(statement))

        async def fetchval(self, statement):
            assert statement == "SELECT current_schema()"
            return "chronicler"

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield self

    # Invoke both actual fixture producers. Their own SQL bodies and migration
    # registrations must agree; no canned SQL result asserts a DB outcome.
    for path, function in (
        ("tests/contracts/test_chronicler_schema_drift.py", "_apply_inline_ddl"),
        ("roster/chronicler/tests/test_storage_integration.py", "_apply_chronicler_schema"),
    ):
        statements.clear()
        producer = runpy.run_path(str(root / path))[function]
        await producer(Capture())
        emitted = "\n".join(statements)
        assert all(
            field in emitted
            for field in (
                "raw_evidence_retention",
                "projected_evidence_retention",
                "allowed_spatial_precision_m",
                "source_tombstone_behavior",
            )
        )
        if function == "_apply_chronicler_schema":
            assert all(
                "CREATE TABLE " + table in emitted
                for table in (
                    "location_retention_policy",
                    "location_summary_floors",
                    "location_evidence_tombstones",
                    "location_retention_plans",
                )
            )
            assert "Location retention history is permanent" in emitted
            assert "Location retention decision is immutable" in emitted

    # Actual own migration classifier, with fixed catalog rows rather than
    # invocation-identity or namespace-owner authority. SQL selection/replay is
    # separately positioned in the existing migrated species.
    migration = runpy.run_path(
        str(root / "alembic/versions/core/core_264_owntracks_retention_lineage.py")
    )
    select = migration["_select_core_writer_owner"]
    local = ("health", "r", 101, "stored-local-writer", True)
    public = ("public", "r", 102, "stored-public-writer", True)
    assert select("health", [local, public]) == (101, "stored-local-writer")
    assert select("health", [public]) == (102, "stored-public-writer")
    assert select("public", [public]) == (102, "stored-public-writer")
    for rows in (
        [],
        [local, local, public],
        [local[:1] + ("v",) + local[2:], public],
        [public[:-1] + (False,)],
        [public[:2] + (True,) + public[3:]],
        [("foreign", *public[1:])],
    ):
        with pytest.raises(RuntimeError, match="core writer anchor"):
            select("health", rows)

    # Run the actual pre-DDL catalog selection and owner installer. The double
    # filters by its ACTUAL SQL IN names, rather than handing back every seeded
    # row regardless of a missing selector. This models catalog I/O, not SQL.
    import re
    from types import SimpleNamespace
    from unittest.mock import patch

    from sqlalchemy.dialects import postgresql

    create = migration["_create_local_tables"]
    commands = []
    present_tables = set()

    class Catalog:
        dialect = postgresql.dialect()

        def execute(self, statement, parameters):
            query = str(statement)
            assert "pg_catalog.pg_class" in query and parameters == {"schema": "switchboard"}
            selected = re.search(r"c\.relname IN\s*\((.*?)\)", query, re.S)
            assert selected is not None
            selected_names = set(re.findall(r"'([^']+)'", selected.group(1)))
            return SimpleNamespace(scalars=lambda: sorted(present_tables & selected_names))

    catalog = Catalog()
    operation = SimpleNamespace(get_bind=lambda: catalog, execute=commands.append)
    with patch.dict(
        create.__globals__,
        {"op": operation, "_core_writer_owner": lambda schema: (101, "stored-core-writer")},
    ):
        create("switchboard", "SELECT 1")
        absent_commands = [str(q) for q in commands if str(q).startswith("ALTER TABLE ")]
        created_tables = {
            re.fullmatch(r"ALTER TABLE switchboard\.(\w+) OWNER TO .+", q).group(1)
            for q in absent_commands
        }
        assert set(migration["LOCAL_TABLES"]) <= created_tables
        assert all("stored-core-writer" in q for q in absent_commands)
        # A complete existing catalog must never receive an OWNER transfer,
        # including the three original ingress ancestry/claim/source ledgers.
        present_tables.update(created_tables)
        commands.clear()
        create("switchboard", "SELECT 1")
        assert commands == ["SELECT 1"]
        present_tables.clear()
        present_tables.update(
            {
                "location_ingress_input_parents",
                "location_ingress_input_claims",
                "location_ingress_inbox_sources",
            }
        )
        commands.clear()
        create("switchboard", "SELECT 1")
        fresh_after_three = [str(q) for q in commands if str(q).startswith("ALTER TABLE ")]
        assert len(fresh_after_three) == len(absent_commands) - 3
        assert all(
            not any("." + name + " " in q for name in present_tables) for q in fresh_after_three
        )


async def test_native_mcp_input_birth_precedes_emission_and_unknown_commit_refuses(monkeypatch):
    """REQ-location-retention-005/006; actual producer/guard software, not SQL/MCP transport."""
    from contextlib import asynccontextmanager
    from dataclasses import dataclass
    from types import SimpleNamespace
    from uuid import uuid4

    await _assert_owntracks_input_complete_cohort_and_server_lifetime()
    await _assert_native_owntracks_replay_reader_lifetime()
    await _assert_native_input_dispatch_submission_fence()
    await _assert_native_input_shutdown_preserves_actual_lifetimes()
    _assert_native_input_closed_diagnostics()
    await _assert_native_ingress_census_and_actual_task_end()

    from butlers.chronicler import location_retention, storage
    from butlers.core import fact_authority
    from butlers.guards import _McpRuntimeSessionGuard
    from roster.chronicler.modules import ChroniclerModule

    @dataclass
    class NativeRow:
        id: object
        source_name: str
        occurred_at: datetime
        title: str = "Synthetic private location"

    row = NativeRow(uuid4(), "owntracks.points", datetime(2026, 1, 5, tzinfo=UTC))
    native_id = row.id
    birth_args = []
    trace = []
    unknown = False

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            trace.append("own_transaction")
            yield
            trace.append("own_commit")

        async def fetchrow(self, query, *args):
            return {
                "days": 30,
                "version": 1,
                "spatial_scheme_version": 1,
                "updated_at": row.occurred_at,
            }

        async def fetch(self, query, *args):
            return [{"output_id": native_id}] if native_id in args[1] else []

        async def fetchval(self, query, *args):
            if "current_user" in query:
                return "butler_chronicler_rw"
            if "location_retention_frontiers" in query:
                return False
            trace.append("committed_readback")
            return 0 if unknown else sum(birth[0] == args[0] for birth in birth_args)

        async def execute(self, query, *args):
            if "INSERT INTO location_native_copy_births" in query:
                birth_args.append(args)
                trace.append("native_input_birth")

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()

    async def native_reader(actual_conn, **kwargs):
        assert actual_conn in (pool, conn)
        return [row]

    monkeypatch.setattr(storage, "list_point_events", native_reader)

    class MCP:
        def __init__(self):
            self.tools = {}

        def tool(self, *args, **kwargs):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    mcp = MCP()
    location_retention._copy_pools.add(pool)  # Software fixed-constructor registry double.
    await ChroniclerModule().register_tools(mcp, None, SimpleNamespace(pool=pool), "chronicler")
    session_id = uuid4()
    invocation = await fact_authority.register_invocation(
        "chronicler", str(session_id), source_endpoint=None, routed=False
    )
    responses = []

    async def app(scope, receive, send):
        result = await mcp.tools["chronicler_list_events"]()
        # Positioned against a producer bypass: raw response alone is not a birth.
        assert birth_args and trace.index("own_commit") < trace.index("committed_readback")
        assert birth_args[-1][5] == session_id
        assert birth_args[-1][4] is True and birth_args[-1][6] is True
        responses.append(result)

    guard = _McpRuntimeSessionGuard(app, butler_name="chronicler")
    scope = {
        "type": "http",
        "query_string": b"runtime_session_id=caller-forged",
        "headers": [(fact_authority.INVOCATION_HEADER.lower().encode(), invocation.encode())],
    }
    try:
        await guard(scope, None, None)
        assert responses[0]["data"][0]["id"] == row.id
        assert fact_authority._current_copy_invocation.get() is None
        unknown = True
        with pytest.raises(location_retention.PolicyUnavailableError, match="birth is unknown"):
            await guard(scope, None, None)
        assert len(responses) == 1
        assert fact_authority._current_copy_invocation.get() is None
        unknown = False
        birth_args.clear()
        trace.clear()
        await guard(scope, None, None)
        assert len(responses) == 2
        # A caller query can locate diagnostics but never a receiving holder.
        await location_retention.capture_native_read(pool, "point_event", native_reader)
        assert birth_args[-1][5] is None
        # Ordinary non-location inputs retain the existing reader path.
        row.source_name = "calendar.events"
        row.id = uuid4()  # A genuinely unrelated ID, not relabelled stored source lineage.
        birth_args.clear()
        assert await location_retention.capture_native_read(pool, "point_event", native_reader) == [
            row
        ]
        assert birth_args == []
    finally:
        fact_authority.settle_invocation(invocation)
        location_retention._copy_pools.discard(pool)


async def test_raw_reconciliation_binds_full_source_receipt_and_resumes_same_unknown_ids():
    """REQ-location-retention-006; source/commit wiring only, not real PostgreSQL evidence."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler.location_retention import PolicyUnavailableError, reconcile_raw_batches

    plan = {
        "decision_id": uuid4(),
        "batch_id": uuid4(),
        "grant_id": uuid4(),
        "run_id": uuid4(),
        "manifest_digest": b"m" * 32,
        "policy_version": 1,
        "cutoff": datetime(2026, 1, 5, tzinfo=UTC),
        "state": "ready",
    }
    expected = {"raw_id": uuid4(), "source_revision": 1, "logical_source_digest": b"s" * 32}
    header = {**plan, "deleted_count": 1, "already_forgotten_count": 0}
    source_rows = [{**expected, "disposition": "deleted"}]
    trace = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield
            trace.append("own_commit")

        async def fetchrow(self, query, *args):
            if "connectors.owntracks_retention_batches" in query:
                trace.append("source_header_read")
                return header
            if "location_retention_policy" in query:
                return {"version": 1}
            return plan

        async def fetch(self, query, *args):
            if "connectors.owntracks_retention_batch_rows" in query:
                trace.append("source_detail_read")
                return source_rows
            return [expected]

        async def fetchval(self, query, *args):
            if "current_user" in query:
                return "butler_chronicler_rw"
            if "location_retention_frontiers" in query:
                return False
            trace.append("own_committed_readback")
            return plan["state"]

        async def execute(self, query, *args):
            if "SET state='raw_unknown'" in query:
                plan["state"] = "raw_unknown"
            elif "SET state='complete'" in query:
                plan["state"] = "complete"
            elif "deleted_count" in query:
                assert (
                    "sum(b.deleted_count)" in query
                )  # Replay must not increment a cached ACK count.

    class Pool:
        async def fetch(self, query, *args):
            return [dict(plan)] if plan["state"] != "complete" else []

        @asynccontextmanager
        async def acquire(self):
            yield Conn()

    pool = Pool()
    frozen_ids = (plan["decision_id"], plan["batch_id"], plan["grant_id"])
    header = None
    source_rows = []
    await reconcile_raw_batches(pool)
    assert plan["state"] == "raw_unknown"
    assert (plan["decision_id"], plan["batch_id"], plan["grant_id"]) == frozen_ids
    header = {**plan, "deleted_count": 1, "already_forgotten_count": 0}
    source_rows = [{**expected, "disposition": "deleted"}]
    for field, bad in (
        ("grant_id", uuid4()),
        ("manifest_digest", b"x" * 32),
        ("deleted_count", 0),
        ("cutoff", plan["cutoff"] + timedelta(seconds=1)),
    ):
        original = header[field]
        header[field] = bad
        with pytest.raises(PolicyUnavailableError, match="receipt differs|disposition differs"):
            await reconcile_raw_batches(pool)
        assert plan["state"] == "raw_unknown"
        header[field] = original
    source_rows[0]["raw_id"] = uuid4()
    with pytest.raises(PolicyUnavailableError, match="disposition differs"):
        await reconcile_raw_batches(pool)
    source_rows[0]["raw_id"] = expected["raw_id"]
    trace.clear()
    await reconcile_raw_batches(pool)
    assert plan["state"] == "complete"
    assert trace == [
        "source_header_read",
        "source_detail_read",
        "own_commit",
        "own_committed_readback",
    ]
    await reconcile_raw_batches(pool)
    assert (plan["decision_id"], plan["batch_id"], plan["grant_id"]) == frozen_ids

    # Connector ACK/restart observation also requires every member, not just
    # a matching committed header. This is an existing-receipt software path;
    # it supplies no source enrollment, SQL or role evidence.
    from butlers.connectors.owntracks_forgetting import (
        ForgettingRefusedError,
        FrozenRaw,
        ReadyGrant,
        forget_ready_batch,
        frozen_manifest,
    )

    raw = FrozenRaw(
        raw_id=expected["raw_id"],
        source_revision=1,
        logical_source_digest=expected["logical_source_digest"].hex(),
        content_digest="c" * 64,
        retention_at=plan["cutoff"] - timedelta(days=1),
        accepted_request_id=uuid4(),
        accepted_payload_digest="a" * 64,
        accepted_normalized_digest="b" * 64,
    )
    grant = ReadyGrant(
        grant_id=plan["grant_id"],
        batch_id=plan["batch_id"],
        decision_id=plan["decision_id"],
        policy_version=plan["policy_version"],
        cutoff=plan["cutoff"],
        lease_version=1,
        lease_until=plan["cutoff"],
        rows=(raw,),
        manifest_digest=frozen_manifest(
            plan["decision_id"], plan["policy_version"], plan["cutoff"], [raw]
        ).hex(),
    )
    ledger = {
        **plan,
        "manifest_digest": bytes.fromhex(grant.manifest_digest),
        "deleted_count": 1,
        "already_forgotten_count": 0,
    }
    members = [{**expected, "batch_id": grant.batch_id, "disposition": "deleted"}]
    connector_trace = []

    class ConnectorConn:
        @asynccontextmanager
        async def transaction(self):
            yield
            connector_trace.append("commit")

        async def fetchval(self, query, *args):
            return "connector_writer"

        async def fetchrow(self, query, *args):
            connector_trace.append("header")
            return ledger

        async def fetch(self, query, *args):
            connector_trace.append("members")
            return members

        async def execute(self, query, *args):
            assert "INSERT" not in query and "DELETE" not in query

    class ConnectorPool:
        @asynccontextmanager
        async def acquire(self):
            connector_trace.append("acquire")
            yield ConnectorConn()

    connector = ConnectorPool()
    result = await forget_ready_batch(connector, grant)
    assert result["rows"] == members
    assert connector_trace == ["acquire", "header", "commit", "acquire", "header", "members"]
    for changed in (
        [],
        [*members, *members],
        [{**members[0], "raw_id": uuid4()}],
        [{**members[0], "logical_source_digest": b"x" * 32}],
        [{**members[0], "disposition": "unknown"}],
        [{**members[0], "batch_id": uuid4()}],
    ):
        original = members
        members = changed
        with pytest.raises(ForgettingRefusedError, match="committed_receipt_mismatch"):
            await forget_ready_batch(connector, grant)
        members = original
    ledger["deleted_count"] = 0
    with pytest.raises(ForgettingRefusedError, match="committed_receipt_mismatch"):
        await forget_ready_batch(connector, grant)
    ledger["deleted_count"] = 1
    assert await forget_ready_batch(connector, grant) == result


async def test_native_frontier_requires_planted_current_holder_and_committed_inventory():
    """REQ-location-retention-003/005; inventory engine software, NOT source/SQL proof."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler import location_retention as service

    decision, copy = uuid4(), uuid4()
    manifest, digest = b"m" * 32, b"c" * 32

    class Pool:
        def __init__(self):
            self.role = "butler_chronicler_rw"
            self.trace = []
            self.legacy = False
            self.late_session = False
            self.late_cache = False
            self.changed = False
            self.catalog_unknown = False
            self.catalog_pending = False
            self.artifact_pending = False
            self.answer_pending = False
            self.catalog = []
            self.unknown_commit = False
            self.frontier = None
            self.filtered_missing = False
            self.filtered_partial = False
            self.filtered_changed = False
            self.expected = []
            self.raw = ["planted-exact-raw"]
            self.points = ["planted-point-body"]
            self.holders = {
                "switchboard_skipped": {
                    "owning_butler": "switchboard",
                    "holder_kind": "switchboard_skipped",
                    "holder_generation": decision,
                    "source_digest": manifest,
                    "receipt_id": uuid4(),
                },
                "api_server": {
                    "owning_butler": "chronicler",
                    "holder_kind": "api_server",
                    "holder_generation": copy,
                    "source_digest": digest,
                    "receipt_id": uuid4(),
                },
            }
            self.local = {"manifest_digest": manifest, "receipt_id": uuid4()}

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            prior, expected = self.frontier, list(self.expected)
            try:
                yield
            except BaseException:
                self.frontier, self.expected = prior, expected
                self.trace.append("rollback")
                raise
            else:
                self.trace.append("commit")

        async def fetchrow(self, query, *args):
            if "connectors.owntracks_filtered_copy_batches" in query:
                if self.filtered_missing:
                    return None
                return {
                    "receipt_id": decision,
                    "manifest_digest": b"wrong" * 6 if self.filtered_changed else manifest,
                    "policy_version": 1,
                    "cutoff": datetime(2026, 1, 5, tzinfo=UTC),
                    "expected_count": 1,
                }
            if "location_retention_policy" in query:
                self.trace.append("policy")
                return {"version": 1}
            if "location_retention_plans" in query:
                return {
                    "decision_id": decision,
                    "state": "holder_pending",
                    "manifest_digest": manifest,
                    "policy_version": 1,
                    "cutoff": datetime(2026, 1, 5, tzinfo=UTC),
                }
            if "location_retention_frontiers" in query:
                return self.frontier
            if "location_retention_local_receipts" in query:
                return self.local
            if "location_retention_holder_receipts" in query:
                key = "switchboard_skipped" if len(args) == 1 else args[1]
                if "projection_prepare" in query:
                    key = "projection_prepare"
                return self.holders.get(key)
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "connectors.owntracks_filtered_copy_members" in query:
                return [] if self.filtered_partial else [{"copy_generation": copy}]
            if query.startswith("SELECT DISTINCT g.catalog_id AS original_catalog_id"):
                return []  # This API-holder species has no disposed catalog generations.
            if "SELECT a.artifact_generation,i.parent_count" in query:
                return []  # This frontier fixture has no native Memory artifacts.
            if "WITH artifacts AS" in query:
                return self.catalog
            if "FROM location_native_cache_heads" in query:
                return []
            if "FROM location_native_copy_births" in query:
                return [
                    {"copy_generation": copy, "input_digest": digest, "producer_kind": "api_export"}
                ]
            if "FROM location_retention_frontier_holders" in query:
                return [
                    dict(row, receipt_id=self.holders[row["holder_kind"]]["receipt_id"])
                    for row in self.expected
                ]
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "current_user" in query:
                return self.role
            if "FROM location_native_delegation_inputs" in query:
                return self.question_pending
            if "FROM location_native_delegation_answers" in query:
                return self.answer_pending
            if "FROM public.memory_catalog" in query:
                return self.catalog_unknown
            if "FROM location_native_catalog_generations" in query:
                return self.catalog_pending
            if "FROM location_native_memory_artifacts" in query:
                return self.artifact_pending
            if "FROM location_native_memory_parents" in query:
                return False
            if "FROM sessions" in query:
                return self.legacy or self.late_session
            if "FROM tier2_cache" in query:
                return self.legacy or self.late_cache
            if "FROM location_native_copy_births" in query:
                return self.changed
            if "FROM location_legacy_cache_observations" in query:
                return self.legacy
            if "frontier_generation FROM" in query:
                self.trace.append("committed_readback")
                return None if self.unknown_commit else self.frontier["frontier_generation"]
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO location_retention_holder_receipts" in query:
                if "'projection_prepare'" not in query:
                    self.holders[args[2]] = {
                        "owning_butler": args[1],
                        "holder_kind": args[2],
                        "holder_generation": args[3],
                        "source_digest": args[4],
                        "receipt_id": args[5],
                    }
                    return
                self.holders["projection_prepare"] = {
                    "owning_butler": "chronicler",
                    "holder_kind": "projection_prepare",
                    "holder_generation": decision,
                    "source_digest": manifest,
                    "receipt_id": args[2],
                }
            if "INSERT INTO location_retention_frontiers " in query:
                self.trace.append("seal")
                self.frontier = {
                    "manifest_digest": args[1],
                    "frontier_generation": args[2],
                    "producer_contract": 1,
                    "expected_count": args[3],
                }
            if "INSERT INTO location_retention_frontier_holders" in query:
                self.expected.append(
                    {
                        "owning_butler": args[1],
                        "holder_kind": args[2],
                        "holder_generation": args[3],
                        "source_digest": args[4],
                    }
                )
            assert "DELETE FROM" not in query

    pool = Pool()
    pool.question_pending = False
    service._copy_pools.add(pool)  # A software registry double, not enrollment evidence.
    try:
        missing = pool.holders.pop("api_server")
        assert await service.seal_native_frontier(pool, decision) is None
        assert pool.frontier is None and pool.raw and pool.points
        pool.holders["api_server"] = missing
        pool.legacy = True
        assert await service.seal_native_frontier(pool, decision) is None
        assert pool.frontier is None and pool.raw and pool.points
        pool.legacy = False
        pool.changed = True
        with pytest.raises(service.PolicyUnavailableError, match="inventory is incomplete"):
            await service.seal_native_frontier(pool, decision)
        assert pool.frontier is None and pool.raw and pool.points
        pool.changed = False
        for flag in ("catalog_unknown", "catalog_pending", "artifact_pending"):
            setattr(pool, flag, True)
            with pytest.raises(service.PolicyUnavailableError, match="inventory is incomplete"):
                await service.seal_native_frontier(pool, decision)
            assert pool.frontier is None and pool.raw and pool.points
            setattr(pool, flag, False)
        pool.catalog = [
            {
                "owning_butler": "chronicler",
                "holder_kind": "memory_artifact",
                "holder_generation": uuid4(),
                "source_digest": b"a" * 32,
                "receipt_id": None,
            }
        ]
        assert await service.seal_native_frontier(pool, decision) is None
        assert pool.frontier is None and pool.raw and pool.points
        pool.catalog[0]["receipt_id"] = uuid4()
        pool.question_pending = True  # Actual outstanding question holder, not a terminal status.
        assert await service.seal_native_frontier(pool, decision) is None
        assert pool.frontier is None and pool.raw and pool.points
        pool.question_pending = False
        pool.answer_pending = True
        assert await service.seal_native_frontier(pool, decision) is None
        assert pool.frontier is None and pool.raw and pool.points
        pool.answer_pending = False
        for field in ("filtered_missing", "filtered_partial"):
            setattr(pool, field, True)
            assert await service.seal_native_frontier(pool, decision) is None
            assert pool.frontier is None and pool.raw and pool.points
            setattr(pool, field, False)
        pool.filtered_changed = True
        with pytest.raises(service.PolicyUnavailableError, match="copy preparation differs"):
            await service.seal_native_frontier(pool, decision)
        assert pool.frontier is None and pool.raw and pool.points
        pool.filtered_changed = False
        pool.trace.clear()
        sealed = await service.seal_native_frontier(pool, decision)
        assert sealed == pool.frontier["frontier_generation"]
        assert len(pool.expected) == 4 and {h["holder_kind"] for h in pool.expected} == {
            "switchboard_skipped",
            "api_server",
            "projection_prepare",
            "memory_artifact",
        }
        assert pool.trace.index("policy") < pool.trace.index("seal") < pool.trace.index("commit")
        assert pool.trace.index("commit") < pool.trace.index("committed_readback")
        assert pool.raw == ["planted-exact-raw"] and pool.points == ["planted-point-body"]
        # Actual unknown own bodies can arrive after the immutable snapshot.
        # Reuse must census them again, preserving the original sealed cohort.
        for field in ("late_session", "late_cache"):
            setattr(pool, field, True)
            assert await service.seal_native_frontier(pool, decision) is None
            assert pool.frontier["frontier_generation"] == sealed
            assert pool.raw == ["planted-exact-raw"] and pool.points == ["planted-point-body"]
            setattr(pool, field, False)
            assert await service.seal_native_frontier(pool, decision) == sealed
        # This frontier producer itself has no deletion side effect. The real
        # role/point/raw action and separate readbacks are distinct controls.
        pool.question_pending = True
        assert await service.seal_native_frontier(pool, decision) is None
        pool.question_pending = False
        # A genuinely later loan cannot be hidden by the committed old seal.
        pool.catalog.append(
            {
                "owning_butler": "finance",
                "holder_kind": "catalog_consumer",
                "holder_generation": uuid4(),
                "source_digest": b"l" * 32,
                "receipt_id": uuid4(),
            }
        )
        assert await service.seal_native_frontier(pool, decision) is None
        pool.catalog.pop()
        pool.changed = True
        assert await service.seal_native_frontier(pool, decision) is None
        pool.role = "other_role"
        with pytest.raises(service.PolicyUnavailableError, match="identity differs"):
            await service.seal_native_frontier(pool, decision)
    finally:
        service._copy_pools.discard(pool)


async def test_native_session_api_export_binds_actual_parents_and_refuses_unknown_commit():
    """REQ-location-retention-005; planted native body, software-only SQL double."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler import location_retention
    from butlers.chronicler.location_export_lifetime import (
        _current_location_export,
        _LocationExportScope,
    )
    from butlers.chronicler.location_session_exports import capture_session_rows

    class Pool:
        def __init__(self):
            self.trace = []
            self.births = []
            self.closed = False
            self.unknown = False
            self.native = True
            self.row = {"id": uuid4(), "result": "planted-owning-native-result"}

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            yield
            self.trace.append("commit")

        async def execute(self, query, *args):
            if "INSERT INTO location_native_copy_births" in query:
                self.births.append(args)
            else:
                self.trace.append("policy_lock")

        async def fetchrow(self, query, *args):
            assert "location_retention_policy" in query
            return {"version": 1}

        async def fetchval(self, query, *args):
            if "current_schema" in query:
                return "chronicler"
            if "location_retention_plans" in query:
                return self.closed
            self.trace.append("readback")
            return 0 if self.unknown else 1

        async def fetch(self, query, *args):
            if "location_native_copy_births" in query:
                return (
                    [{"output_kind": "point_event", "output_id": self.row["id"], "known": True}]
                    if self.native
                    else []
                )
            self.trace.append("body_read")
            return [self.row]

    pool = Pool()
    export = _LocationExportScope()
    token = _current_location_export.set(export)
    location_retention._api_copy_pools.add(pool)
    try:
        rows = await capture_session_rows(pool, "SELECT id,result FROM sessions", ())
        assert rows[0]["result"] == "planted-owning-native-result"
        assert len(pool.births) == len(export.copies) == 1
        assert pool.trace.index("policy_lock") < pool.trace.index("body_read")
        assert pool.trace.index("commit") < pool.trace.index("readback")
        assert pool.births[0][-1] == export.request_id
        pool.unknown = True
        with pytest.raises(location_retention.PolicyUnavailableError, match="birth is unknown"):
            await capture_session_rows(pool, "SELECT id,result FROM sessions", ())
        assert len(export.copies) == 1
        pool.unknown = False
        pool.closed = True
        with pytest.raises(location_retention.PolicyUnavailableError, match="fenced"):
            await capture_session_rows(pool, "SELECT id,result FROM sessions", ())
        pool.closed = False
        pool.native = False
        before = len(pool.births)
        assert await capture_session_rows(pool, "SELECT id,result FROM sessions", ()) == [pool.row]
        assert len(pool.births) == before  # Legacy row existence never mints lineage.
        export.active = False
        with pytest.raises(location_retention.PolicyUnavailableError, match="lifetime"):
            await capture_session_rows(pool, "SELECT id,result FROM sessions", ())
        # The real Chronicler enrichment must send SQL text through the actual
        # native adapter, then bind the planted process-log body to its export.
        from butlers.api.models.session import SessionDetail
        from butlers.api.routers.sessions import _attach_session_extras

        export.active, pool.native = True, True
        original_fetch = pool.fetch
        observed_log_queries = []

        async def log_fetch(sql, *args):
            assert isinstance(sql, str)
            if "FROM session_process_logs" in sql:
                observed_log_queries.append(sql)
                return [{"pid": 43, "runtime_type": "codex", "exit_code": 0}]
            return await original_fetch(sql, *args)

        pool.fetch = log_fetch
        detail = SessionDetail(
            id=pool.row["id"],
            butler="chronicler",
            prompt="native",
            trigger_source="api",
            started_at=datetime.now(UTC),
        )
        before = len(pool.births)
        await _attach_session_extras(detail, pool, pool.row["id"])
        assert observed_log_queries and detail.process_log is not None
        assert detail.process_log.pid == 43
        assert len(pool.births) == before + 1
    finally:
        location_retention._api_copy_pools.discard(pool)
        _current_location_export.reset(token)


async def test_native_memory_writer_reserves_before_embedding_and_commits_exact_body(monkeypatch):
    """REQ-location-retention-005; real storage writer, software-only SQL profile.

    Constructor registry and authority stamping are doubles. This proves the
    producer/writer causal wiring only, never PostgreSQL, role or admission.
    """
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler import location_memory_copies as copies
    from butlers.chronicler import location_retention
    from butlers.modules.memory import storage

    source, parent = uuid4(), uuid4()
    content = "planted-native-session-summary"

    class Pool:
        def __init__(self):
            self.trace = []
            self.reservations = {}
            self.parents = []
            self.episodes = {}
            self.bindings = {}
            self.unknown = False
            self.disposed = False
            self.source_content = content

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            yield
            self.trace.append("commit")

        async def fetch(self, query, *args):
            if "location_native_copy_births" in query:
                return [{"copy_generation": parent, "input_digest": b"p" * 32}]
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "location_retention_policy" in query:
                self.trace.append("policy")
                return {"version": 1}
            if "FROM chronicler.sessions" in query:
                return {
                    "result": self.source_content,
                    "completed_at": datetime.now(UTC),
                    "success": True,
                }
            if "SELECT id FROM episodes" in query:
                return None
            if "SELECT * FROM episodes" in query:
                return self.episodes.get(args[0])
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "current_schema" in query:
                return "chronicler_mem"
            if "current_user" in query:
                return "existing-memory-owner"
            if "location_native_copy_dispositions" in query:
                return self.disposed
            if "location_native_memory_parents" in query:
                self.trace.append("reservation_readback")
                return 0 if self.unknown else len(self.parents)
            if "location_native_memory_commits" in query:
                self.trace.append("body_readback")
                return None if self.unknown else self.bindings[args[0]][1]
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO chronicler.location_native_memory_reservations" in query:
                self.trace.append("reservation")
                self.reservations[args[0]] = args
            elif "INSERT INTO chronicler.location_native_memory_parents" in query:
                self.parents.append(args)
            elif "INSERT INTO episodes" in query:
                self.trace.append("body_insert")
                self.episodes[args[0]] = {"id": args[0], "session_id": args[2], "content": args[3]}
            elif "INSERT INTO chronicler.location_native_memory_commits" in query:
                self.trace.append("body_binding")
                self.bindings[args[0]] = args
            else:
                assert "pg_advisory_xact_lock" in query

    pool = Pool()
    location_retention._copy_pools.add(pool)
    copies._receivers[pool] = (pool, "chronicler_mem", "existing-memory-owner")

    async def authority(*args, **kwargs):
        return SimpleNamespace(authority="owner", entity_id=None)

    async def ttl(*args, **kwargs):
        return 7

    monkeypatch.setattr(storage, "_stamp_authority", authority)
    monkeypatch.setattr(storage, "_lookup_episode_ttl_days", ttl)

    class Engine:
        model_name = "software-vector-double"

        def embed(self, actual_content):
            assert actual_content == content
            assert pool.trace.index("commit") < pool.trace.index("reservation_readback")
            pool.trace.append("embedding")
            return [0.1, 0.2]

    try:
        async with copies.capture_memory_episode(pool, pool, source, content):
            episode = await storage.store_episode(
                pool, content, "chronicler", Engine(), session_id=source
            )
        assert pool.bindings and pool.episodes[episode]["content"] == content
        original = dict(pool.episodes[episode])
        frozen = copies.episode_body_digest(original)
        advanced = {
            **original,
            "reference_count": 19,
            "consolidated": True,
            "consolidation_status": "consolidated",
            "leased_until": None,
        }
        assert copies.episode_body_digest(advanced) == frozen
        assert (
            copies.episode_body_digest({**advanced, "content": "different-location-body"}) != frozen
        )
        assert copies.episode_body_digest({**advanced, "metadata": {"location": [1, 2]}}) != frozen
        assert (
            copies.episode_body_digest({**advanced, "content_authority": "third_party"}) != frozen
        )
        assert pool.trace.index("reservation_readback") < pool.trace.index("embedding")
        assert pool.trace.index("policy", pool.trace.index("embedding")) < pool.trace.index(
            "body_insert"
        )
        assert (
            pool.trace.index("body_insert")
            < pool.trace.index("body_binding")
            < pool.trace.index("body_readback")
        )
        assert copies._current_memory_copy.get() is None
        pool.source_content = "different-current-source-result"
        before = len(pool.episodes)
        with pytest.raises(location_retention.PolicyUnavailableError, match="source output"):
            async with copies.capture_memory_episode(pool, pool, source, content):
                raise AssertionError("A changed source must refuse before embedding")
        assert len(pool.episodes) == before
        pool.source_content = content
        pool.disposed = True
        with pytest.raises(location_retention.PolicyUnavailableError, match="disposed"):
            async with copies.capture_memory_episode(pool, pool, source, content):
                raise AssertionError("A disposed source must refuse before embedding")
        pool.disposed = False
        pool.unknown = True
        with pytest.raises(
            location_retention.PolicyUnavailableError, match="reservation is unknown"
        ):
            async with copies.capture_memory_episode(pool, pool, source, content):
                raise AssertionError("An unobserved reservation must refuse before embedding")
        assert copies._current_memory_copy.get() is None
    finally:
        copies._receivers.pop(pool, None)
        location_retention._copy_pools.discard(pool)

    await _assert_native_episode_tool_reads()
    await _assert_native_artifact_invocation_lifetime()
    await _assert_native_memory_mutation_versions()


async def _assert_native_episode_tool_reads():
    """Real get/read entry; constructor/DB doubles, no role or SQL credit."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler import location_memory_copies as copies
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import _current_tool_copy, _ToolCopy
    from butlers.modules.memory.storage import get_memory

    class Memory:
        def __init__(self):
            self.trace = []
            self.births = []
            self.parents = True
            self.changed = False
            self.fenced = False
            self.unknown = False
            self.row = {"id": uuid4(), "content": "own native episode", "reference_count": 0}
            self.original = copies.episode_body_digest(self.row)
            self.artifact = None
            self.artifact_exclusive = True
            self.artifact_partial = None
            self.extra = {}
            self.plan_unknown = False
            self.mutations = []

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.trace.append("begin")
            yield
            self.trace.append("commit")

        async def fetchrow(self, sql, *args):
            if "FROM chronicler.location_native_memory_artifacts" in sql:
                return (
                    self.artifact
                    if self.artifact and "artifact_generation" in self.artifact
                    else None
                )
            if "SELECT consolidation_status" in sql:
                return {"consolidation_status": "dead_letter"}
            if "SELECT validity" in sql:
                return {"validity": "active"}
            if "SELECT * FROM" in sql or "SELECT id FROM" in sql:
                self.trace.append("canonical-lock")
                return self.row
            assert "location_retention_policy" in sql
            self.trace.append("policy")
            return {"version": 1}

        async def fetchval(self, sql, *args):
            if "current_schema" in sql:
                return "chronicler_mem"
            if "current_user" in sql:
                return "actual-owner-double"
            if "location_retention_plans" in sql or "location_retention_plan_outputs" in sql:
                if self.plan_unknown:
                    raise RuntimeError("fixed unavailable plan read double")
                return self.fenced
            if "count(*) FROM chronicler.location_native_copy_births" in sql:
                self.trace.append("readback")
                return 0 if self.unknown else 1
            raise AssertionError(sql)

        async def execute(self, sql, *args):
            if "INSERT INTO chronicler.location_native_copy_births" in sql:
                self.trace.append("birth")
                self.births.append(args)
            elif sql.startswith("UPDATE "):
                self.trace.append("mutation")
                self.mutations.append((sql, args))
                return "UPDATE 1"

        async def fetch(self, sql, *args):
            if "FROM chronicler.location_native_memory_artifacts" in sql:
                if self.artifact is None:
                    return []
                rows = [
                    {
                        **self.artifact,
                        "output_kind": "point_event",
                        "output_id": self.row["id"],
                        "copy_generation": self.row["id"],
                        "input_digest": b"p" * 32,
                        "birth_digest": b"p" * 32,
                        "parent_count": 1,
                        "lineage_known": self.artifact_exclusive,
                        "exclusive_input": self.artifact_exclusive,
                    }
                ]
                if self.artifact_partial is not None:
                    rows[0]["parent_count"] = 2
                    second = {**rows[0], "copy_generation": sibling_parent}
                    if self.artifact_partial == "missing":
                        second["output_id"], second["birth_digest"] = None, None
                    elif self.artifact_partial == "digest":
                        second["birth_digest"] = b"x" * 32
                    rows.append(second)
                    if self.artifact_partial == "extra":
                        rows.append({**second, "copy_generation": uuid4()})
                    if self.artifact_partial == "empty":
                        rows = [
                            {
                                **rows[0],
                                "copy_generation": None,
                                "input_digest": None,
                                "output_id": None,
                                "birth_digest": None,
                            }
                        ]
                    if "LEFT JOIN" not in sql:
                        rows = [
                            r
                            for r in rows
                            if r["output_id"] is not None and r["birth_digest"] == r["input_digest"]
                        ]
                return rows
            if "FROM chronicler.location_native_memory_commits" in sql:
                return (
                    [
                        {
                            "output_kind": "point_event",
                            "output_id": self.row["id"],
                            "copy_generation": self.row["id"],
                            "input_digest": b"p" * 32,
                            "birth_digest": b"p" * 32,
                            "parent_count": 1,
                            "lineage_known": True,
                            "exclusive_input": True,
                            "body_digest": self.original,
                        }
                    ]
                    if self.parents
                    else []
                )
            self.trace.append("body_read")
            if sql.startswith("UPDATE "):
                self.row["reference_count"] += 1
            return (
                [{**self.row, **self.extra, "content": "changed independent body"}]
                if self.changed
                else [{**self.row, **self.extra}]
            )

    sibling_parent = uuid4()
    domain, memory = object(), Memory()
    runtime = SimpleNamespace(domain=domain, memory=memory, active=True)
    copies._receivers[domain] = (memory, "chronicler_mem", "actual-owner-double")
    _runtimes[domain] = runtime
    tool = _ToolCopy(runtime, uuid4(), uuid4(), "memory_get", "memory")
    token = _current_tool_copy.set(tool)
    try:
        result = await get_memory(
            memory, "episode", memory.row["id"], allowed_sensitivities=["normal"]
        )
        assert result["reference_count"] == 1
        assert tool.read_observed and not tool.mixed_inputs
        assert memory.births[-1][4] is True and memory.births[-1][5] == tool.session
        assert memory.trace.index("policy") < memory.trace.index("body_read")
        assert (
            memory.trace.index("birth")
            < memory.trace.index("commit")
            < memory.trace.index("readback")
        )
        memory.changed = True
        await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes")
        assert tool.mixed_inputs and memory.births[-1][4] is False
        memory.changed = False
        tool.mixed_inputs = False
        memory.parents = False
        await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes")
        assert tool.mixed_inputs  # A row with no native parent is independent, not exclusive.
        memory.parents = True
        tool.mixed_inputs = False
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert tool.mixed_inputs  # Unknown fact ancestry cannot borrow episode authority.
        from butlers.chronicler.location_projection import _digest_value

        original_full = content_digest({"memory_artifact": _digest_value(memory.row)})
        memory.artifact = {
            "memory_table": "facts",
            "body_digest": original_full,
            "content_digest": copies.artifact_content_digest("facts", memory.row),
        }
        tool.mixed_inputs = False
        await get_memory(memory, "fact", memory.row["id"], allowed_sensitivities=["normal"])
        assert tool.read_observed and not tool.mixed_inputs
        assert memory.artifact["body_digest"] == original_full  # History never refreshed.
        assert memory.births[-1][4] is True and memory.births[-1][5] == tool.session
        memory.artifact_partial = "complete"
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert not tool.mixed_inputs and memory.births[-1][4] is True
        for partial in ("missing", "digest", "extra", "empty"):
            memory.artifact_partial = partial
            before = len(memory.births)
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                try:
                    await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
                finally:
                    assert len(memory.births) == before  # Refuse before any copied input birth.
            assert len(memory.births) == before
        memory.artifact_partial = None
        memory.extra = {"rank": 0.75, "similarity": 1.0}
        tool.mixed_inputs = False
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert not tool.mixed_inputs  # Actual numeric search scores preserve the body.
        for extra in (
            {"rank": "private copied prose"},
            {"rank": True},
            {"unregistered": "private copied prose"},
        ):
            memory.extra = extra
            tool.mixed_inputs = False
            await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
            assert tool.mixed_inputs and memory.births[-1][4] is False
        memory.extra = {"rank": float("nan")}
        before = len(memory.births)
        with pytest.raises(ValueError, match="JSON compliant"):
            await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert len(memory.births) == before  # Invalid score cannot emit an admitted copy.
        memory.extra = {}
        memory.artifact["content_digest"] = None
        tool.mixed_inputs = False
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert tool.mixed_inputs  # Legacy NULL is not refilled from the current row.
        memory.artifact["content_digest"] = copies.artifact_content_digest("facts", memory.row)
        tool.mixed_inputs = False
        memory.row["content"] = "independent modified fact"
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert tool.mixed_inputs and memory.births[-1][4] is False
        memory.row["content"] = "own native episode"
        memory.artifact_exclusive = False
        tool.mixed_inputs = False
        await copies.capture_memory_rows(memory, "facts", "SELECT * FROM facts")
        assert tool.mixed_inputs
        memory.artifact_exclusive = True
        memory.artifact = {
            "memory_table": "rules",
            "body_digest": original_full,
            "content_digest": copies.artifact_content_digest("rules", memory.row),
        }
        tool.mixed_inputs = False
        await get_memory(memory, "rule", memory.row["id"], allowed_sensitivities=["normal"])
        assert not tool.mixed_inputs
        from butlers.chronicler.location_memory_context import (
            _current_runtime_context,
            _RuntimeContext,
            observe_context_rows,
        )

        context = _RuntimeContext(runtime, uuid4(), tool.session, True)
        context_token = _current_runtime_context.set(context)
        try:
            await copies.capture_memory_rows(memory, "rules", "SELECT * FROM rules")
            observe_context_rows(memory, "rules", [memory.row])
            assert context.known_context and ("rules", memory.row["id"]) in context.local_rows
            memory.artifact_exclusive = False
            await copies.capture_memory_rows(memory, "rules", "SELECT * FROM rules")
            observe_context_rows(memory, "rules", [memory.row])
            assert (
                context.known_context is False
            )  # A matching UUID never blesses mixed body ancestry.
        finally:
            _current_runtime_context.reset(context_token)
            memory.artifact_exclusive = True
        from butlers.modules.memory.storage import confirm_memory

        memory.trace.clear()
        assert await confirm_memory(
            memory, "fact", memory.row["id"], memory_schema="chronicler_mem"
        )
        assert memory.trace.index("policy") < memory.trace.index("canonical-lock")
        assert memory.trace.index("canonical-lock") < memory.trace.index("mutation")
        assert memory.trace.index("mutation") < memory.trace.index("commit")
        before_mutations = len(memory.mutations)
        with pytest.raises(copies.PolicyUnavailableError, match="schema differs"):
            await confirm_memory(memory, "fact", memory.row["id"], memory_schema="another_mem")
        memory.plan_unknown = True
        with pytest.raises(RuntimeError, match="unavailable plan"):
            await confirm_memory(memory, "rule", memory.row["id"])
        memory.plan_unknown = False
        memory.fenced = True
        with pytest.raises(copies.PolicyUnavailableError, match="prepared"):
            await confirm_memory(memory, "rule", memory.row["id"])
        from butlers.modules.memory import storage

        prepared_mutations = (
            lambda: storage.forget_memory(memory, "fact", memory.row["id"]),
            lambda: storage.forget_memory(
                memory, "fact", memory.row["id"], correction_id=str(uuid4())
            ),
            lambda: storage.retry_dead_letter_episode(memory, memory.row["id"]),
            lambda: storage.retire_rule(memory, memory.row["id"]),
            lambda: storage.endorse_rule(memory, memory.row["id"], endorsed_by=None),
            lambda: storage.mark_helpful(memory, memory.row["id"]),
            lambda: storage.mark_harmful(memory, memory.row["id"], reason="independent feedback"),
        )
        for mutation in prepared_mutations:
            with pytest.raises(copies.PolicyUnavailableError, match="prepared"):
                await mutation()
        assert len(memory.mutations) == before_mutations  # No write precedes the current fence.
        before = len(memory.births)
        with pytest.raises(copies.PolicyUnavailableError, match="fenced"):
            await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes")
        assert len(memory.births) == before
        memory.fenced = False
        memory.unknown = True
        tool.read_observed = False
        with pytest.raises(copies.PolicyUnavailableError, match="birth is unknown"):
            await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes")
        assert tool.read_observed is False
    finally:
        _current_tool_copy.reset(token)
        _runtimes.pop(domain)
        copies._receivers.pop(domain)


async def _assert_native_artifact_invocation_lifetime():
    """Actual producer entry paths; registry/SQL doubles, no auth/SQL credit."""
    import time
    from contextlib import asynccontextmanager
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler import location_memory_context as contexts
    from butlers.chronicler import location_memory_copies as copies
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.core.copy_lifetime import _current_copy_invocation
    from butlers.core.fact_authority import _Invocation, _invocations

    generation, session = uuid4(), uuid4()

    class Memory:
        def __init__(self):
            self.trace = []

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        def is_closed(self):
            return False

        async def fetchval(self, sql, *args):
            if "current_schema" in sql:
                return "chronicler_mem"
            if "current_user" in sql:
                return "existing-owning-role-double"
            return False

        async def fetchrow(self, sql, *args):
            self.trace.append("writer")
            if "location_retention_policy" in sql:
                return {"version": 1}
            assert "location_runtime_context_bindings" in sql
            assert args == (session,)
            return {"input_generation": generation}

        async def fetch(self, sql, *args):
            self.trace.append("selected")
            return []

        async def execute(self, sql, *args):
            assert "pg_advisory_xact_lock" in sql

    memory, domain = Memory(), object()
    runtime = SimpleNamespace(
        name="chronicler",
        memory=memory,
        domain=domain,
        active=True,
        memory_identity=("chronicler_mem", "existing-owning-role-double"),
        identity=("chronicler", "existing-owning-role-double"),
    )
    original = _Invocation("chronicler", str(session), None, None, time.monotonic() + 60)
    key = str(uuid4())
    _runtimes[memory] = _runtimes[domain] = runtime
    copies._receivers[domain] = (memory, *runtime.memory_identity)
    token = _current_copy_invocation.set(original)
    try:

        async def refused(cell):
            previous = _current_copy_invocation.set(cell)
            before = list(memory.trace)
            try:
                with pytest.raises(copies.PolicyUnavailableError, match="invocation differs"):
                    await contexts.context_artifact_scope(memory, memory)
                with pytest.raises(copies.PolicyUnavailableError, match="invocation differs"):
                    await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes")
                assert memory.trace == before  # Neither writer nor selected body reached.
            finally:
                _current_copy_invocation.reset(previous)

        await refused(original)  # Typed fields without registry identity are not proof.
        _invocations[key] = original
        accepted = await contexts.context_artifact_scope(memory, memory)
        assert accepted is not None and accepted.generation == generation
        assert accepted.runtime is runtime and accepted.connection is memory
        assert await copies.capture_memory_rows(memory, "episodes", "SELECT * FROM episodes") == []
        assert "writer" in memory.trace and "selected" in memory.trace
        await refused(replace(original))  # Equal copied cell is still a distinct object.
        for altered in (
            replace(original, deadline=time.monotonic() - 1),
            replace(original, target="relationship"),
        ):
            _invocations[key] = altered
            await refused(altered)
        _invocations[key] = original
        assert await contexts.context_artifact_scope(memory, memory) is not None
        _invocations.pop(key)
        await refused(original)  # Native finalizer removal also revokes the producer cell.
    finally:
        _current_copy_invocation.reset(token)
        _invocations.pop(key, None)
        copies._receivers.pop(domain)
        _runtimes.pop(memory)
        _runtimes.pop(domain)


@pytest.mark.asyncio
async def test_catalog_native_admission_precedes_delegate_and_server_lifetime_is_bounded(
    caplog, monkeypatch
):
    """REQ-location-retention-005/006; software transport/control positioning, not SQL proof."""
    import json
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler import location_catalog_copies as copies

    loan = uuid4()
    runtime = SimpleNamespace()
    admissions = []
    observed = []
    cell = None

    async def admit(selected, capability):
        nonlocal cell
        assert selected == loan
        assert capability == "native-private-capability-0123456789"
        admissions.append(selected)
        cell = copies._AdmittedLoan(runtime, capability, selected, {})
        return cell

    runtime.admit_loan = admit

    async def delegate(scope, receive, send):
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            if not message.get("more_body", False):
                break
        observed.append((scope, messages, copies._admitted_loan.get()))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ordinary", "more_body": False})

    app = copies.CatalogLoanAdmission(delegate, lambda: runtime)

    async def request(raw, headers=None, frames=None):
        sent = []
        chunks = iter(frames or [{"type": "http.request", "body": raw, "more_body": False}])

        async def receive():
            return next(chunks)

        async def send(message):
            sent.append(message)

        await app(
            {"type": "http", "method": "POST", "path": "/mcp", "headers": headers or []},
            receive,
            send,
        )
        return sent

    # Genuine generic-positive streaming is untouched, including a body beyond
    # the reserved native profile. It receives no private authority cell.
    generic = b"g" * 300000
    assert (await request(generic))[0]["status"] == 200
    assert observed[-1][1][0]["body"] == generic and observed[-1][2] is None
    headers = [(copies._HEADER.lower().encode(), b"native-private-capability-0123456789")]
    packet = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "location_catalog_loan_body", "arguments": {"loan_id": str(loan)}},
        }
    ).encode()
    assert (await request(packet, headers))[0]["status"] == 200
    assert admissions == [loan]
    assert observed[-1][2] is cell
    assert observed[-1][1][0]["body"] == packet
    assert all(
        name.lower() != copies._HEADER.lower().encode() for name, _ in observed[-1][0]["headers"]
    )
    assert not cell.active and copies._admitted_loan.get() is None

    # Measure the actual private buffer's copied size, rather than merely
    # asserting a 503 that also passed when rejection came after allocation.
    # Builtin instrumentation is available in both old and current source;
    # it injects no admitted context or verifier decision.
    peaks = []

    class RecordingBuffer(bytearray):
        def extend(self, chunk):
            super().extend(chunk)
            peaks.append(len(self))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(copies, "bytearray", RecordingBuffer, raising=False)
        before = len(observed)
        assert (await request(b"x" * 262145, headers))[0]["status"] == 503
        assert len(observed) == before
        assert max(peaks, default=0) <= 262144
        peaks.clear()
        assert (
            await request(
                b"",
                headers,
                frames=[
                    {"type": "http.request", "body": b"x" * 262140, "more_body": True},
                    {"type": "http.request", "body": b"y" * 5, "more_body": False},
                ],
            )
        )[0]["status"] == 503
        assert max(peaks) <= 262144 and len(observed) == before
        peaks.clear()
        assert (await request(packet, headers))[0]["status"] == 200
        assert peaks == [len(packet)] and observed[-1][2] is cell
        assert not cell.active and copies._admitted_loan.get() is None
    # Restore this case's admission-count checkpoint after proving its valid
    # companion; the original malformed-request cases below retain their
    # original independent admission assertions.
    assert admissions == [loan, loan]
    admissions.pop()

    # Position duplicate-key rejection before the delegate, not at a later
    # FunctionTool which would already have observed/normalized the request.
    before = len(observed)
    duplicate = packet.replace(b'"loan_id":', b'"loan_id":"forged", "loan_id":')
    assert (await request(duplicate, headers))[0]["status"] == 503
    assert len(observed) == before and admissions == [loan]
    assert (await request(packet, headers + headers))[0]["status"] == 503
    assert (await request(b"x" * 262145, headers))[0]["status"] == 503
    empty_frames = [{"type": "http.request", "body": b"", "more_body": True}] * 129
    assert (await request(b"", headers, empty_frames))[0]["status"] == 503
    assert len(observed) == before

    # Actual Switchboard outer admission is distinct from source admission.
    # The fake online verifier is scoped software evidence only; the declared
    # registered daemon/network and real-role controls are not inferred here.
    forwarded = []

    async def admit_route(selected, capability):
        assert selected == loan
        if capability != "native-private-capability-0123456789":
            raise copies.PolicyUnavailableError("Online receiver differs")
        return copies._AdmittedRoute(capability, selected)

    async def router_delegate(scope, receive, send):
        message = await receive()
        request = json.loads(message["body"])
        args = request["params"]["arguments"]
        forwarded.append(
            copies.catalog_transport_headers(
                args["target_butler"], args["tool_name"], {**args["args"], "trace_context": {}}
            )
        )
        with pytest.raises(copies.PolicyUnavailableError, match="operation differs"):
            copies.catalog_transport_headers("finance", args["tool_name"], args["args"])
        with pytest.raises(copies.PolicyUnavailableError, match="operation differs"):
            copies.catalog_transport_headers(
                "chronicler", args["tool_name"], {"loan_id": str(uuid4())}
            )
        assert copies._admitted_loan.get() is None
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"route", "more_body": False})

    runtime.admit_route = admit_route
    app = copies.CatalogLoanAdmission(router_delegate, lambda: runtime)
    routed = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "route",
                "arguments": {
                    "target_butler": "chronicler",
                    "tool_name": "location_catalog_loan_body",
                    "args": {"loan_id": str(loan)},
                },
            },
        }
    ).encode()
    assert (await request(routed, headers))[0]["status"] == 200
    assert forwarded == [{copies._HEADER: "native-private-capability-0123456789"}]
    assert copies._admitted_route.get() is None
    assert (
        copies.catalog_transport_headers(
            "chronicler", "location_catalog_loan_body", {"loan_id": str(loan)}
        )
        == {}
    )
    assert (await request(routed.replace(b'"chronicler"', b'"finance"'), headers))[0][
        "status"
    ] == 503
    assert (
        await request(
            routed, [(copies._HEADER.lower().encode(), b"caller-forged-header-012345678901234567")]
        )
    )[0]["status"] == 503
    assert len(forwarded) == 1

    # UUID-only direct invocation cannot borrow the native cell after return.
    with pytest.raises(copies.PolicyUnavailableError, match="admission"):
        await copies.CatalogCopyRuntime.loan_body(runtime, loan)

    # Actual outer challenge failure retains refusal while recording only the
    # installed exception class/SQLSTATE and source-owned stage.
    import asyncpg

    async def denied_admit(selected, capability):
        raise asyncpg.InsufficientPrivilegeError("synthetic-private-argument-do-not-log")

    runtime.admit_loan = denied_admit
    app = copies.CatalogLoanAdmission(delegate, lambda: runtime)
    caplog.clear()
    assert (await request(packet, headers))[0]["status"] == 503
    assert (
        "stage=loan_challenge category=postgres sqlstate=42501 class=insufficient_privilege"
        in caplog.text
    )
    assert "synthetic-private-argument-do-not-log" not in caplog.text
    assert "native-private-capability-0123456789" not in caplog.text
    runtime.admit_loan = admit
    caplog.clear()
    assert (await request(packet, headers))[0]["status"] == 200
    assert "Location catalog failure" not in caplog.text

    finished = []

    async def finish(selected, digest, owning_request):
        finished.append((selected, digest, owning_request))

    receiver = SimpleNamespace(finish_server_copy=finish)

    async def copying_delegate(scope, receive, send):
        state = copies._server_copy_scope.get()
        state.loans.append((receiver, loan, b"d" * 32, False))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"copy", "more_body": False})
        if scope.get("interrupted"):
            raise RuntimeError("interrupted after send")

    lifecycle = copies.CatalogServerCopyLifetime(copying_delegate)

    async def no_receive():
        return {"type": "http.disconnect"}

    async def delivered(message):
        pass

    await lifecycle({"type": "http"}, no_receive, delivered)
    assert len(finished) == 1 and finished[0][:2] == (loan, b"d" * 32)
    assert copies._server_copy_scope.get() is None
    with pytest.raises(RuntimeError, match="interrupted"):
        await lifecycle({"type": "http", "interrupted": True}, no_receive, delivered)
    assert len(finished) == 1  # Last body alone cannot close a live/failed delegate.

    # Stateful MCP executes Tools in its session task. The SDK's actual Request
    # carries only cells placed by the live fixed ASGI constructor.
    import asyncio
    from contextvars import Context

    from starlette.requests import Request

    from butlers.mcp_wrappers import _SpanWrappingMCP, _ToolCallLoggingMCP

    runtime.name, runtime.active = "chronicler", True
    captured_request = None
    restored = []
    monkeypatch.setattr("fastmcp.server.dependencies.get_http_request", lambda: captured_request)
    for wrapper in (_SpanWrappingMCP, _ToolCallLoggingMCP):
        proxy = wrapper(
            SimpleNamespace(tool=lambda *a, **kw: lambda f: f), "chronicler", module_name="memory"
        )

        @proxy.tool(name="location_catalog_loan_body")
        async def carried(loan_id):
            native = copies._admitted_loan.get()
            server = copies._server_copy_scope.get()
            if native is None:
                raise copies.PolicyUnavailableError("Native loan admission is unavailable")
            assert native.runtime is runtime and native.loan == loan_id and native.active
            assert server is not None and server.active and server.target == "chronicler"
            return {"valid": True}

        async def isolated(scope, receive, send):
            nonlocal captured_request
            captured_request = Request(scope)
            assert not any(
                name.lower() == copies._HEADER.lower().encode() for name, _ in scope["headers"]
            )

            async def invoke():
                before = copies._admitted_loan.get(), copies._server_copy_scope.get()
                result = await carried(loan_id=loan)
                restored.append(
                    (before, copies._admitted_loan.get(), copies._server_copy_scope.get())
                )
                return result

            result = await asyncio.create_task(invoke(), context=Context())
            assert result == {"valid": True}
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"valid", "more_body": False})

        app = copies.CatalogServerCopyLifetime(
            copies.CatalogLoanAdmission(isolated, lambda: runtime), butler_name="chronicler"
        )
        assert (await request(packet, headers))[0]["status"] == 200
        assert restored[-1] == ((None, None), None, None)
        assert not captured_request.scope[copies._HTTP_SERVER_CELL].active
        assert not captured_request.scope[copies._HTTP_LOAN_CELL].active
        with pytest.raises(copies.PolicyUnavailableError, match="server constructor"):
            await carried(loan_id=loan)
        # Header/locator fields cannot recreate an internal Request cell.
        captured_request = Request({"type": "http", "headers": headers})
        with pytest.raises(copies.PolicyUnavailableError, match="admission"):
            await carried(loan_id=loan)

    # The fixed Switchboard route has its own verified runtime and cell; it is
    # carried per message too, not inferred from the source/loan locator.
    route_runtime = SimpleNamespace(name="switchboard", active=True)

    async def admit_route(selected, capability):
        assert selected == loan and capability == "native-private-capability-0123456789"
        return copies._AdmittedRoute(capability, selected, runtime=route_runtime)

    route_runtime.admit_route = admit_route
    route_packet = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "route",
                "arguments": {
                    "target_butler": "chronicler",
                    "tool_name": "location_catalog_loan_body",
                    "args": {"loan_id": str(loan)},
                },
            },
        }
    ).encode()
    for wrapper in (_SpanWrappingMCP, _ToolCallLoggingMCP):
        proxy = wrapper(
            SimpleNamespace(tool=lambda **kw: lambda f: f), "switchboard", module_name="switchboard"
        )

        @proxy.tool(name="route")
        async def carried_route():
            routed = copies._admitted_route.get()
            if routed is None:
                raise copies.PolicyUnavailableError("Native routed admission is unavailable")
            assert routed.runtime is route_runtime and routed.loan == loan and routed.active
            assert copies._server_copy_scope.get().target == "switchboard"
            return {"routed": True}

        async def isolated_route(scope, receive, send):
            nonlocal captured_request
            captured_request = Request(scope)
            result = await asyncio.create_task(carried_route(), context=Context())
            assert result == {"routed": True}
            assert copies._admitted_route.get().runtime is route_runtime
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"valid", "more_body": False})

        app = copies.CatalogServerCopyLifetime(
            copies.CatalogLoanAdmission(isolated_route, lambda: route_runtime),
            butler_name="switchboard",
        )
        assert (await request(route_packet, headers))[0]["status"] == 200
        assert copies._admitted_route.get() is None
        assert not captured_request.scope[copies._HTTP_ROUTE_CELL].active
        with pytest.raises(copies.PolicyUnavailableError, match="server constructor"):
            await carried_route()


@pytest.mark.asyncio
async def test_native_processing_reserves_full_reads_before_render_and_keeps_failed_readback_held():
    """REQ-location-retention-005; native software position only, not SQL authority."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler import location_memory_processing as processing
    from butlers.chronicler.location_memory_copies import _receivers
    from butlers.chronicler.location_retention import PolicyUnavailableError

    parent = uuid4()
    episode = {"id": uuid4(), "content": "source sentinel", "butler": "chronicler"}
    unrelated = {"id": uuid4(), "content": "independent preserved sentinel"}
    trace = []

    class Pool:
        readable = True
        claim = None
        digest = None
        receipts = []
        finish_error = None

        @asynccontextmanager
        async def acquire(self):
            trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            trace.append("begin")
            yield
            trace.append("commit")

        async def fetchrow(self, sql, *args):
            if "location_retention_policy" in sql:
                trace.append("policy-first")
                return {"version": 1}
            raise AssertionError("unexpected owning read")

        async def fetch(self, sql, *args):
            if "FROM facts" in sql:
                trace.append("facts-read")
                return [unrelated]
            if "FROM rules" in sql:
                return []
            if "location_native_memory_commits" in sql:
                return [{"copy_generation": parent, "input_digest": b"p" * 32}]
            if "location_native_memory_artifacts" in sql:
                return []
            raise AssertionError("unexpected owning read")

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "chronicler_mem"
            if sql == "SELECT current_user":
                return "installed-own-role-double"
            if "count(*)=c.parent_count" in sql:
                trace.append("separate-readback")
                return self.readable
            if "processing_claims" in sql:
                return True
            if "processing_finished" in sql:
                return len(self.receipts)
            return False

        async def execute(self, sql, *args):
            if "INSERT INTO chronicler.location_native_processing_claims" in sql:
                trace.append("full-bundle-reserved")
                self.claim, self.digest = args[:2]
            if "INSERT INTO chronicler.location_native_processing_finished" in sql:
                trace.append("native-processing-ended")
                if self.finish_error is not None:
                    raise self.finish_error
                self.receipts.append(args[-1])

    domain, pool = object(), Pool()
    _receivers[domain] = (pool, "chronicler_mem", "installed-own-role-double")
    try:
        async with processing.processing_lifetime(pool):
            facts, rules = await processing.read_dedup_bundle(
                pool, [episode], "chronicler", "shared"
            )
            trace.append("render")
            assert facts == [unrelated] and rules == []
            assert trace.index("policy-first") < trace.index("facts-read")
            assert trace.index("full-bundle-reserved") < trace.index("commit")
            assert trace.index("commit") < trace.index("separate-readback") < trace.index("render")
            first_digest = pool.digest
            facts, _ = await processing.read_dedup_bundle(
                pool, [{**episode, "content": "changed source sentinel"}], "chronicler", "shared"
            )
            assert pool.digest != first_digest
            pool.readable = False
            with pytest.raises(PolicyUnavailableError, match="Committed processing input"):
                await processing.read_dedup_bundle(pool, [episode], "chronicler", "shared")
        assert trace[-2:] == ["commit", "acquire"]
        assert len(pool.receipts) == 3  # Actual native scope end; not a runtime/descendant receipt.
        import asyncio

        async def unwind(primary, secondary):
            current = Pool()
            current.receipts = []
            current.finish_error = secondary
            _receivers[domain] = (current, "chronicler_mem", "installed-own-role-double")
            observed = None
            try:
                async with processing.processing_lifetime(current):
                    await processing.read_dedup_bundle(current, [episode], "chronicler", "shared")
                    if primary is not None:
                        raise primary
            except BaseException as exc:
                observed = exc
            assert processing._processing.get() is None
            return observed, current.receipts

        primary = ValueError("fixed runner failure sentinel")
        secondary = RuntimeError("fixed receipt failure sentinel")
        observed, ended = await unwind(primary, secondary)
        assert observed is primary and ended == []
        cancelled = asyncio.CancelledError("fixed runner cancellation sentinel")
        observed, ended = await unwind(cancelled, secondary)
        assert observed is cancelled and ended == []
        observed, ended = await unwind(None, secondary)
        assert observed is secondary and ended == []  # Normal return cannot hide failed witness.
        new_cancellation = asyncio.CancelledError("fixed completion cancellation sentinel")
        observed, ended = await unwind(primary, new_cancellation)
        assert observed is new_cancellation and ended == []
        observed, ended = await unwind(primary, None)
        assert observed is primary and len(ended) == 1
        observed, ended = await unwind(None, None)
        assert observed is None and len(ended) == 1  # Real ended-scope companion.
    finally:
        _receivers.pop(domain)

    await _assert_native_consolidation_full_ancestry()


async def _assert_native_consolidation_full_ancestry():
    """Actual full-bundle reader; DB double only, no SQL/role admission proof."""
    from uuid import uuid4

    from butlers.chronicler.location_memory_copies import (
        artifact_content_digest,
        episode_body_digest,
    )
    from butlers.chronicler.location_memory_derivation import _captured_bundle_parents
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import PolicyUnavailableError

    class Bundle:
        def __init__(self):
            self.rows = {
                table: {"id": uuid4(), "content": table + " source sentinel", "reference_count": 0}
                for table in ("episodes", "facts", "rules")
            }
            self.witnesses = {}
            for table, row in self.rows.items():
                self.witnesses[table] = {
                    "copy_generation": uuid4(),
                    "input_digest": b"p" * 32,
                    "birth_digest": b"p" * 32,
                    "parent_count": 1,
                    "output_id": uuid4(),
                    "lineage_known": True,
                    "exclusive_input": True,
                    "body_digest": episode_body_digest(row)
                    if table == "episodes"
                    else content_digest({"memory_artifact": _digest_value(row)}),
                    "memory_table": table,
                    "content_digest": None
                    if table == "episodes"
                    else artifact_content_digest(table, row),
                }
            self.missing = None
            self.partial = None

        async def fetchrow(self, sql, *args):
            table = sql.split(" FROM ")[1].split()[0]
            assert sql.endswith("FOR UPDATE") and args[0] == self.rows[table]["id"]
            return self.rows[table]

        async def fetch(self, sql, *args):
            table = "episodes" if "location_native_memory_commits" in sql else args[0]
            if table == self.missing:
                return []
            rows = [dict(self.witnesses[table])]
            if table == "rules" and self.partial is not None:
                rows[0]["parent_count"] = 2
                second = {**rows[0], "copy_generation": sibling, "output_id": uuid4()}
                if self.partial == "missing":
                    second["output_id"], second["birth_digest"] = None, None
                elif self.partial == "digest":
                    second["birth_digest"] = b"x" * 32
                rows.append(second)
                if self.partial == "extra":
                    rows.append({**second, "copy_generation": uuid4()})
                if self.partial == "empty":
                    rows = [
                        {
                            **rows[0],
                            "copy_generation": None,
                            "input_digest": None,
                            "output_id": None,
                            "birth_digest": None,
                        }
                    ]
                if "LEFT JOIN" not in sql:
                    rows = [
                        r
                        for r in rows
                        if r["output_id"] is not None and r["birth_digest"] == r["input_digest"]
                    ]
            return rows

    sibling = uuid4()
    bundle = Bundle()
    selected = {
        table: [{"id": row["id"], "content": row["content"]}] for table, row in bundle.rows.items()
    }

    async def capture():
        return await _captured_bundle_parents(
            bundle, selected["episodes"], selected["facts"], selected["rules"]
        )

    parents, exclusive = await capture()
    assert exclusive is True and len(parents) == 3  # All actual native source generations survive.
    bundle.partial = "complete"
    full_parents, full_exclusive = await capture()
    assert full_exclusive is True and len(full_parents) == 4
    assert sibling in {p["copy_generation"] for p in full_parents}
    for partial in ("missing", "digest", "extra", "empty"):
        bundle.partial = partial
        with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
            await capture()
    bundle.partial = "complete"
    assert (await capture()) == (full_parents, True)
    bundle.partial = None
    bundle.rows["facts"]["reference_count"] = 7
    assert (await capture())[1] is True  # Producer content-v1 permits read counters only.
    bundle.missing = "rules"
    parents, exclusive = await capture()
    assert exclusive is False and len(parents) == 2  # Independent selected rule is never blessed.
    bundle.missing = None
    bundle.witnesses["rules"]["exclusive_input"] = False
    assert (await capture())[1] is False
    bundle.witnesses["rules"]["exclusive_input"] = True
    bundle.rows["facts"]["metadata"] = {"independent": "preserved"}
    assert (await capture())[1] is False  # Same visible prompt does not prove unchanged full body.
    bundle.rows["facts"].pop("metadata")
    bundle.witnesses["facts"]["content_digest"] = None
    assert (await capture())[1] is False  # Legacy NULL cannot acquire reference-change permission.
    bundle.witnesses["facts"]["content_digest"] = artifact_content_digest(
        "facts", bundle.rows["facts"]
    )
    selected["episodes"][0]["content"] = "stale copied input"
    with pytest.raises(PolicyUnavailableError, match="full input changed"):
        await capture()


@pytest.mark.asyncio
async def test_runtime_context_disposes_closed_exact_bundle_and_preserves_mixed_or_active_context():
    """REQ-location-retention-005; planted software state/control, SQL remains separate."""
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_memory_context import dispose_runtime_context
    from butlers.chronicler.location_policy import PolicyUnavailableError

    generation, session_id, loan_id, source_generation, incarnation, decision = (
        uuid4() for _ in range(6)
    )
    context = "# Memory Context\n- native source sentinel"
    base = "Independent configured instructions stay byte exact"
    prompt = "server generated source input"
    system = base + "\n\n" + context
    body_digest = b"b" * 32
    frozen = {
        "input_generation": generation,
        "receiving_session": session_id,
        "exclusive_input": True,
        "ended_receipt": uuid4(),
        "server_request": None,
        "context_bytes": len(context.encode()),
        "context_digest": hashlib.sha256(context.encode()).digest(),
        "system_digest": hashlib.sha256(system.encode()).digest(),
        "prompt_digest": hashlib.sha256(prompt.encode()).digest(),
    }
    frozen["bundle_digest"] = content_digest(
        {
            "loans": [[str(loan_id), body_digest.hex()]],
            "context": frozen["context_digest"].hex(),
            "system": frozen["system_digest"].hex(),
            "prompt": frozen["prompt_digest"].hex(),
        }
    )
    session = {
        "prompt": prompt,
        "effective_system_prompt": system,
        "tool_calls": [],
        "completed_at": datetime.now(UTC),
        "prompt_provenance": [
            {"source": "base", "sha": "retained"},
            {"source": "memory_context", "sha": "removed"},
        ],
    }
    loan = {
        "loan_id": loan_id,
        "source_generation": source_generation,
        "body_digest": body_digest,
        "receiving_incarnation": incarnation,
    }
    plan = {
        "decision_id": str(decision),
        "manifest_digest": (b"m" * 32).hex(),
        "catalog_loans": [
            {
                **{key: str(value) for key, value in loan.items()},
                "body_digest": body_digest.hex(),
                "receiver_name": "chronicler",
                "complete_input": True,
            }
        ],
    }

    class Pool:
        writes = []
        receipt = None
        terminal = None
        diagnostic = {"command": "Synthetic copied input", "stderr": "Synthetic copied output"}
        artifacts = []
        tool_witnesses = []
        tool_loans = []
        mutation_inputs = []
        closed_questions = []
        mutation_disposed = False
        mutation_parents_closed = False
        artifact_row = None
        descendant = False
        deleted = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        def is_closed(self):
            return False

        async def fetchrow(self, sql, *args):
            if "SELECT d.*,b.receiving_session" in sql:
                return self.terminal
            if "public.memory_catalog" in sql:
                return None
            if "SELECT * FROM rules" in sql:
                return self.artifact_row
            if "location_native_memory_bundles" in sql:
                return None
            return frozen if "context_bindings" in sql else session

        async def fetch(self, sql, *args):
            if "location_native_delegation_inputs q" in sql:
                return self.closed_questions
            if "location_native_memory_mutation_inputs" in sql:
                return [
                    row
                    for row in self.mutation_inputs
                    if not args
                    or "WHERE tool_generation=$1" not in sql
                    or row["tool_generation"] == args[0]
                ]
            if "location_native_catalog_generations" in sql:
                return []  # No planted owning source; never fabricate a catalog parent.
            if "location_runtime_tool_inputs" in sql:
                return self.tool_loans
            if "location_runtime_tool_intents" in sql:
                return self.tool_witnesses
            if "context_episodes" in sql:
                return []
            if "FROM chronicler.location_native_copy_births" in sql:
                return [
                    {
                        "copy_generation": source_generation,
                        "input_digest": body_digest,
                        "lineage_known": True,
                        "exclusive_input": True,
                    }
                ]
            if "context_artifacts" in sql:
                return self.artifacts
            return [loan]

        async def fetchval(self, sql, *args):
            if "session_process_logs" in sql:
                return (
                    self.diagnostic["command"]
                    not in {"[Location input forgotten]", "[Location-derived diagnostic forgotten]"}
                    or self.diagnostic["stderr"] is not None
                )
            if "location_native_memory_artifact_dispositions" in sql:
                return self.mutation_disposed
            if "SELECT count(*)=$5 AND bool_and" in sql or "SELECT count(*)=$4 AND bool_and" in sql:
                return self.mutation_parents_closed
            if "DELETE FROM rules" in sql:
                self.deleted = True
                self.writes.append((sql, args))
                return args[0]
            if "SELECT EXISTS(SELECT 1 FROM rules" in sql:
                return not self.deleted
            if "context_artifacts" in sql:
                return self.descendant
            if sql == "SELECT current_schema()":
                return "chronicler_mem"
            if sql == "SELECT current_user":
                return "own-role-double"
            if "count(*) FROM location_catalog_copy_finished" in sql:
                return 1 + len(self.tool_loans)
            if "location_runtime_context_dispositions" in sql:
                return self.receipt
            return False

        async def execute(self, sql, *args):
            self.writes.append((sql, args))
            if "INSERT INTO" in sql and "context_dispositions" in sql:
                self.receipt = args[3]
                self.terminal = dict(
                    input_generation=args[0],
                    decision_id=args[1],
                    manifest_digest=args[2],
                    receipt_id=args[3],
                    reduced_system_digest=args[4],
                    reduced_provenance_digest=args[5],
                    receiving_session=session_id,
                )
            if "UPDATE" in sql and ".session_process_logs " in sql:
                self.diagnostic.update(command="[Location input forgotten]", stderr=None)
            if "UPDATE" in sql and ".sessions " in sql:
                session.update(
                    prompt="[Location input forgotten]",
                    result="[Location output forgotten]",
                    tool_calls=[],
                    error=None,
                    effective_system_prompt=args[2],
                    prompt_provenance=args[3],
                )

    pool = Pool()
    runtime = SimpleNamespace(
        memory=pool,
        domain=pool,
        identity=("chronicler", "own-role-double"),
        memory_identity=("chronicler_mem", "own-role-double"),
        name="chronicler",
        active=True,
    )
    for field, value in (("exclusive_input", False), ("ended_receipt", None)):
        prior = frozen[field]
        frozen[field] = value
        assert await dispose_runtime_context(runtime, generation, plan) is False
        assert not any("UPDATE" in sql or "DELETE" in sql for sql, _ in pool.writes)
        frozen[field] = prior
    session["effective_system_prompt"] = "changed or independent current body"
    assert await dispose_runtime_context(runtime, generation, plan) is False
    session["effective_system_prompt"] = system
    empty_plan = {**plan, "catalog_loans": []}
    assert await dispose_runtime_context(runtime, generation, empty_plan) is False
    from butlers.chronicler.location_projection import _digest_value

    identifier = uuid4()
    pool.artifact_row = {"id": identifier, "content": "actual native output"}
    from butlers.chronicler import location_memory_context as contexts
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_memory_derivation import execute_rule_insert

    _runtimes[pool] = runtime
    native = contexts._RuntimeContext(runtime, generation, session_id, True, admitted=True)
    native_token = contexts._current_runtime_context.set(native)
    try:
        await execute_rule_insert(pool, identifier, "INSERT INTO rules VALUES($1)", identifier)
        captured_rows = [
            args for sql, args in pool.writes if "INSERT INTO" in sql and "context_artifacts" in sql
        ]
        assert len(captured_rows) == 1
        captured = captured_rows[0]
        assert captured[1:4] == (generation, "rules", identifier)
        assert captured[4] == content_digest({"memory_artifact": _digest_value(pool.artifact_row)})
    finally:
        contexts._current_runtime_context.reset(native_token)
        _runtimes.pop(pool)
    pool.writes.clear()
    pool.artifacts = [
        {
            "artifact_id": identifier,
            "memory_table": "rules",
            "body_digest": content_digest({"memory_artifact": _digest_value(pool.artifact_row)}),
        }
    ]
    session["tool_calls"] = [
        {"name": "unknown_routed_mutation", "outcome": "success", "result": {"id": str(identifier)}}
    ]
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted
    session["tool_calls"] = [
        {"name": "memory_store_rule", "outcome": "success", "result": {"id": str(identifier)}}
    ]
    pool.descendant = True
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted and not any("UPDATE" in sql for sql, _ in pool.writes)
    pool.descendant = False
    pool.artifact_row["content"] = "changed independent version"
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted
    pool.artifact_row["content"] = "actual native output"
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    stored_call = {**session["tool_calls"][0], "module": "memory", "input_fingerprint": "a" * 64}
    read_call = {
        "name": "memory_catalog_search",
        "module": "memory",
        "outcome": "success",
        "input_fingerprint": "b" * 64,
        "result": [{"summary": "selected catalog body"}],
    }
    session["tool_calls"] = [stored_call, read_call]
    pool.tool_witnesses = [
        {
            "tool_name": call["name"],
            "module_name": call["module"],
            "input_digest": bytes.fromhex(call["input_fingerprint"]),
            "outcome": "success",
            "result_digest": bytes.fromhex(fingerprint_tool_call_payload(call["result"])),
            "exclusive_inputs": call is read_call,
        }
        for call in session["tool_calls"]
    ]
    from butlers.chronicler.location_memory_context import captured_artifact_calls
    from butlers.chronicler.location_tool_copies import matched_tool_records

    second_read = {**read_call, "input_fingerprint": "c" * 64}
    second_witness = {**pool.tool_witnesses[1], "input_digest": bytes.fromhex("c" * 64)}
    two_reads = [read_call, second_read]
    two_witnesses = [pool.tool_witnesses[1], second_witness]
    assert not captured_artifact_calls(None, [], two_witnesses)
    assert not captured_artifact_calls([], [], two_witnesses)
    assert matched_tool_records(two_reads, two_witnesses)
    assert captured_artifact_calls(two_reads, [], two_witnesses)
    second_witness["exclusive_inputs"] = False
    assert matched_tool_records(two_reads, two_witnesses)  # Matching is no erasure authority.
    assert captured_artifact_calls(two_reads, [], two_witnesses) is False
    second_witness["exclusive_inputs"] = True
    for native_name in ("memory_search", "memory_recall", "memory_get"):
        native_read = {**read_call, "name": native_name}
        native_witness = {**two_witnesses[0], "tool_name": native_name}
        assert matched_tool_records([native_read], [native_witness])
        assert captured_artifact_calls([native_read], [], [native_witness])
        native_witness["exclusive_inputs"] = False
        assert captured_artifact_calls([native_read], [], [native_witness]) is False
    assert matched_tool_records(two_reads + [second_read], two_witnesses) is False
    assert matched_tool_records([read_call, read_call], [two_witnesses[0]]) is False
    assert captured_artifact_calls([read_call, read_call], [], [two_witnesses[0]]) is False
    assert captured_artifact_calls(two_reads, [], [{"tool_name": read_call["name"]}]) is False
    assert matched_tool_records([read_call, read_call], [two_witnesses[0], two_witnesses[0]])
    assert captured_artifact_calls([read_call, read_call], [], [two_witnesses[0], two_witnesses[0]])

    from butlers.chronicler.location_delegation_disposal import unanswered_source_question

    source_row = {
        "asking_butler": "chronicler",
        "status": "routed",
        "metadata": {},
        "wake_state": "not_applicable",
        **{
            key: None
            for key in (
                "answer",
                "answer_digest",
                "answered_at",
                "answering_butler",
                "wake_key",
                "wake_task_id",
                "wake_task_name",
                "wake_updated_at",
            )
        },
    }
    assert unanswered_source_question(source_row)
    for changed in (
        {"metadata": {"independent": "retained"}},
        {"answer": "actual answer"},
        {"wake_task_id": uuid4()},
        {"wake_state": "callback_pending"},
        {"asking_butler": "relationship"},
        {"status": "answered"},
    ):
        assert not unanswered_source_question({**source_row, **changed})
    question_call = {
        **read_call,
        "module": "core",
        "name": "delegate_ask",
        "result": {"ledger_id": str(uuid4())},
    }
    question_witness = {
        **two_witnesses[0],
        "tool_name": "delegate_ask",
        "module_name": "core",
        "tool_generation": uuid4(),
        "result_digest": bytes.fromhex(fingerprint_tool_call_payload(question_call["result"])),
    }
    closed_question = {"tool_generation": question_witness["tool_generation"]}
    assert not captured_artifact_calls([question_call], [], [question_witness])
    assert captured_artifact_calls(
        [question_call], [], [question_witness], closed_questions=[closed_question]
    )
    for damaged in (
        {"exclusive_inputs": False},
        {"module_name": "memory"},
        {"result_digest": b"x" * 32},
        {"input_digest": b"x" * 32},
    ):
        assert not captured_artifact_calls(
            [question_call],
            [],
            [{**question_witness, **damaged}],
            closed_questions=[closed_question],
        )
    assert not captured_artifact_calls(
        [question_call],
        [],
        [question_witness],
        closed_questions=[{"tool_generation": uuid4()}],
    )
    assert not captured_artifact_calls(
        [question_call, question_call],
        [],
        [question_witness],
        closed_questions=[closed_question],
    )

    # Actual successful mutating calls require their own immutable input and
    # exact source-artifact disposition; another read witness supplies none.
    mutation_call = {
        **read_call,
        "name": "memory_confirm",
        "input_fingerprint": "d" * 64,
        "result": {"confirmed": True},
    }
    mutation_witness = {
        **pool.tool_witnesses[1],
        "tool_name": "memory_confirm",
        "tool_generation": uuid4(),
        "input_digest": bytes.fromhex("d" * 64),
        "result_digest": bytes.fromhex(fingerprint_tool_call_payload(mutation_call["result"])),
    }
    mutation_input = {
        "tool_generation": mutation_witness["tool_generation"],
        "lifecycle_only": True,
        "artifact_generation": uuid4(),
        "input_generation": uuid4(),
        "before_digest": b"u" * 32,
        "original_digest": b"o" * 32,
        "parent_count": 2,
    }
    assert not captured_artifact_calls([mutation_call], [], [mutation_witness])
    assert captured_artifact_calls([mutation_call], [], [mutation_witness], [mutation_input])
    foreign = {**mutation_input, "tool_generation": uuid4()}
    assert not captured_artifact_calls([mutation_call], [], [mutation_witness], [foreign])
    mixed = {**mutation_input, "lifecycle_only": False}
    assert not captured_artifact_calls([mutation_call], [], [mutation_witness], [mixed])
    other_call = {**mutation_call, "input_fingerprint": "e" * 64}
    other_witness = {
        **mutation_witness,
        "tool_generation": uuid4(),
        "input_digest": bytes.fromhex("e" * 64),
    }
    other_input = {**mutation_input, "tool_generation": other_witness["tool_generation"]}
    assert not captured_artifact_calls(
        [mutation_call, other_call], [], [mutation_witness, other_witness], [mutation_input]
    )
    assert captured_artifact_calls(
        [mutation_call, other_call],
        [],
        [mutation_witness, other_witness],
        [mutation_input, other_input],
    )
    other_witness["exclusive_inputs"] = False
    assert not captured_artifact_calls(
        [mutation_call, other_call],
        [],
        [mutation_witness, other_witness],
        [mutation_input, other_input],
    )
    assert not captured_artifact_calls([mutation_call, mutation_call], [], [mutation_witness])
    session["tool_calls"].append(mutation_call)
    pool.tool_witnesses.append(mutation_witness)
    pool.mutation_inputs = [mutation_input]
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted  # Artifact receipt required despite successful tool outcome.
    pool.mutation_disposed = True
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted  # Missing/mixed/extra/unselected parent still holds raw input.
    pool.mutation_parents_closed = True
    mutation_input["lifecycle_only"] = False
    assert await dispose_runtime_context(runtime, generation, plan) is False
    mutation_input["lifecycle_only"] = True

    pool.tool_loans = [{**loan, "loan_id": uuid4()}]
    assert await dispose_runtime_context(runtime, generation, plan) is False
    assert not pool.deleted  # Unselected late input preserves the entire context.
    plan["catalog_loans"].append(
        {
            **plan["catalog_loans"][0],
            "loan_id": str(pool.tool_loans[0]["loan_id"]),
        }
    )
    pool.tool_witnesses[1]["outcome"] = None
    assert await dispose_runtime_context(runtime, generation, plan) is False
    pool.tool_witnesses[1]["outcome"] = "success"
    pool.tool_witnesses[1]["exclusive_inputs"] = False
    assert await dispose_runtime_context(runtime, generation, plan) is False
    pool.tool_witnesses[1]["exclusive_inputs"] = True
    read_call["result"] = [{"summary": "changed later or independent body"}]
    assert await dispose_runtime_context(runtime, generation, plan) is False
    read_call["result"] = [{"summary": "selected catalog body"}]
    assert not pool.deleted
    assert await dispose_runtime_context(runtime, generation, plan) is True
    assert pool.deleted
    # Same fixed invocation cannot refill after the actual context disposition.
    _runtimes[pool] = runtime
    native_token = contexts._current_runtime_context.set(native)
    try:
        before = len(pool.writes)
        with pytest.raises(PolicyUnavailableError, match="input was disposed"):
            await execute_rule_insert(pool, identifier, "INSERT INTO rules VALUES($1)", identifier)
        assert not any("INSERT INTO rules" in sql for sql, _ in pool.writes[before:])
    finally:
        contexts._current_runtime_context.reset(native_token)
        _runtimes.pop(pool)
    # Same-writer late-read snapshot binds every actual parent and does not
    # reuse the original context generation as a mutable lineage bundle.
    from butlers.chronicler.location_tool_copies import _current_tool_copy, _ToolCopy

    native_parent, late_parent, active_tool = uuid4(), uuid4(), uuid4()
    native_pool = Pool()
    native_pool.writes = []
    native_pool.artifact_row = {"id": identifier, "content": "actual source-owned output"}
    native_digest = content_digest({"memory_artifact": _digest_value(native_pool.artifact_row)})
    native_pool.tool_witnesses = [
        {**row, "tool_generation": uuid4()} for row in pool.tool_witnesses
    ]
    native_pool.mutation_inputs = [
        {**mutation_input, "tool_generation": native_pool.tool_witnesses[2]["tool_generation"]}
    ]
    native_pool.mutation_parents_closed = True
    native_pool.tool_witnesses.append(
        {
            "tool_generation": active_tool,
            "tool_name": "memory_store_rule",
            "module_name": "memory",
            "input_digest": b"w" * 32,
            "outcome": None,
            "result_digest": None,
            "exclusive_inputs": False,
        }
    )
    native_pool.tool_loans = list(pool.tool_loans)
    original_fetch = native_pool.fetch
    source_parent_known = True
    source_partial = None
    sibling_loan_parent = uuid4()

    async def source_fetch(sql, *args):
        if "location_catalog_copy_loans l" in sql:
            return native_pool.tool_loans
        if "location_native_catalog_generations" in sql:
            rows = (
                [
                    {
                        "copy_generation": late_parent,
                        "input_digest": b"l" * 32,
                        "birth_digest": b"l" * 32,
                        "parent_count": 1,
                        "output_id": identifier,
                        "lineage_known": True,
                        "exclusive_input": True,
                    }
                ]
                if source_parent_known
                else []
            )
            if rows and source_partial is not None:
                rows[0]["parent_count"] = 2
                second = {**rows[0], "copy_generation": sibling_loan_parent}
                if source_partial == "missing":
                    second["output_id"], second["birth_digest"] = None, None
                elif source_partial == "digest":
                    second["birth_digest"] = b"x" * 32
                rows.append(second)
                if source_partial == "extra":
                    rows.append({**second, "copy_generation": uuid4()})
                if source_partial == "empty":
                    rows = [
                        {
                            **rows[0],
                            "copy_generation": None,
                            "input_digest": None,
                            "output_id": None,
                            "birth_digest": None,
                        }
                    ]
                if "LEFT JOIN" not in sql:
                    rows = [
                        r
                        for r in rows
                        if r["output_id"] is not None and r["birth_digest"] == r["input_digest"]
                    ]
            return rows
        if "FROM chronicler.location_native_copy_births" in sql:
            return [
                {
                    "copy_generation": native_parent,
                    "input_digest": b"n" * 32,
                    "lineage_known": True,
                    "exclusive_input": True,
                }
            ]
        return await original_fetch(sql, *args)

    native_pool.fetch = source_fetch
    source_runtime = SimpleNamespace(
        **{**vars(runtime), "memory": native_pool, "domain": native_pool}
    )
    active_copy = _ToolCopy(source_runtime, active_tool, session_id, "memory_store_rule", "memory")
    tool_token = _current_tool_copy.set(active_copy)
    writer = contexts._ArtifactWriter(source_runtime, generation, native_pool)
    try:
        emitted_artifact = uuid4()
        assert (
            await contexts.capture_context_catalog_source(
                writer,
                emitted_artifact,
                "rules",
                identifier,
                native_digest,
            )
            is True
        )
        source_artifact = [
            args
            for sql, args in native_pool.writes
            if "INSERT INTO chronicler.location_native_memory_artifacts" in sql
        ][0]
        assert source_artifact[-2] == native_digest
        from butlers.chronicler.location_memory_copies import artifact_content_digest

        assert source_artifact[-1] == artifact_content_digest("rules", native_pool.artifact_row)
        native_pool.writes.clear()
        source_partial = "complete"
        assert await contexts.capture_context_catalog_source(
            writer, uuid4(), "rules", identifier, native_digest
        )
        full_source_rows = [
            args
            for sql, args in native_pool.writes
            if "INSERT INTO" in sql and "location_native_dispatch_parents" in sql
        ]
        assert {args[1] for args in full_source_rows} == {
            native_parent,
            late_parent,
            sibling_loan_parent,
        }
        for partial in ("missing", "digest", "extra", "empty"):
            native_pool.writes.clear()
            source_partial = partial
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                await contexts.capture_context_catalog_source(
                    writer, uuid4(), "rules", identifier, native_digest
                )
            assert native_pool.writes == []
        source_partial = None
        native_pool.writes.clear()
        with pytest.raises(PolicyUnavailableError, match="body differs"):
            await contexts.capture_context_catalog_source(
                writer, uuid4(), "rules", identifier, b"x" * 32
            )
        assert native_pool.writes == []
        assert (
            await contexts.capture_context_catalog_source(
                writer, emitted_artifact, "rules", identifier, native_digest
            )
            is True
        )
        source_rows = [
            args
            for sql, args in native_pool.writes
            if "INSERT INTO" in sql and "location_native_dispatch_parents" in sql
        ]
        assert {args[1] for args in source_rows} == {native_parent, late_parent}
        assert all(args[0] == emitted_artifact and args[0] != generation for args in source_rows)
        native_pool.writes.clear()
        source_parent_known = False
        assert (
            await contexts.capture_context_catalog_source(
                writer,
                uuid4(),
                "rules",
                identifier,
                native_digest,
            )
            is False
        )
        assert native_pool.writes == []
        source_parent_known = True
        native_pool.tool_witnesses[1]["exclusive_inputs"] = False
        assert (
            await contexts.capture_context_catalog_source(
                writer,
                uuid4(),
                "rules",
                identifier,
                native_digest,
            )
            is False
        )
        assert native_pool.writes == []
    finally:
        _current_tool_copy.reset(tool_token)
    update = next(args for sql, args in pool.writes if "UPDATE" in sql and ".sessions " in sql)
    assert update[2] == base
    assert update[3] == [{"source": "base", "sha": "retained"}]
    assert any("location_catalog_copy_finished" in sql for sql, _ in pool.writes)
    await _assert_closed_native_mutation_copy_disposal()
    await _assert_catalog_terminal_complete_ancestry_values()
    # A replay never refills its immutable reduced-body witnesses from current rows.
    assert await dispose_runtime_context(runtime, generation, plan)
    original_reduced = session["effective_system_prompt"]
    session["effective_system_prompt"] += " changed independent suffix"
    with pytest.raises(
        PolicyUnavailableError, match="Committed configured context body is unknown"
    ):
        await dispose_runtime_context(runtime, generation, plan)
    session["effective_system_prompt"] = original_reduced
    original_digest = pool.terminal["reduced_system_digest"]
    pool.terminal["reduced_system_digest"] = None
    with pytest.raises(
        PolicyUnavailableError, match="Committed configured context body is unknown"
    ):
        await dispose_runtime_context(runtime, generation, plan)
    pool.terminal["reduced_system_digest"] = original_digest
    pool.diagnostic["stderr"] = "Synthetic retained source diagnostic"
    with pytest.raises(
        PolicyUnavailableError, match="Committed configured context body is unknown"
    ):
        await dispose_runtime_context(runtime, generation, plan)
    pool.diagnostic["stderr"] = None
    assert await dispose_runtime_context(runtime, generation, plan)
    await _assert_core_question_context_values()
    await _assert_native_answer_challenge_values()
    await _assert_native_answer_server_values()
    await _assert_native_answer_cohort_values()
    await _assert_native_answer_receiver_values()
    await _assert_native_answer_context_values()
    await _assert_metadata_wake_tool_values()
    await _assert_native_answer_source_values()
    await _assert_source_question_profile_values()
    await _assert_answered_question_reference_values()
    await _assert_answer_question_observation_values()
    await _assert_source_question_tool_values()
    await _assert_recursive_question_values()
    await _assert_recursive_question_cohort_values()
    await _assert_recursive_question_observation_values()
    await _assert_unaccepted_question_recovery_values()
    await _assert_receiving_task_disposition_values()
    await _assert_native_answer_schedule_values()
    await _assert_native_return_processing_values()
    await _assert_native_ingress_runtime_values()
    await _assert_structured_local_processing_disposition_values()


@pytest.mark.asyncio
async def test_unconfigured_reader_preserves_ordinary_rows_and_closed_diagnostics_do_not_leak():
    """REQ-location-retention-005/007; planted reader classification, software only."""
    from types import SimpleNamespace
    from uuid import uuid4

    import asyncpg

    from butlers.chronicler.location_policy import PolicyUnavailableError, closed_failure
    from butlers.chronicler.location_retention import _read_unconfigured

    row = SimpleNamespace(id=uuid4(), source_name="calendar.events", title="ordinary sentinel")

    class Pool:
        projected = False

        async def fetchval(self, sql, kind, ids):
            assert "location_projection_outputs" in sql
            assert kind == "episode" and ids == [row.id]
            return self.projected

    pool = Pool()

    async def reader(actual):
        assert actual is pool
        return [row]

    assert await _read_unconfigured(pool, "episode", reader) == [row]
    pool.projected = True
    with pytest.raises(PolicyUnavailableError):
        await _read_unconfigured(pool, "episode", reader)
    pool.projected = False
    row.source_name = "owntracks.ssid"
    with pytest.raises(PolicyUnavailableError):
        await _read_unconfigured(pool, "episode", reader)
    row.source_name = "calendar.events"
    assert await _read_unconfigured(pool, "episode", reader) == [row]
    from butlers.chronicler.location_retention import _api_copy_pools, _capture_api_read

    legacy_pool = AsyncMock(spec=asyncpg.Pool)
    legacy_pool.fetchval.side_effect = lambda sql, *args: (
        "public" if sql == "SELECT current_schema()" else pool.projected
    )

    async def actual_ordinary_reader(actual):
        assert actual is legacy_pool
        return [row]

    _api_copy_pools.add(legacy_pool)  # Explicit software registry double, no real enrollment.
    try:
        assert await _capture_api_read(legacy_pool, "episode", actual_ordinary_reader) == [row]
        pool.projected = True
        with pytest.raises(PolicyUnavailableError):
            await _capture_api_read(legacy_pool, "episode", actual_ordinary_reader)
        pool.projected = False
        assert await _capture_api_read(legacy_pool, "episode", actual_ordinary_reader) == [row]
    finally:
        _api_copy_pools.discard(legacy_pool)
    sentinel = "synthetic raw private location must not be emitted"
    for exc, expected in (
        (asyncpg.UndefinedColumnError(sentinel), ("postgres", "undefined_column", "42703")),
        (PolicyUnavailableError(sentinel), ("native", "policy_unavailable", "unknown")),
        (TypeError(sentinel), ("native", "type_error", "unknown")),
    ):
        assert closed_failure(exc) == expected
        assert sentinel not in repr(closed_failure(exc))


async def _assert_native_memory_mutation_versions():
    """Actual native confirm writer and complete-chain consumers; SQL doubles only."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from uuid import uuid4

    from butlers.chronicler import location_memory_copies as copies
    from butlers.chronicler.location_memory_mutations import (
        current_artifact_body_matches,
        memory_mutation_transaction,
    )
    from butlers.chronicler.location_projection import _digest_value
    from butlers.modules.memory.storage import confirm_memory

    class Writer:
        def __init__(self):
            self.row = {
                "id": uuid4(),
                "content": "native source body",
                "validity": "active",
                "last_confirmed_at": None,
                "reference_count": 0,
                "metadata": {},
            }
            self.original = {
                "artifact_generation": uuid4(),
                "artifact_id": self.row["id"],
                "memory_table": "facts",
                "body_digest": content_digest({"memory_artifact": _digest_value(self.row)}),
                "content_digest": copies.artifact_content_digest("facts", self.row),
            }
            self.transitions = []
            self.input_births = []
            self.mutation_inputs = []
            self.parents = [
                {
                    "output_kind": "point_event",
                    "output_id": uuid4(),
                    "lineage_known": True,
                    "exclusive_input": True,
                    "bundle_exclusive": True,
                }
            ]
            self.declared_parent_count = 1
            self.dependency_present = True
            self.dependency_count = 1
            self.intent_present = True
            self.input_unknown = False
            self.trace = []
            self.prepared = False
            self.unknown = False
            self.fail_insert = False
            self.serial = 0
            self.independent = False
            self.schema = "chronicler_mem"
            self.role = "configured_native_writer_double"

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            saved = deepcopy((self.row, self.transitions, self.input_births, self.mutation_inputs))
            schema = self.schema
            self.trace.append("begin")
            try:
                yield
            except BaseException:
                self.row, self.transitions, self.input_births, self.mutation_inputs = saved
                self.trace.append("rollback")
                raise
            else:
                self.trace.append("commit")
            finally:
                self.schema = schema  # SET LOCAL never changes the pool view.

        async def fetchrow(self, sql, *args):
            if "location_native_memory_mutation_inputs" in sql:
                self.trace.append("input_readback")
                found = [
                    x
                    for x in self.mutation_inputs
                    if x["input_generation"] == args[0] and x["tool_generation"] == args[1]
                ]
                return None if self.input_unknown or not found else found[0]
            if "FROM public.memory_catalog" in sql:
                return None
            if "location_retention_policy" in sql:
                self.trace.append("policy")
                return {"version": 1}
            if "location_native_memory_artifacts" in sql:
                self.trace.append("birth_lock")
                return None if self.independent else self.original
            self.trace.append("canonical_lock")
            return deepcopy(self.row)

        async def fetch(self, sql, *args):
            if "AS bundle_exclusive" in sql:
                self.trace.append("parent_read")
                rows = []
                for parent in self.parents:
                    row = deepcopy(parent)
                    row.setdefault("copy_generation", row["output_id"])
                    row.setdefault("input_digest", b"p" * 32)
                    row.setdefault("birth_digest", row["input_digest"])
                    row["parent_count"] = self.declared_parent_count
                    if row["output_id"] is None or row["birth_digest"] != row["input_digest"]:
                        if "LEFT JOIN" not in sql:
                            continue  # Faithful old INNER JOIN drops this frozen declared parent.
                        if row["output_id"] is None or "AND b.input_digest=p.input_digest" in sql:
                            row["output_id"], row["birth_digest"] = None, None
                    rows.append(row)
                return rows
            assert "location_native_memory_mutations" in sql
            return deepcopy(self.transitions)

        async def fetchval(self, sql, *args):
            if "pg_catalog.pg_constraint" in sql:
                return self.dependency_present and (
                    self.dependency_count == 1 if "count(*)=1" in sql else True
                )
            if "location_runtime_tool_intents" in sql:
                self.trace.append("intent_read")
                return self.intent_present
            if "count(*) FROM chronicler.location_native_copy_births" in sql:
                return sum(x[0] == args[0] and x[3] == args[1] for x in self.input_births)
            if "current_schema" in sql:
                return self.schema
            if "current_user" in sql:
                return self.role
            if "location_retention_plan_outputs" in sql:
                return self.prepared
            if "FROM public.memory_catalog" in sql:
                return False
            assert "after_digest" in sql and "mutation_generation=$1" in sql
            self.trace.append("readback")
            found = [t for t in self.transitions if t["mutation_generation"] == args[0]]
            return None if self.unknown or not found else found[0]["after_digest"]

        async def execute(self, sql, *args):
            if sql.startswith("SET LOCAL search_path"):
                self.schema = "chronicler_mem"
                self.trace.append("fixed_memory_view")
                return "SET"
            if "pg_advisory_xact_lock" in sql:
                return "SELECT 1"
            if "INSERT INTO chronicler.location_native_copy_births" in sql:
                self.trace.append("input_birth")
                self.input_births.append(args)
                return "INSERT 0 1"
            if "INSERT INTO chronicler.location_native_memory_mutation_inputs" in sql:
                self.trace.append("input_commit")
                self.mutation_inputs.append(
                    dict(
                        zip(
                            (
                                "input_generation",
                                "tool_generation",
                                "artifact_generation",
                                "before_digest",
                                "after_digest",
                                "parent_count",
                                "lifecycle_only",
                            ),
                            args,
                            strict=True,
                        )
                    )
                )
                return "INSERT 0 1"
            if "INSERT INTO chronicler.location_native_memory_mutations" in sql:
                self.trace.append("transition")
                if self.fail_insert:
                    raise RuntimeError("fixed transition-write failure double")
                self.transitions.append(
                    dict(
                        zip(
                            (
                                "mutation_generation",
                                "artifact_generation",
                                "revision",
                                "previous_generation",
                                "before_digest",
                                "after_digest",
                                "lifecycle_only",
                            ),
                            args,
                            strict=True,
                        )
                    )
                )
                return "INSERT 0 1"
            assert "SET last_confirmed_at" in sql
            self.trace.append("business")
            self.serial += 1
            self.row["last_confirmed_at"] = self.serial
            return "UPDATE 1"

    domain, writer = object(), Writer()
    copies._receivers[domain] = (writer, "chronicler_mem", "configured_native_writer_double")
    original = deepcopy(writer.original)
    try:
        assert await confirm_memory(writer, "fact", writer.row["id"])
        assert writer.trace.index("policy") < writer.trace.index("canonical_lock")
        assert writer.trace.index("business") < writer.trace.index("transition")
        assert writer.trace.index("transition") < writer.trace.index("commit")
        assert writer.trace.index("commit") < writer.trace.index("readback")
        assert writer.original == original  # Original body and lineage remain frozen.
        assert writer.transitions[0]["before_digest"] == original["content_digest"]
        assert await current_artifact_body_matches(writer, writer.row, original)
        first = deepcopy(writer.transitions[0])
        assert await confirm_memory(writer, "fact", writer.row["id"])
        assert writer.transitions[0] == first
        assert writer.transitions[1]["revision"] == 2
        assert writer.transitions[1]["previous_generation"] == first["mutation_generation"]
        assert writer.transitions[1]["before_digest"] == first["after_digest"]
        assert await current_artifact_body_matches(writer, writer.row, original)
        healthy = deepcopy(writer.transitions)
        for index, key, value in (
            (1, "revision", 3),
            (1, "previous_generation", uuid4()),
            (1, "before_digest", b"z" * 32),
            (1, "artifact_generation", uuid4()),
            (1, "lifecycle_only", False),
            (1, "after_digest", b"z" * 32),
        ):
            writer.transitions = deepcopy(healthy)
            writer.transitions[index][key] = value
            assert not await current_artifact_body_matches(writer, writer.row, original)
        writer.transitions = deepcopy(healthy)
        assert await current_artifact_body_matches(writer, writer.row, original)
        writer.row["content"] = "unregistered changed body"
        assert not await current_artifact_body_matches(writer, writer.row, original)
        with pytest.raises(copies.PolicyUnavailableError, match="body witness differs"):
            await confirm_memory(writer, "fact", writer.row["id"])
        writer.row["content"] = "native source body"
        legacy = {**original, "content_digest": None}
        assert not await current_artifact_body_matches(writer, writer.row, legacy)
        writer.fail_insert = True
        before = deepcopy((writer.row, writer.transitions))
        with pytest.raises(RuntimeError, match="transition-write"):
            await confirm_memory(writer, "fact", writer.row["id"])
        assert (writer.row, writer.transitions) == before
        writer.fail_insert = False
        writer.unknown = True
        with pytest.raises(copies.PolicyUnavailableError, match="witness is unknown"):
            await confirm_memory(writer, "fact", writer.row["id"])
        assert writer.trace[-1] == "readback"
        assert len(writer.transitions) == len(healthy) + 1  # Commit is not falsely rolled back.
        writer.unknown = False
        assert await current_artifact_body_matches(writer, writer.row, original)
        writer.prepared = True
        count = len(writer.transitions)
        with pytest.raises(copies.PolicyUnavailableError, match="prepared"):
            await confirm_memory(writer, "fact", writer.row["id"])
        assert len(writer.transitions) == count
        writer.prepared = False
        async with memory_mutation_transaction(writer, "facts", writer.row["id"]) as actual:
            assert actual is writer
            writer.row["metadata"] = {"independent_annotation": "preserve this unrelated prose"}
        assert writer.transitions[-1]["lifecycle_only"] is False
        assert not await current_artifact_body_matches(writer, writer.row, original)
        # A later closed lifecycle change cannot clear an earlier mixed edit.
        assert await confirm_memory(writer, "fact", writer.row["id"])
        assert writer.transitions[-1]["lifecycle_only"] is False
        assert not await current_artifact_body_matches(writer, writer.row, original)
        writer.independent = True
        before = len(writer.transitions)
        assert await confirm_memory(writer, "fact", writer.row["id"])
        assert len(writer.transitions) == before  # No fabricated native ancestry.
    finally:
        copies._receivers.pop(domain)

    from types import SimpleNamespace

    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_tool_copies import _current_tool_copy, _ToolCopy

    native = Writer()
    runtime = SimpleNamespace(name="chronicler", active=True)
    tool = _ToolCopy(runtime, uuid4(), uuid4(), "memory_confirm", "memory")
    token = _current_tool_copy.set(tool)
    _runtimes[native] = runtime
    copies._receivers[domain] = (native, "chronicler_mem", native.role)
    try:
        # Two immutable declared parents must not collapse to one usable birth.
        partial = Writer()
        partial.declared_parent_count = 2
        first = {**partial.parents[0], "copy_generation": uuid4(), "input_digest": b"a" * 32}
        second = {**first, "copy_generation": uuid4(), "output_id": uuid4()}
        partial.parents = [first, {**second, "output_id": None}]
        _runtimes[partial] = runtime
        copies._receivers[domain] = (partial, "chronicler_mem", partial.role)
        try:
            before = deepcopy(partial.row)
            with pytest.raises(copies.PolicyUnavailableError, match="complete input ancestry"):
                await confirm_memory(partial, "fact", partial.row["id"])
            assert partial.row == before and partial.input_births == []
            partial.parents = [first, second]
            assert await confirm_memory(partial, "fact", partial.row["id"])
            assert partial.mutation_inputs[-1]["parent_count"] == 2
            assert len(partial.input_births) == 2  # Genuine full sibling positive.
            baseline = deepcopy((partial.row, partial.input_births, partial.mutation_inputs))
            for corrupt in (
                [first, {**second, "birth_digest": b"z" * 32}],
                [first, second, {**second, "copy_generation": uuid4(), "output_id": uuid4()}],
            ):
                tool.generation = uuid4()
                partial.parents = corrupt
                with pytest.raises(copies.PolicyUnavailableError, match="complete input ancestry"):
                    await confirm_memory(partial, "fact", partial.row["id"])
                assert (partial.row, partial.input_births, partial.mutation_inputs) == baseline
        finally:
            _runtimes.pop(partial)
            copies._receivers[domain] = (native, "chronicler_mem", native.role)
            tool.generation = uuid4()
        frozen = deepcopy(native.original)
        assert await confirm_memory(native, "fact", native.row["id"])
        assert len(native.input_births) == len(native.mutation_inputs) == 1
        assert native.trace.index("input_birth") < native.trace.index("business")
        assert native.trace.index("business") < native.trace.index("input_commit")
        assert native.trace.index("commit") < native.trace.index("input_readback")
        assert native.original == frozen and tool.read_observed and not tool.mixed_inputs
        assert native.mutation_inputs[0]["tool_generation"] == tool.generation
        assert native.mutation_inputs[0]["artifact_generation"] == frozen["artifact_generation"]
        native.dependency_present = False
        fixed_before = deepcopy((native.row, native.transitions, native.input_births))
        with pytest.raises(copies.PolicyUnavailableError, match="installed tool dependency"):
            await confirm_memory(native, "fact", native.row["id"])
        assert (native.row, native.transitions, native.input_births) == fixed_before
        native.dependency_present = True
        native.dependency_count = 2
        tool.generation = uuid4()
        duplicate_before = deepcopy(
            (native.row, native.transitions, native.input_births, native.mutation_inputs)
        )
        with pytest.raises(copies.PolicyUnavailableError, match="installed tool dependency"):
            await confirm_memory(native, "fact", native.row["id"])
        assert (
            native.row,
            native.transitions,
            native.input_births,
            native.mutation_inputs,
        ) == duplicate_before
        native.dependency_count = 1
        native.intent_present = False
        before = deepcopy((native.row, native.transitions, native.input_births))
        with pytest.raises(copies.PolicyUnavailableError, match="reservation is unavailable"):
            await confirm_memory(native, "fact", native.row["id"])
        assert (native.row, native.transitions, native.input_births) == before
        native.intent_present = True
        parents = native.parents
        native.parents = []
        with pytest.raises(copies.PolicyUnavailableError, match="complete input ancestry"):
            await confirm_memory(native, "fact", native.row["id"])
        assert (native.row, native.transitions, native.input_births) == before
        native.parents = parents
        tool.generation = uuid4()  # A separate actual tool reservation per execution.
        native.input_unknown = True
        with pytest.raises(copies.PolicyUnavailableError, match="input is unknown"):
            await confirm_memory(native, "fact", native.row["id"])
        assert len(native.mutation_inputs) == 2  # COMMIT cannot be falsely rolled back.
        native.input_unknown = False
        tool.generation = uuid4()
        native.parents.append(
            {
                **parents[0],
                "copy_generation": parents[0]["output_id"],
                "output_id": uuid4(),
                "lineage_known": False,
            }
        )
        assert await confirm_memory(native, "fact", native.row["id"])
        assert native.mutation_inputs[-1]["parent_count"] == 2
        assert native.mutation_inputs[-1]["lifecycle_only"] is False
        assert tool.mixed_inputs and len(native.input_births) == 4  # ALL parents, no omission.
        tool.active = False
        with pytest.raises(copies.PolicyUnavailableError, match="lifetime differs"):
            await confirm_memory(native, "fact", native.row["id"])
    finally:
        _current_tool_copy.reset(token)
        _runtimes.pop(native)
        copies._receivers.pop(domain)

    from butlers.chronicler.location_copy_pools import _api_copy_pools
    from butlers.chronicler.location_memory_mutations import _api_writers

    api = Writer()
    api.schema = "chronicler"
    _api_copy_pools.add(api)
    try:
        with pytest.raises(copies.PolicyUnavailableError, match="not enrolled"):
            await confirm_memory(api, "fact", api.row["id"], memory_schema="chronicler_mem")
        assert "business" not in api.trace
        # Private fixed enrollment fixture; this is no real-role/constructor proof.
        _api_writers[api] = ("chronicler", "chronicler_mem", api.role)
        assert await confirm_memory(api, "fact", api.row["id"], memory_schema="chronicler_mem")
        assert api.schema == "chronicler" and len(api.transitions) == 1
        assert api.trace.index("fixed_memory_view") < api.trace.index("policy")
        assert api.trace.index("commit") < api.trace.index("readback")
        assert await current_artifact_body_matches(api, api.row, api.original)
        api.role = "changed_identity"
        count = len(api.transitions)
        with pytest.raises(copies.PolicyUnavailableError, match="identity differs"):
            await confirm_memory(api, "fact", api.row["id"], memory_schema="chronicler_mem")
        assert len(api.transitions) == count
        api.role = "configured_native_writer_double"
        with pytest.raises(copies.PolicyUnavailableError, match="schema differs"):
            await confirm_memory(api, "fact", api.row["id"], memory_schema="another_schema")
        assert len(api.transitions) == count
    finally:
        _api_writers.pop(api, None)
        _api_copy_pools.discard(api)


async def _assert_closed_native_mutation_copy_disposal():
    """Actual native disposer; faithful SQL selectors doubled, no SQL/role credit."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler.location_retention import (
        PolicyUnavailableError,
        dispose_bound_native_copies,
    )

    decision, generation, session_id = uuid4(), uuid4(), uuid4()
    digest, manifest = b"i" * 32, b"m" * 32

    class Owner:
        def __init__(self):
            self.receipt = None
            self.closed = False
            self.pending = False
            self.unknown = False
            self.writes = []
            self.session = {
                "completed_at": datetime.now(UTC),
                "success": True,
                "error": None,
                "prompt": "original input body",
                "result": "original output body",
                "tool_calls": [],
                "effective_system_prompt": "independent base sentinel",
            }

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            if "location_retention_policy" in sql:
                return {"version": 1}
            if "location_retention_plans" in sql:
                return {"state": "holder_pending", "manifest_digest": manifest}
            if "location_native_copy_dispositions" in sql:
                return (
                    None
                    if self.receipt is None
                    else {
                        "receipt_id": self.receipt,
                        "input_digest": digest,
                        "receiving_session": session_id,
                    }
                )
            assert "FROM sessions" in sql
            return self.session

        async def fetch(self, sql, *args):
            if "SELECT DISTINCT b.copy_generation" in sql:
                return [{"copy_generation": generation}]
            if "FROM location_native_copy_births" in sql:
                return [
                    {
                        "lineage_known": True,
                        "exclusive_input": True,
                        "selected": True,
                        "receiving_session": session_id,
                        "input_digest": digest,
                        "producer_kind": "native_mcp",
                    }
                ]
            assert "location_native_cache_heads" in sql
            return []

        async def fetchval(self, sql, *args):
            if "current_user" in sql:
                return "butler_chronicler_rw"
            if "JOIN location_runtime_context_dispositions d USING(input_generation)" in sql:
                assert args == (session_id, decision, manifest)
                return self.closed
            if "location_runtime_context_intents i" in sql:
                return self.pending
            if sql.startswith("SELECT count(*)"):
                return 0 if self.unknown else int(self.receipt is not None)
            assert sql.startswith("SELECT EXISTS")
            return False

        async def execute(self, sql, *args):
            self.writes.append((sql, args))
            if "INSERT INTO location_native_copy_dispositions " in sql:
                self.receipt = args[1]

    owner = Owner()
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt is None and not any("UPDATE sessions" in q for q, _ in owner.writes)
    owner.session.update(prompt="[Location input forgotten]", result="[Location output forgotten]")
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt is None  # A forged placeholder is not a durable own disposition.
    owner.closed = True
    owner.pending = True
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt is None  # ALL receiving inputs must be disposed under this plan.
    owner.pending = False
    owner.session["result"] = "changed late output"
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt is None
    owner.session["result"] = "[Location output forgotten]"
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt is not None
    assert owner.session["effective_system_prompt"] == "independent base sentinel"
    assert not any("DELETE FROM" in q for q, _ in owner.writes)
    original_receipt = owner.receipt
    await dispose_bound_native_copies(owner, decision)
    assert owner.receipt == original_receipt  # Same generation resumes its exact receipt.
    owner.unknown = True
    with pytest.raises(PolicyUnavailableError, match="disposition is unknown"):
        await dispose_bound_native_copies(owner, decision)
    assert owner.receipt == original_receipt  # Unknown readback cannot invent rollback/replacement.


async def _assert_core_question_context_values():
    """Planted software constructor/body controls, not real role/source/online proof."""
    import hashlib
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler.location_delegation_contexts import dispose_core_question_contexts
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_memory_context import _context_writers
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.delegation_source import _writers

    generation, session_id = uuid4(), uuid4()
    prompt, system = "Synthetic full question input", "Independent configured system"
    frozen = {
        "input_generation": generation,
        "receiving_session": session_id,
        "exclusive_input": True,
        "context_bytes": 0,
        "context_digest": hashlib.sha256(b"").digest(),
        "system_digest": hashlib.sha256(system.encode()).digest(),
        "prompt_digest": hashlib.sha256(prompt.encode()).digest(),
        "ended_receipt": uuid4(),
        "server_request": None,
    }
    frozen["bundle_digest"] = content_digest(
        {
            "loans": [],
            "context": frozen["context_digest"].hex(),
            "system": frozen["system_digest"].hex(),
            "prompt": frozen["prompt_digest"].hex(),
        }
    )
    session = {
        "prompt": prompt,
        "effective_system_prompt": system,
        "tool_calls": [],
        "completed_at": datetime.now(UTC),
        "result": "Synthetic source-derived result",
        "error": None,
        "prompt_provenance": [{"source": "independent", "sha": "unchanged"}],
    }
    captured = {
        "receiving_session": session_id,
        "input_generation": generation,
        "bundle_digest": frozen["bundle_digest"],
        "prompt_digest": frozen["prompt_digest"],
        "exclusive_input": True,
        "ended_receipt": uuid4(),
    }

    log = {"command": "Synthetic copied native prompt", "stderr": "Synthetic source output"}

    class Pool:
        role = "fixed_role"
        in_transaction = False
        receipts = {}
        descendants = False
        writes = []
        acquired = 0
        admitted_valid = True
        captured_valid = True
        captured_extra = False

        @asynccontextmanager
        async def acquire(self):
            self.acquired += 1
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "location_received_delegation_floors" in sql:
                return binding
            if "location_received_delegation_inputs" in sql:
                return {**binding, "exclusive_input": self.admitted_valid, "parent_count": 1}
            if "SELECT d.*,b.receiving_session" in sql:
                receipt = self.receipts.get(args[0])
                return (
                    None
                    if receipt is None
                    else {
                        **receipt,
                        "receiving_session": session_id,
                        "system_digest": frozen["system_digest"],
                    }
                )
            if "location_runtime_context_dispositions" in sql:
                return self.receipts.get(args[0])
            if "location_runtime_context_bindings" in sql:
                return frozen
            if "SELECT * FROM sessions" in sql:
                return session
            raise AssertionError("Unknown core context query")

        async def fetch(self, sql, *args):
            if "SELECT i.input_generation" in sql:
                return [{"input_generation": generation}]
            if "location_received_delegation_contexts" in sql:
                return (
                    [captured, dict(captured)]
                    if self.captured_extra
                    else ([captured] if self.captured_valid else [])
                )
            if "FROM location_runtime_tool_intents" in sql:
                assert args == (session_id,)
                return []
            if '"relationship".location_native_delegation_inputs' in sql:
                assert args == (generation,)
                return []
            if '"relationship".location_native_delegation_answers' in sql:
                assert args == (generation,)
                return []
            raise AssertionError("Unknown core context cohort")

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            if sql == "SELECT current_user":
                return self.role
            if "location_catalog_copy_lifetimes" in sql:
                return self.descendants
            if "session_process_logs" in sql:
                return (
                    log["command"]
                    not in {"[Location input forgotten]", "[Location-derived diagnostic forgotten]"}
                    or log["stderr"] is not None
                )
            raise AssertionError("Unknown core context predicate")

        async def execute(self, sql, *args):
            if "pg_advisory_xact_lock" in sql:
                assert self.in_transaction
                return
            assert self.in_transaction
            self.writes.append(sql)
            if "UPDATE session_process_logs" in sql:
                log.update(command="[Location input forgotten]", stderr=None)
                return
            if "UPDATE sessions" in sql:
                session.update(
                    prompt="[Location input forgotten]",
                    result="[Location output forgotten]",
                    tool_calls=[],
                    error=None,
                )
                return
            if "INSERT INTO location_runtime_context_dispositions" in sql:
                self.receipts[args[0]] = dict(
                    input_generation=args[0],
                    decision_id=args[1],
                    manifest_digest=args[2],
                    receipt_id=args[3],
                    reduced_system_digest=args[4],
                    reduced_provenance_digest=args[5],
                )
                return
            raise AssertionError("Unknown core context write")

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool,
        name="relationship",
        registry=object(),
        identity=("relationship", "fixed_role"),
    )
    binding = {
        "receiving_generation": uuid4(),
        "decision_id": uuid4(),
        "manifest_digest": b"m" * 32,
        "source_name": "chronicler",
        "question_generation": uuid4(),
        "ledger_id": uuid4(),
        "loan_id": uuid4(),
        "body_digest": b"b" * 32,
        "receiving_incarnation": runtime.incarnation,
    }
    captured.update(
        claim_generation=(claim_generation := uuid4()),
        reserved_claim=claim_generation,
        receiving_generation=binding["receiving_generation"],
        receiving_incarnation=runtime.incarnation,
    )
    # Explicit planted private registry, never advertised as constructor enrollment.
    _writers[pool] = runtime.delegation_writer
    _context_writers[pool] = runtime
    try:
        from butlers.chronicler.location_delegation_contexts import closed_question_context_input

        assert await closed_question_context_input(
            pool, '"relationship"', runtime, frozen, generation, binding
        )
        for key in ("captured_valid", "admitted_valid"):
            setattr(pool, key, False)
            assert not await closed_question_context_input(
                pool, '"relationship"', runtime, frozen, generation, binding
            )
            setattr(pool, key, True)
        pool.captured_extra = True
        assert not await closed_question_context_input(
            pool, '"relationship"', runtime, frozen, generation, binding
        )
        pool.captured_extra = False
        for key, damaged in (
            ("reserved_claim", None),
            ("reserved_claim", uuid4()),
            ("receiving_generation", uuid4()),
            ("prompt_digest", b"x" * 32),
            ("bundle_digest", b"x" * 32),
            ("ended_receipt", None),
        ):
            original = captured[key]
            captured[key] = damaged
            assert not await closed_question_context_input(
                pool, '"relationship"', runtime, frozen, generation, binding
            )
            captured[key] = original
        assert await closed_question_context_input(
            pool, '"relationship"', runtime, frozen, generation, binding
        )
        for row, key, damaged in (
            (frozen, "exclusive_input", False),
            (frozen, "ended_receipt", None),
            (frozen, "context_bytes", 1),
            (frozen, "bundle_digest", b"x" * 32),
            (captured, "reserved_claim", None),
            (captured, "reserved_claim", uuid4()),
            (captured, "ended_receipt", None),
            (captured, "receiving_incarnation", uuid4()),
            (session, "completed_at", None),
            (session, "prompt", "Changed independent body"),
            (session, "tool_calls", [{"name": "unknown_operation"}]),
        ):
            original = row[key]
            row[key] = damaged
            await dispose_core_question_contexts(runtime, binding)
            assert not pool.receipts and not pool.writes
            row[key] = original
        for key in ("admitted_valid", "captured_valid"):
            setattr(pool, key, False)
            await dispose_core_question_contexts(runtime, binding)
            assert not pool.receipts and not pool.writes
            setattr(pool, key, True)
        assert log == {
            "command": "Synthetic copied native prompt",
            "stderr": "Synthetic source output",
        }
        pool.descendants = True
        await dispose_core_question_contexts(runtime, binding)
        assert not pool.receipts and not pool.writes
        pool.descendants = False
        pool.role = "wrong_role"
        with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
            await dispose_core_question_contexts(runtime, binding)
        assert not pool.writes
        pool.role = "fixed_role"
        before = pool.acquired
        await dispose_core_question_contexts(runtime, binding)
        assert pool.acquired == before + 2  # Actual independent committed readback path.
        receipt = pool.receipts[generation]["receipt_id"]
        assert session["prompt"] == "[Location input forgotten]"
        assert session["result"] == "[Location output forgotten]"
        assert session["effective_system_prompt"] == system
        assert session["prompt_provenance"] == [{"source": "independent", "sha": "unchanged"}]
        assert log == {"command": "[Location input forgotten]", "stderr": None}
        writes = list(pool.writes)
        await dispose_core_question_contexts(runtime, binding)
        assert pool.receipts[generation]["receipt_id"] == receipt and pool.writes == writes
        log["command"] = "[Location-derived diagnostic forgotten]"
        await dispose_core_question_contexts(runtime, binding)
        assert pool.receipts[generation]["receipt_id"] == receipt and pool.writes == writes
        log["stderr"] = "Synthetic retained copy after unknown readback"
        with pytest.raises(
            PolicyUnavailableError, match="Committed native core context disposal is unknown"
        ):
            await dispose_core_question_contexts(runtime, binding)
        assert pool.receipts[generation]["receipt_id"] == receipt and pool.writes == writes
        log["stderr"] = None
        await dispose_core_question_contexts(runtime, binding)
        assert pool.receipts[generation]["receipt_id"] == receipt and pool.writes == writes
    finally:
        runtime.close()


async def _assert_native_answer_challenge_values():
    """Real private handler, planted owning cells; not online enrollment or SQL proof."""
    import json
    import time
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from starlette.requests import Request

    from butlers.chronicler.location_catalog_copies import _HEADER
    from butlers.chronicler.location_delegation_returns import _AnswerPending, answer_challenge
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import _ToolCopy

    class Pool:
        in_transaction = False
        available = True
        intent = True
        role = "fixed_role"

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            assert self.in_transaction and args == (pending.receiving,)
            assert "FROM location_received_answer_attempts" in sql
            return attempt if self.available else None

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            if sql == "SELECT current_user":
                return self.role
            assert self.in_transaction and "FROM location_runtime_tool_intents" in sql
            assert args == (tool.generation, tool.session)
            return self.intent

        async def execute(self, sql, *args):
            assert self.in_transaction and "pg_advisory_xact_lock" in sql

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool, name="relationship", registry=object(), identity=("relationship", "fixed_role")
    )
    tool = _ToolCopy(runtime, uuid4(), uuid4(), "delegate_wake", "core")
    pending = _AnswerPending(
        uuid4(), "chronicler", "synthetic-fixed-wake", uuid4(), time.monotonic() + 30, tool=tool
    )
    token = "synthetic-answer-challenge-token-32"
    runtime.delegation_writer.answer_pending[token] = pending
    attempt = {
        "ledger_id": pending.ledger,
        "source_name": pending.source,
        "wake_key": pending.wake_key,
        "receiving_incarnation": runtime.incarnation,
        "receiving_session": tool.session,
        "tool_generation": tool.generation,
        "server_request": None,
    }
    body = {
        "op": "answer_challenge",
        "ledger_id": str(pending.ledger),
        "source": pending.source,
        "wake_key": pending.wake_key,
    }
    # Callback is a separate HTTP invocation: no inherited caller ContextVar.
    expected = await answer_challenge(runtime.delegation_writer, token, body)
    assert expected["receiving_generation"] == str(pending.receiving)
    assert expected["receiving_incarnation"] == str(runtime.incarnation)
    for key in ("ledger_id", "source_name", "wake_key", "receiving_session", "tool_generation"):
        original = attempt[key]
        attempt[key] = (
            uuid4() if isinstance(original, type(pending.ledger)) else "synthetic different"
        )
        with pytest.raises(PolicyUnavailableError, match="attempt differs"):
            await answer_challenge(runtime.delegation_writer, token, body)
        attempt[key] = original
    for key in ("available", "intent"):
        setattr(pool, key, False)
        with pytest.raises(PolicyUnavailableError):
            await answer_challenge(runtime.delegation_writer, token, body)
        setattr(pool, key, True)
    tool.active = False
    with pytest.raises(PolicyUnavailableError, match="receiving tool differs"):
        await answer_challenge(runtime.delegation_writer, token, body)
    tool.active = True
    original_deadline = pending.deadline
    pending.deadline = float("-inf")
    with pytest.raises(PolicyUnavailableError, match="receiving lifetime differs"):
        await answer_challenge(runtime.delegation_writer, token, body)
    pending.deadline = original_deadline
    assert await answer_challenge(runtime.delegation_writer, token, body) == expected

    async def actual_control(raw: bytes):
        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/location-copy/v1",
                "headers": [(_HEADER.lower().encode(), token.encode())],
            },
            receive,
        )
        return await runtime.control(request)

    response = await actual_control(json.dumps(body).encode())
    assert response.status_code == 200 and json.loads(response.body) == expected
    assert (
        await actual_control(json.dumps({**body, "actor": "caller-forged"}).encode())
    ).status_code == 503
    duplicate = json.dumps(body).encode()[:-1] + b',"source":"caller-forged"}'
    assert (await actual_control(duplicate)).status_code == 503
    from butlers.chronicler import location_catalog_copies

    copied = []

    class TrackedBuffer(bytearray):
        def extend(self, body):
            copied.append(len(body))
            super().extend(body)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(location_catalog_copies, "bytearray", TrackedBuffer, raising=False)
        assert (await actual_control(b"x" * 8193)).status_code == 503
        assert copied == []  # Refuse the chunk before allocating a second copy.
        assert (await actual_control(json.dumps(body).encode())).status_code == 200
        assert copied == [len(json.dumps(body).encode()), 0]
    runtime.close()
    assert not runtime.delegation_writer.answer_pending


async def _assert_native_answer_schedule_values():
    """Owning same-transaction/readback species; planted cells, no online SQL credit."""
    import hashlib
    import time
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from uuid import uuid4

    from butlers.chronicler.location_delegation_answers import answer_bundle_digest
    from butlers.chronicler.location_delegation_returns import (
        _ReceivedAnswer,
        schedule_received_answer,
    )
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_tool_copies import _ToolCopy
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key
    from butlers.core.delegation_wake import _build_return_task_prompt

    ledger, generation, task = uuid4(), uuid4(), uuid4()
    canonical = {
        "id": ledger,
        "asking_butler": "relationship",
        "target_butler": "chronicler",
        "question": "Synthetic admitted location question",
        "answer": "Synthetic admitted answer",
        "answering_butler": "chronicler",
        "catalog_match_id": None,
        "catalog_score": None,
        "metadata": {},
        "status": "answered",
    }
    canonical["answer_digest"] = compute_answer_digest(canonical["answer"])
    canonical["wake_key"] = compute_wake_key(ledger, canonical["answer_digest"])
    digest = answer_bundle_digest(canonical)
    prompt = _build_return_task_prompt(
        ledger_id=ledger,
        asking_butler=canonical["asking_butler"],
        target_butler=canonical["target_butler"],
        question=canonical["question"],
        answer=canonical["answer"],
        wake_key=canonical["wake_key"],
        answer_digest=canonical["answer_digest"],
    )

    class Pool:
        in_transaction = False
        acquired = 0
        available = True
        fenced = False
        unknown = False
        changed_task = False
        fail_binding = False
        result = None
        task_body = None
        binding = None
        trace = []

        @asynccontextmanager
        async def acquire(self):
            self.acquired += 1
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            original = deepcopy((self.task_body, self.binding))
            enclosing = self.in_transaction
            self.in_transaction = True
            try:
                yield
            except BaseException:
                self.task_body, self.binding = original
                self.trace.append("rollback")
                raise
            else:
                self.trace.append("commit")
            finally:
                self.in_transaction = enclosing

        async def fetchrow(self, sql, *args):
            if "JOIN location_received_answer_attempts" in sql:
                assert self.in_transaction and args == (generation,)
                return (
                    {
                        "ledger_id": ledger,
                        "bundle_digest": digest,
                        "wake_key": canonical["wake_key"],
                        "receiving_incarnation": runtime.incarnation,
                    }
                    if self.available
                    else None
                )
            if "FROM public.delegation_ledger" in sql:
                assert args == (ledger,)
                return canonical
            if "FROM scheduled_tasks WHERE name" in sql:
                assert self.in_transaction
                return None
            assert "FROM location_received_answer_schedules" in sql and args == (generation,)
            if not self.in_transaction:
                self.trace.append("readback")
                if self.unknown:
                    return None
            return self.binding

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            if sql == "SELECT current_user":
                return "fixed_role"
            if "FROM location_received_answer_floors" in sql:
                assert self.in_transaction and args == (generation,)
                return self.fenced
            assert "FROM scheduled_tasks" in sql and args == (task,)
            return self.task_body

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "pg_advisory_xact_lock" in sql:
                self.trace.append("policy")
                return
            assert "INSERT INTO location_received_answer_schedules" in sql
            self.trace.append("binding")
            if self.fail_binding:
                raise RuntimeError("synthetic binding failure")
            self.binding = dict(
                receiving_generation=args[0], task_id=args[1], prompt_digest=args[2]
            )

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool, name="relationship", registry=object(), identity=("relationship", "fixed_role")
    )
    tool = _ToolCopy(runtime, uuid4(), uuid4(), "delegate_wake", "core")

    def admission():
        selected = _ReceivedAnswer(
            runtime.delegation_writer, generation, ledger, digest, time.monotonic() + 30, tool=tool
        )
        runtime.delegation_writer.receiving_answers[generation] = selected
        return selected

    async def write(conn, row):
        assert conn is pool and conn.in_transaction and row == canonical
        pool.trace.append("business")
        pool.task_body = prompt if not pool.changed_task else prompt + "independent suffix"
        return {"status": "ok", "task_id": str(task)}

    try:
        for field in ("available", "fenced"):
            setattr(pool, field, field == "fenced")
            with pytest.raises(PolicyUnavailableError):
                await schedule_received_answer(admission(), write)
            assert pool.task_body is None and pool.binding is None
            setattr(pool, field, field == "available")
        original = canonical["question"]
        canonical["question"] = "Changed unrelated question under same answer hash"
        with pytest.raises(PolicyUnavailableError, match="input differs"):
            await schedule_received_answer(admission(), write)
        assert "business" not in pool.trace
        canonical["question"] = original
        for field in ("changed_task", "fail_binding"):
            setattr(pool, field, True)
            with pytest.raises((PolicyUnavailableError, RuntimeError)):
                await schedule_received_answer(admission(), write)
            assert pool.task_body is None and pool.binding is None
            setattr(pool, field, False)
        pool.trace.clear()
        result = await schedule_received_answer(admission(), write)
        assert result == {"status": "ok", "task_id": str(task)}
        assert pool.trace.index("policy") < pool.trace.index("business")
        assert pool.trace.index("business") < pool.trace.index("binding")
        assert pool.trace.index("binding") < pool.trace.index("commit")
        assert pool.trace.index("commit") < pool.trace.index("readback")
        assert pool.binding["prompt_digest"] == hashlib.sha256(prompt.encode()).digest()
        assert pool.task_body == prompt
        assert not runtime.delegation_writer.receiving_answers
        committed = deepcopy(pool.binding)
        pool.unknown = True
        with pytest.raises(PolicyUnavailableError, match="Committed native return task is unknown"):
            await schedule_received_answer(admission(), write)
        assert pool.binding == committed and pool.task_body == prompt  # No false rollback.
        pool.unknown = False
        assert await schedule_received_answer(admission(), write) == result
        expired = admission()
        expired.deadline = float("-inf")
        with pytest.raises(PolicyUnavailableError, match="admitted lifetime differs"):
            await schedule_received_answer(expired, write)
        assert pool.binding == committed and pool.task_body == prompt
        # Exercise the real wake handler and same-connection reconciliation.
        # The admission boundary is planted here; this is software wiring,
        # never online source or PostgreSQL authority evidence.
        from butlers.core import delegation_source, delegation_wake

        prior_writer = delegation_source._writers.get(pool)
        pool.task_body = pool.binding = None

        async def admitted(actual_pool, selected_ledger, selected_wake):
            assert actual_pool is pool and selected_ledger == ledger
            assert selected_wake == canonical["wake_key"]
            return dict(canonical), admission()

        async def no_early_read(*args, **kwargs):
            raise AssertionError("full body read bypassed native reservation")

        async def create(actual, name, cron, body, **kwargs):
            assert actual is pool and actual.in_transaction
            assert name == f"delegate-return-{ledger}" and body == prompt
            pool.trace.append("business")
            pool.task_body = body
            return task

        async def owning_event(actual, *args, **kwargs):
            assert actual is pool and actual.in_transaction

        try:
            delegation_source.register_writer(pool, runtime.delegation_writer)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(delegation_source, "receive_answer", admitted)
                patch.setattr(delegation_wake, "get_delegation", no_early_read)
                patch.setattr(delegation_wake, "schedule_create", create)
                for name in (
                    "advance_wake_callback_routed",
                    "record_wake_task_created",
                    "record_wake_attempt",
                ):
                    patch.setattr(delegation_wake, name, owning_event)
                result = await delegation_wake.handle_delegate_wake(
                    pool,
                    ledger_id=ledger,
                    wake_key=canonical["wake_key"],
                    asking_butler="relationship",
                )
            assert result["status"] == "ok" and result["task_id"] == str(task)
            assert pool.task_body == prompt
            assert pool.binding["prompt_digest"] == hashlib.sha256(prompt.encode()).digest()
            assert not runtime.delegation_writer.receiving_answers
        finally:
            delegation_source.clear_writer(pool, runtime.delegation_writer)
            if prior_writer is not None:
                delegation_source.register_writer(pool, prior_writer)
    finally:
        runtime.close()


async def _assert_native_return_processing_values():
    """Real scheduler/context entry, with planted software source and owning rows."""
    import asyncio
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_delegation_answers import answer_bundle_digest
    from butlers.chronicler.location_delegation_returns import answer_challenge
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_input_binding import _dispatchers, register_dispatch_runtime
    from butlers.chronicler.location_memory_context import (
        _context_writers,
        begin_runtime_context,
        bind_context_session,
        capture_context_prompt,
        end_runtime_context,
        register_context_writer,
        verify_context_session,
    )
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_return_processing import (
        _bundle,
        current_scheduled_answer,
        scheduled_answer_scope,
    )
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key
    from butlers.core.delegation_source import clear_writer, register_writer

    ledger, task, source_incarnation = uuid4(), uuid4(), uuid4()
    canonical = dict(
        id=ledger,
        asking_butler="relationship",
        target_butler="chronicler",
        question="Synthetic admitted question",
        answer="Synthetic admitted answer",
        answering_butler="chronicler",
        status="answered",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
    )
    canonical["answer_digest"] = compute_answer_digest(canonical["answer"])
    canonical["wake_key"] = compute_wake_key(ledger, canonical["answer_digest"])
    source_digest = answer_bundle_digest(canonical)
    prompt = "Synthetic immutable return prompt"
    prompt_digest = hashlib.sha256(prompt.encode()).digest()
    parents = [
        dict(
            declared_receiving=uuid4(),
            receiving_generation=None,
            answer_generation=uuid4(),
            loan_id=uuid4(),
            source_incarnation=source_incarnation,
            bundle_digest=source_digest,
            prompt_digest=prompt_digest,
            scheduled_prompt=prompt,
            exclusive_input=True,
            parent_count=1,
            source_name="chronicler",
            ledger_id=ledger,
            wake_key=canonical["wake_key"],
            receiving_incarnation=None,
        )
        for _ in range(2)
    ]
    for row in parents:
        row["receiving_generation"] = row["declared_receiving"]
    assert _bundle(parents, prompt) == _bundle(list(reversed(parents)), prompt)
    for field, changed in (("exclusive_input", False), ("parent_count", 2)):
        altered = [parents[0], dict(parents[1], **{field: changed})]
        assert _bundle(altered, prompt) != _bundle(parents, prompt)
    for corrupt in (
        [parents[0], dict(parents[1], receiving_generation=None)],
        [parents[0], dict(parents[1], bundle_digest=None)],
        [parents[0], dict(parents[1], scheduled_prompt=prompt + "changed")],
        [parents[0], parents[0]],
        [parents[0], dict(parents[1], parent_count=True)],
        [parents[0], dict(parents[1], parent_count=-1)],
    ):
        with pytest.raises(PolicyUnavailableError):
            _bundle(corrupt, prompt)

    class Pool:
        in_transaction = False
        unknown = False
        fenced = False
        processing_started = False
        end_failure = None
        claims = {}
        claim_parents = []
        context = None
        ended = {}
        context_ended = {}
        context_intent = None
        answer_intent = None
        answer_context = None
        session = None
        trace = []

        def is_closing(self):
            return False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False
                self.trace.append("commit")

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "pg_advisory_xact_lock" in sql:
                self.trace.append("policy")
            elif "INSERT INTO location_runtime_context_ended" in sql:
                self.context_ended[args[0]] = args[1]
            elif "INSERT INTO location_received_answer_claims_ended" in sql:
                if self.end_failure is not None:
                    raise self.end_failure
                self.ended[args[0]] = args[1]
            elif "INSERT INTO location_received_answer_claim_parents" in sql:
                self.claim_parents.append(
                    dict(
                        claim_generation=args[0],
                        receiving_generation=args[1],
                        bundle_digest=args[2],
                    )
                )
            elif "INSERT INTO location_received_answer_claims" in sql:
                self.trace.append("processing_claim")
                self.claims[args[0]] = dict(
                    zip(
                        (
                            "claim_generation",
                            "task_id",
                            "prompt_digest",
                            "bundle_digest",
                            "parent_count",
                            "receiving_incarnation",
                            "exclusive_input",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_runtime_context_intents" in sql:
                self.context_intent = dict(input_generation=args[0], receiving_session=args[1])
                self.trace.append("context_intent")
            elif "INSERT INTO location_runtime_context_answer_intents" in sql:
                self.answer_intent = dict(input_generation=args[0], claim_generation=args[1])
            elif "INSERT INTO location_runtime_context_bindings" in sql:
                self.context = dict(
                    zip(
                        (
                            "input_generation",
                            "receiving_session",
                            "bundle_digest",
                            "context_digest",
                            "system_digest",
                            "prompt_digest",
                            "exclusive_input",
                            "context_bytes",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_answer_contexts" in sql:
                self.answer_context = dict(
                    zip(
                        (
                            "input_generation",
                            "claim_generation",
                            "receiving_session",
                            "bundle_digest",
                            "claim_bundle_digest",
                        ),
                        args,
                    )
                )
            else:
                raise AssertionError("unexpected owning return write")

        async def fetch(self, sql, *args):
            if "FROM location_received_answer_schedules" in sql:
                assert self.in_transaction and args == (task,)
                return parents
            assert "FROM location_received_answer_claim_parents" in sql
            assert not self.in_transaction
            return [p for p in self.claim_parents if p["claim_generation"] == args[0]]

        async def fetchrow(self, sql, *args):
            if "FROM public.delegation_ledger" in sql:
                return canonical
            if "FROM location_received_answer_attempts" in sql:
                assert self.in_transaction
                return next(
                    dict(
                        ledger_id=ledger,
                        source_name=r["source_name"],
                        wake_key=r["wake_key"],
                        receiving_incarnation=r["receiving_incarnation"],
                    )
                    for r in parents
                    if r["receiving_generation"] == args[0]
                )
            if "FROM location_received_answer_inputs i" in sql:
                assert self.in_transaction
                return dict(prompt=prompt, prompt_digest=prompt_digest)
            if "FROM location_received_answer_claims" in sql:
                self.trace.append("claim_readback")
                return None if self.unknown else self.claims.get(args[0])
            if "FROM location_runtime_context_bindings" in sql:
                return self.context
            if "FROM sessions" in sql:
                return self.session
            raise AssertionError("unexpected owning return row")

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            if sql == "SELECT current_user":
                return "fixed_role"
            if "FROM location_received_answer_floors" in sql:
                return self.fenced
            if "FROM location_runtime_context_ended" in sql:
                return self.context_ended.get(args[0])
            if "FROM location_received_answer_claims_ended" in sql:
                return self.ended.get(args[0])
            if "FROM location_runtime_context_intents" in sql:
                return self.context_intent["receiving_session"]
            if "FROM location_runtime_context_answer_intents" in sql:
                return self.answer_intent["claim_generation"]
            if "FROM location_runtime_context_bindings" in sql:
                return self.context is not None and self.context["receiving_session"] == args[1]
            if "FROM location_received_answer_contexts" in sql:
                return (
                    self.answer_context is not None
                    and self.answer_context["claim_generation"] == args[0 if len(args) == 1 else 1]
                )
            raise AssertionError("unexpected owning return lookup")

    class Registry:
        async def call_tool(self, name, args):
            assert name == "list_butlers" and args == {}
            return SimpleNamespace(
                data=[
                    dict(
                        name="chronicler",
                        eligibility_state="active",
                        endpoint_url="http://synthetic.example.test:41103/sse",
                    )
                ]
            )

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool,
        name="relationship",
        registry=Registry(),
        identity=("relationship", "fixed_role"),
    )
    for row in parents:
        row["receiving_incarnation"] = runtime.incarnation
    spawner = SimpleNamespace(_pool=pool)
    register_dispatch_runtime(spawner, object)
    register_context_writer(runtime)
    register_writer(pool, runtime.delegation_writer)

    async def exchange(endpoint, token, body):
        assert endpoint == "http://synthetic.example.test:41103/internal/location-retention/catalog"
        selected = next(r for r in parents if str(r["loan_id"]) == body["loan_id"])
        observed = await answer_challenge(
            runtime.delegation_writer,
            token,
            dict(
                op="answer_challenge",
                ledger_id=str(ledger),
                source="chronicler",
                wake_key=canonical["wake_key"],
            ),
        )
        assert observed["receiving_generation"] == str(selected["receiving_generation"])
        return dict(
            loan_id=str(selected["loan_id"]),
            answer_generation=str(selected["answer_generation"]),
            bundle_digest=source_digest.hex(),
            source_incarnation=str(source_incarnation),
            parent_count=1,
            exclusive_input=selected["exclusive_input"],
        )

    runtime.exchange = exchange
    try:
        for field in ("fenced", "unknown"):
            setattr(pool, field, True)
            with pytest.raises(PolicyUnavailableError):
                async with scheduled_answer_scope(pool, task, prompt):
                    pool.processing_started = True
            assert not pool.processing_started and current_scheduled_answer(pool) is None
            setattr(pool, field, False)
        pool.trace.clear()
        async with scheduled_answer_scope(pool, task, prompt):
            selected = current_scheduled_answer(pool)
            assert selected is not None and selected.exclusive
            assert pool.trace.index("processing_claim") < pool.trace.index("claim_readback")
            context = await begin_runtime_context(pool, spawner)
            try:
                actual = context[0]
                pool.session = dict(prompt=prompt, effective_system_prompt="synthetic fixed system")
                capture_context_prompt(None, pool.session["effective_system_prompt"])
                async with pool.transaction():
                    await bind_context_session(pool, pool, actual.session, prompt)
                await verify_context_session(pool, actual.session)
                assert pool.context["exclusive_input"] is True
                assert pool.answer_context["claim_bundle_digest"] == selected.bundle_digest
                assert pool.answer_intent["claim_generation"] == selected.generation
                assert pool.trace.index("claim_readback") < pool.trace.index("context_intent")
            finally:
                await end_runtime_context(context)
        assert current_scheduled_answer(pool) is None
        assert selected.generation in pool.ended
        parents[1]["exclusive_input"] = False
        async with scheduled_answer_scope(pool, task, prompt):
            assert not current_scheduled_answer(pool).exclusive
            context = await begin_runtime_context(pool, spawner)
            try:
                capture_context_prompt(None, "synthetic fixed system")
                async with pool.transaction():
                    await bind_context_session(pool, pool, context[0].session, prompt)
                await verify_context_session(pool, context[0].session)
                assert pool.context["exclusive_input"] is False
            finally:
                await end_runtime_context(context)
        assert current_scheduled_answer(pool) is None
        pool.end_failure = RuntimeError("synthetic secondary receipt failure")
        with pytest.raises(ValueError, match="synthetic primary failure"):
            async with scheduled_answer_scope(pool, task, prompt):
                raise ValueError("synthetic primary failure")
        assert current_scheduled_answer(pool) is None
        pool.end_failure = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            async with scheduled_answer_scope(pool, task, prompt):
                raise ValueError("synthetic primary failure")
        assert current_scheduled_answer(pool) is None
        pool.end_failure = None
    finally:
        clear_writer(pool, runtime.delegation_writer)
        _dispatchers.pop(pool, None)
        _context_writers.pop(pool, None)
        runtime.close()


async def _assert_native_answer_server_values():
    """Actual ASGI finalizer + own committed receipt; software rows, not SQL proof."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler.location_catalog_copies import (
        CatalogServerCopyLifetime,
        _server_copy_scope,
        _server_copy_scopes,
    )
    from butlers.chronicler.location_delegation_returns import finish_answer_server
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError

    class Pool:
        in_transaction = False
        role = "fixed_role"
        unknown = False
        attempts = {}
        receipts = {}

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            if sql == "SELECT current_user":
                return self.role
            assert "FROM location_received_answer_attempts" in sql and self.in_transaction
            return self.attempts.get(args[0]) == args[1:]

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "pg_advisory_xact_lock" in sql:
                return
            assert "INSERT INTO location_received_answer_server_finished" in sql
            self.receipts.setdefault(
                args[0],
                dict(receiving_generation=args[0], server_request=args[1], receipt_id=args[2]),
            )

        async def fetchrow(self, sql, *args):
            assert "FROM location_received_answer_server_finished" in sql
            if self.unknown and not self.in_transaction:
                return None
            return self.receipts.get(args[0])

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool, name="relationship", registry=object(), identity=("relationship", "fixed_role")
    )
    generation = uuid4()
    interrupted = False
    received = []
    sent = []

    async def app(scope, receive, send):
        actual = _server_copy_scope.get()
        assert actual is not None and _server_copy_scopes[actual.request] is actual
        assert actual.target == runtime.name
        # Planted own attempt models the actual committed admission writer.
        pool.attempts[generation] = (actual.request, runtime.incarnation)
        actual.answers.append((runtime, generation))
        received.append(actual.request)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": interrupted})
        if interrupted:
            raise ValueError("synthetic interrupted answer response")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = CatalogServerCopyLifetime(app, butler_name=runtime.name)
    scope = dict(type="http", method="POST", path="/sse", headers=[])
    try:
        interrupted = True
        with pytest.raises(ValueError, match="interrupted answer response"):
            await middleware(scope, receive, send)
        assert generation in pool.attempts and generation not in pool.receipts
        assert _server_copy_scope.get() is None and received[-1] not in _server_copy_scopes
        generation = uuid4()
        interrupted = False
        await middleware(scope, receive, send)
        assert generation in pool.receipts
        assert pool.receipts[generation]["server_request"] == received[-1]
        receipt = pool.receipts[generation]["receipt_id"]
        await finish_answer_server(runtime, generation, received[-1])
        assert pool.receipts[generation]["receipt_id"] == receipt
        with pytest.raises(PolicyUnavailableError, match="attempt differs"):
            await finish_answer_server(runtime, generation, uuid4())
        assert pool.receipts[generation]["receipt_id"] == receipt
        pool.unknown = True
        with pytest.raises(PolicyUnavailableError, match="lifetime is unknown"):
            await finish_answer_server(runtime, generation, received[-1])
        assert pool.receipts[generation]["receipt_id"] == receipt
        pool.unknown = False
        pool.role = "wrong_role"
        with pytest.raises(PolicyUnavailableError, match="owning writer differs"):
            await finish_answer_server(runtime, generation, received[-1])
        assert pool.receipts[generation]["receipt_id"] == receipt
        assert _server_copy_scope.get() is None and received[-1] not in _server_copy_scopes
    finally:
        runtime.close()


async def _assert_native_answer_cohort_values():
    """Complete owning selector with planted inputs; SQL selection remains unproved."""
    import hashlib
    from copy import deepcopy
    from uuid import uuid4

    from butlers.chronicler.location_answer_disposal import source_answer_cohort
    from butlers.chronicler.location_delegation_answers import answer_bundle_digest
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key

    ledger, generation, decision, session, context, tool = [uuid4() for _ in range(6)]
    canonical = dict(
        id=ledger,
        asking_butler="relationship",
        target_butler="chronicler",
        answering_butler="chronicler",
        question="Synthetic immutable source question",
        answer="Synthetic immutable source answer",
        metadata={},
        catalog_match_id=None,
        catalog_score=None,
        status="answered",
    )
    canonical["answer_digest"] = compute_answer_digest(canonical["answer"])
    canonical["wake_key"] = compute_wake_key(ledger, canonical["answer_digest"])
    header = dict(
        answer_generation=generation,
        ledger_id=ledger,
        parent_count=2,
        exclusive_input=True,
        body_digest=hashlib.sha256(canonical["answer"].encode()).digest(),
        bundle_digest=answer_bundle_digest(canonical),
        receiving_session=session,
        context_generation=context,
        tool_generation=tool,
    )
    parents = [
        dict(parent_kind="native_copy", parent_generation=uuid4(), parent_digest=b"p" * 32)
        for _ in range(2)
    ]
    births = {
        parent["parent_generation"]: [
            dict(input_digest=b"p" * 32, lineage_known=True, exclusive_input=True, selected=True)
        ]
        for parent in parents
    }
    plan = dict(decision_id=str(decision), manifest_digest="ab" * 32, catalog_loans=[])

    class Pool:
        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "chronicler"
            assert sql == "SELECT current_user"
            return "fixed_role"

        async def execute(self, sql, *args):
            assert "pg_advisory_xact_lock" in sql

        async def fetchrow(self, sql, *args):
            if "FROM location_retention_policy" in sql:
                return {"version": 1}
            if "FROM location_native_delegation_answer_dispositions" in sql:
                assert args == (generation,)
                return None
            assert "FROM public.delegation_ledger" in sql and args == (ledger,)
            return canonical

        async def fetch(self, sql, *args):
            if "FROM location_native_delegation_answers" in sql:
                return [header] if args == (None,) else []
            if "FROM location_native_delegation_answer_parents" in sql:
                assert args == (generation,)
                return parents
            if "FROM location_native_copy_births" in sql:
                assert args[1] == decision
                return births.get(args[0], [])
            assert "FROM location_native_answer_loans" in sql and args == (generation,)
            return []

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool, name="chronicler", registry=object(), identity=("chronicler", "fixed_role")
    )
    try:
        full = await source_answer_cohort(runtime, pool, plan)
        assert len(full) == 1 and full[0]["complete_input"] is True
        assert (
            full[0]["parent_count"] == 2
            and full[0]["bundle_digest"] == header["bundle_digest"].hex()
        )
        selected = parents.pop()
        incomplete = await source_answer_cohort(runtime, pool, plan)
        assert len(incomplete) == 1 and incomplete[0]["complete_input"] is False
        assert incomplete[0]["parent_count"] == 2  # Original count never shrinks.
        parents.append(selected)
        original = deepcopy(births[selected["parent_generation"]])
        births[selected["parent_generation"]][0]["input_digest"] = b"x" * 32
        assert (await source_answer_cohort(runtime, pool, plan))[0]["complete_input"] is False
        births[selected["parent_generation"]] = original
        parents.append(
            dict(parent_kind="native_copy", parent_generation=uuid4(), parent_digest=b"p" * 32)
        )
        assert (await source_answer_cohort(runtime, pool, plan))[0]["complete_input"] is False
        parents.pop()
        parents.append(dict(parents[0]))
        with pytest.raises(PolicyUnavailableError, match="parent set differs"):
            await source_answer_cohort(runtime, pool, plan)
        parents.pop()
        original_question = canonical["question"]
        canonical["question"] += " unrelated substitution"
        with pytest.raises(PolicyUnavailableError, match="answer body changed"):
            await source_answer_cohort(runtime, pool, plan)
        canonical["question"] = original_question
        assert await source_answer_cohort(runtime, pool, plan) == full
        saved_parents, parents = parents, []
        header["parent_count"] = 0
        unknown = await source_answer_cohort(runtime, pool, plan)
        assert len(unknown) == 1 and unknown[0]["complete_input"] is False
        parents = saved_parents
        header["parent_count"] = 2
        assert await source_answer_cohort(runtime, pool, plan) == full
    finally:
        runtime.close()


async def _assert_native_answer_receiver_values():
    """Positioned exact floor/task/receipt controls; software SQL double only."""
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler import location_answer_disposal as disposal
    from butlers.chronicler.location_policy import PolicyUnavailableError

    incarnation, decision, ledger, generation, loan, source_incarnation = [
        uuid4() for _ in range(6)
    ]
    plan = dict(
        decision_id=str(decision),
        manifest_digest=(b"m" * 32).hex(),
        source_name="relationship",
        source_incarnation=str(source_incarnation),
    )
    answer = dict(
        answer_generation=str(uuid4()),
        ledger_id=str(ledger),
        source_name="relationship",
        bundle_digest=(b"b" * 32).hex(),
        complete_input=True,
    )
    selected = dict(
        loan_id=str(loan),
        receiving_generation=str(generation),
        receiver_name="chronicler",
        receiving_incarnation=str(incarnation),
        source_incarnation=str(source_incarnation),
        bundle_digest=answer["bundle_digest"],
    )
    runtime = SimpleNamespace(
        name="chronicler",
        incarnation=incarnation,
        active=True,
        delegation_writer=SimpleNamespace(receiving_answers={}, answer_pending={}),
    )
    binding = disposal._answer_floor_binding(runtime, plan, answer, selected)
    for changed in (
        {**selected, "source_incarnation": str(uuid4())},
        {**selected, "receiving_incarnation": str(uuid4())},
        {**selected, "bundle_digest": (b"x" * 32).hex()},
        {**selected, "receiver_name": "finance"},
    ):
        with pytest.raises(PolicyUnavailableError, match="floor cohort differs"):
            disposal._answer_floor_binding(runtime, plan, answer, changed)
    assert binding["loan_id"] == loan
    prompt, task = "full synthetic original return", uuid4()

    class Pool:
        in_transaction = False
        floors = {}
        dispositions = {}
        finished = False
        unresolved = False
        tool_finished = False
        unknown = False
        sibling_qualified = True
        attempt = dict(
            source_name="relationship",
            ledger_id=ledger,
            receiving_incarnation=incarnation,
            server_request=uuid4(),
            tool_generation=None,
            receiving_session=None,
        )
        schedule = dict(
            task_id=task,
            prompt=prompt,
            enabled=True,
            prompt_digest=hashlib.sha256(prompt.encode()).digest(),
        )

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            # Model rollback for the tested floor/task/receipt failure paths.
            before = (dict(self.floors), dict(self.dispositions), dict(self.schedule))
            try:
                yield
            except BaseException:
                self.floors, self.dispositions, self.schedule = before
                raise
            finally:
                self.in_transaction = False

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "INSERT INTO location_received_answer_floors" in sql:
                self.floors.setdefault(args[0], dict(zip(disposal._FLOOR_KEYS, args, strict=True)))
            elif "INSERT INTO location_received_answer_qualifications" in sql:
                assert args[0] == generation and args[0] in self.floors
            elif "UPDATE scheduled_tasks" in sql:
                assert args[0] == task
                self.schedule.update(prompt=args[1], enabled=False)
            elif "INSERT INTO location_received_answer_dispositions" in sql:
                self.dispositions[args[0]] = dict(
                    receipt_id=args[1], task_id=args[2], reduced_prompt_digest=args[3]
                )
            else:
                raise AssertionError("Unexpected answer disposal write")

        async def fetchrow(self, sql, *args):
            if "SELECT tool_calls,completed_at FROM sessions" in sql:
                return None
            if "FROM location_received_answer_attempts" in sql:
                return dict(self.attempt)
            if "FROM location_received_answer_inputs" in sql:
                return {
                    key: binding[key]
                    for key in (
                        "source_name",
                        "answer_generation",
                        "loan_id",
                        "bundle_digest",
                        "source_incarnation",
                    )
                } | dict(parent_count=2, exclusive_input=True)
            if "FROM location_received_answer_schedules" in sql:
                return dict(self.schedule)
            if "JOIN location_received_answer_dispositions" in sql:
                assert self.in_transaction
                if self.unknown:
                    return None
                return (
                    self.floors.get(generation, {})
                    | self.dispositions.get(generation, {})
                    | {"prompt": self.schedule["prompt"], "enabled": self.schedule["enabled"]}
                )
            if "FROM location_received_answer_floors" in sql:
                return self.floors.get(args[0])
            raise AssertionError("Unexpected answer disposal read")

        async def fetch(self, sql, *args):
            assert "FROM location_received_answer_schedules" in sql
            row = {
                key: binding[key]
                for key in (
                    "receiving_generation",
                    "source_name",
                    "answer_generation",
                    "loan_id",
                    "bundle_digest",
                    "source_incarnation",
                    "receiving_incarnation",
                    "ledger_id",
                    "decision_id",
                    "manifest_digest",
                )
            }
            row.update(
                declared_receiving=generation,
                prompt_digest=self.schedule["prompt_digest"],
                exclusive_input=True,
                parent_count=2,
                qualified=self.sibling_qualified,
                floor_digest=binding["bundle_digest"],
                floor_loan=binding["loan_id"],
                floor_answer=binding["answer_generation"],
                floor_source=binding["source_name"],
                floor_source_incarnation=binding["source_incarnation"],
                floor_incarnation=incarnation,
                floor_ledger=ledger,
                server_request=self.attempt["server_request"],
                tool_generation=self.attempt["tool_generation"],
                receiving_session=self.attempt["receiving_session"],
                wake_key="synthetic fixed wake",
            )
            return [row]

        async def fetchval(self, sql, *args):
            if "SELECT receipt_id FROM location_received_answer_dispositions" in sql:
                return self.dispositions.get(args[0], {}).get("receipt_id")
            if "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions" in sql:
                return bool(self.dispositions)
            if "FROM location_received_answer_server_finished" in sql:
                return self.finished
            if "FROM location_runtime_tool_intents" in sql:
                return self.tool_finished
            if (
                "FROM location_received_answer_claim_parents" in sql
                or "FROM location_received_answer_claims c WHERE c.task_id" in sql
            ):
                return self.unresolved
            if "FROM scheduled_tasks" in sql:
                return self.schedule["prompt"] == args[1] and not self.schedule["enabled"]
            raise AssertionError("Unexpected answer disposal condition")

    pool = Pool()
    runtime.domain = pool

    async def lock(conn):
        assert conn is pool and conn.in_transaction

    runtime.lock_domain = lock
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) is None
    assert pool.floors[generation] == binding and pool.schedule["prompt"] == prompt
    pool.finished = True
    assert await disposal._close_answer_receiver(runtime, binding, complete=False) is None
    assert pool.floors[generation] == binding and not pool.dispositions
    pool.unresolved = True
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) is None
    assert not pool.dispositions and pool.schedule["prompt"] == prompt
    pool.unresolved = False
    pool.attempt["tool_generation"], pool.attempt["receiving_session"] = uuid4(), uuid4()
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) is None
    assert not pool.dispositions  # Server completion never attests the Tool context.
    pool.tool_finished = True
    runtime.delegation_writer.receiving_answers[generation] = object()
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) is None
    runtime.delegation_writer.receiving_answers.clear()
    pool.sibling_qualified = False
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) is None
    assert not pool.dispositions and pool.schedule["prompt"] == prompt
    pool.sibling_qualified = True
    pool.schedule["prompt"] = "changed current task"
    with pytest.raises(PolicyUnavailableError, match="return task changed"):
        await disposal._close_answer_receiver(runtime, binding, complete=True)
    assert pool.schedule["prompt"] == "changed current task" and not pool.dispositions
    pool.schedule["prompt"] = prompt
    receipt = await disposal._close_answer_receiver(runtime, binding, complete=True)
    assert receipt is not None and pool.dispositions[generation]["receipt_id"] == receipt
    assert pool.schedule["prompt"] == disposal._REDUCED_RETURN and not pool.schedule["enabled"]
    assert await disposal._close_answer_receiver(runtime, binding, complete=True) == receipt
    pool.schedule["prompt"] = "post-COMMIT changed task"
    with pytest.raises(PolicyUnavailableError, match="Committed native answer task differs"):
        await disposal.answer_receiver_status(runtime, decision, receipt)
    assert pool.dispositions[generation]["receipt_id"] == receipt
    pool.schedule["prompt"] = disposal._REDUCED_RETURN
    pool.unknown = True
    with pytest.raises(PolicyUnavailableError, match="disposition is unavailable"):
        await disposal._close_answer_receiver(runtime, binding, complete=True)
    assert pool.dispositions[generation]["receipt_id"] == receipt
    pool.unknown = False
    with pytest.raises(PolicyUnavailableError, match="receiving floor differs"):
        await disposal._close_answer_receiver(
            runtime, binding | {"loan_id": uuid4()}, complete=True
        )
    assert pool.dispositions[generation]["receipt_id"] == receipt


async def _assert_native_answer_context_values():
    """Full declared return ancestry, not a smaller surviving JOIN; software only."""
    import hashlib
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_answer_disposal import closed_answer_context_input
    from butlers.chronicler.location_return_processing import _bundle

    runtime = SimpleNamespace(
        incarnation=uuid4(),
        delegation_writer=SimpleNamespace(receiving_answers={}, answer_pending={}),
    )
    claim, generation, session, decision, first, second = [uuid4() for _ in range(6)]
    prompt = "synthetic complete two-answer return"
    binding = dict(
        receiving_generation=first,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        receiving_incarnation=runtime.incarnation,
    )
    parents = []
    for receiving in (first, second):
        answer, loan, ledger, source_inc = [uuid4() for _ in range(4)]
        parents.append(
            dict(
                original_receiving=receiving,
                declared_receiving=receiving,
                receiving_generation=receiving,
                original_digest=b"b" * 32,
                bundle_digest=b"b" * 32,
                source_name="relationship",
                answer_generation=answer,
                loan_id=loan,
                ledger_id=ledger,
                source_incarnation=source_inc,
                receiving_incarnation=runtime.incarnation,
                parent_count=2,
                exclusive_input=True,
                wake_key="fixed wake",
                scheduled_prompt=prompt,
                prompt_digest=hashlib.sha256(prompt.encode()).digest(),
                decision_id=decision,
                manifest_digest=b"m" * 32,
                floor_digest=b"b" * 32,
                floor_source="relationship",
                floor_answer=answer,
                floor_loan=loan,
                floor_source_incarnation=source_inc,
                floor_incarnation=runtime.incarnation,
                floor_ledger=ledger,
                floor_complete=True,
            )
        )
    original_bundle = _bundle(parents, prompt)
    frozen = dict(
        receiving_session=session,
        bundle_digest=b"f" * 32,
        prompt_digest=hashlib.sha256(prompt.encode()).digest(),
    )
    captured = dict(
        claim_generation=claim,
        reserved_claim=claim,
        receiving_session=session,
        bundle_digest=b"f" * 32,
        prompt_digest=frozen["prompt_digest"],
        original_bundle=original_bundle,
        claim_bundle_digest=original_bundle,
        exclusive_input=True,
        receiving_incarnation=runtime.incarnation,
        ended_receipt=uuid4(),
        parent_count=2,
    )

    class Conn:
        rows = parents

        async def fetchrow(self, sql, *args):
            assert "FROM relationship.location_received_answer_contexts" in sql
            return self.captured

        async def fetch(self, sql, *args):
            assert "LEFT JOIN relationship.location_received_answer_inputs" in sql
            return self.rows

    conn = Conn()
    conn.captured = captured
    assert await closed_answer_context_input(
        conn, "relationship", runtime, frozen, generation, binding
    )
    conn.rows = parents[:1]
    assert not await closed_answer_context_input(
        conn, "relationship", runtime, frozen, generation, binding
    )
    for key, changed in (
        ("floor_complete", False),
        ("floor_loan", uuid4()),
        ("exclusive_input", False),
        ("original_digest", b"x" * 32),
        ("decision_id", uuid4()),
    ):
        conn.rows = [parents[0], parents[1] | {key: changed}]
        assert not await closed_answer_context_input(
            conn, "relationship", runtime, frozen, generation, binding
        )
    conn.rows = parents + [parents[0]]
    assert not await closed_answer_context_input(
        conn, "relationship", runtime, frozen, generation, binding
    )
    conn.rows = parents
    conn.captured = captured | {"ended_receipt": None}
    assert not await closed_answer_context_input(
        conn, "relationship", runtime, frozen, generation, binding
    )
    conn.captured = captured
    assert await closed_answer_context_input(
        conn, "relationship", runtime, frozen, generation, binding
    )

    from butlers.chronicler.location_answer_disposal import _closed_return_task_cohort

    class TaskConn:
        rows = [
            row
            | {
                "qualified": True,
                "server_request": uuid4(),
                "tool_generation": None,
                "receiving_session": None,
                "wake_key": "fixed wake",
            }
            for row in parents
        ]
        unresolved = False
        finished = True

        async def fetch(self, sql, *args):
            assert "WHERE s.task_id=$1" in sql
            return self.rows

        async def fetchval(self, sql, *args):
            if "FROM location_received_answer_claims c WHERE c.task_id" in sql:
                return self.unresolved
            assert "FROM location_received_answer_server_finished" in sql
            return self.finished

    task_conn = TaskConn()
    schedule = dict(task_id=uuid4(), prompt_digest=frozen["prompt_digest"])
    assert await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    original_task_rows = task_conn.rows
    task_conn.unresolved = True
    assert not await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    assert parents[0]["scheduled_prompt"] == prompt and parents[1]["scheduled_prompt"] == prompt
    task_conn.unresolved = False
    runtime.delegation_writer.receiving_answers[second] = object()
    assert not await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    runtime.delegation_writer.receiving_answers.clear()
    task_conn.finished = False
    assert not await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    task_conn.finished = True
    assert await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    task_conn.rows = [task_conn.rows[0], task_conn.rows[1] | {"qualified": False}]
    assert not await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    task_conn.rows = list(original_task_rows)
    task_conn.rows[1] = task_conn.rows[1] | {"exclusive_input": False}
    assert not await _closed_return_task_cohort(task_conn, runtime, schedule, binding)
    task_conn.rows = list(original_task_rows)
    assert await _closed_return_task_cohort(task_conn, runtime, schedule, binding)


async def _assert_metadata_wake_tool_values():
    """Locator-only actual execution witness, no generic Tool/session disposal."""
    from uuid import uuid4

    from butlers.chronicler.location_answer_disposal import metadata_wake_tool_finished
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    generation, tool, session, ledger, task = [uuid4() for _ in range(5)]
    attempt = dict(
        receiving_generation=generation,
        tool_generation=tool,
        receiving_session=session,
        ledger_id=ledger,
        wake_key="synthetic fixed wake",
    )
    result = dict(status="ok", ledger_id=str(ledger), wake_state="task_created", task_id=str(task))
    fingerprint = fingerprint_tool_call_payload(
        dict(ledger_id=str(ledger), wake_key=attempt["wake_key"])
    )
    record = dict(
        name="delegate_wake",
        module="core",
        outcome="success",
        input_fingerprint=fingerprint,
        result=result,
    )
    witness = dict(
        tool_generation=tool,
        tool_name="delegate_wake",
        module_name="core",
        input_digest=bytes.fromhex(fingerprint),
        outcome="success",
        exclusive_inputs=True,
        result_digest=bytes.fromhex(fingerprint_tool_call_payload(result)),
    )

    class Conn:
        ended = True
        calls = [record]
        rows = [witness]
        schedule = True

        async def fetchrow(self, sql, *args):
            assert args == (session,) and "FROM sessions" in sql
            return dict(tool_calls=self.calls, completed_at=object() if self.ended else None)

        async def fetch(self, sql, *args):
            assert args == (session,) and "t.tool_name='delegate_wake'" in sql
            return self.rows

        async def fetchval(self, sql, *args):
            assert "FROM location_received_answer_schedules" in sql and args == (generation, task)
            return self.schedule

    conn = Conn()
    assert await metadata_wake_tool_finished(conn, attempt)
    conn.calls = []
    assert not await metadata_wake_tool_finished(conn, attempt)
    conn.calls = [record, record]
    assert not await metadata_wake_tool_finished(conn, attempt)
    conn.rows = [witness, witness | {"tool_generation": uuid4()}]
    assert await metadata_wake_tool_finished(conn, attempt)
    conn.calls = [record]
    conn.rows = [witness]
    conn.ended = False
    assert not await metadata_wake_tool_finished(conn, attempt)
    conn.ended = True
    conn.rows = [witness | {"exclusive_inputs": False}]
    assert not await metadata_wake_tool_finished(conn, attempt)
    conn.rows = [witness]
    conn.schedule = False
    assert not await metadata_wake_tool_finished(conn, attempt)
    conn.schedule = True
    for added in ({"answer": "raw copied synthetic answer"}, {"reconciled": "caller string"}):
        altered = result | added
        conn.calls = [record | {"result": altered}]
        conn.rows = [
            witness | {"result_digest": bytes.fromhex(fingerprint_tool_call_payload(altered))}
        ]
        assert not await metadata_wake_tool_finished(conn, attempt)
    conn.calls = [record]
    conn.rows = [witness]
    assert await metadata_wake_tool_finished(conn, attempt)


async def _assert_native_answer_source_values():
    """Owning source protocol sequence/value refusal, not real route/SQL proof."""
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_answer_sources import (
        _REDUCED_ANSWER,
        disposed_answer_matches,
        receiver_observation_matches,
        reconcile_answer_receivers,
    )
    from butlers.chronicler.location_delegation_answers import answer_bundle_digest
    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key

    ledger, generation, decision, incarnation, receiving, receiver_inc, loan_id, receipt = [
        uuid4() for _ in range(8)
    ]
    canonical = dict(
        id=ledger,
        asking_butler="relationship",
        target_butler="chronicler",
        answering_butler="chronicler",
        question="Synthetic fixed question",
        answer="Synthetic original answer",
        metadata={},
        catalog_match_id=None,
        catalog_score=None,
        status="answered",
    )
    canonical["answer_digest"] = compute_answer_digest(canonical["answer"])
    canonical["wake_key"] = compute_wake_key(ledger, canonical["answer_digest"])
    header = dict(
        answer_generation=generation,
        ledger_id=ledger,
        body_digest=hashlib.sha256(canonical["answer"].encode()).digest(),
        bundle_digest=answer_bundle_digest(canonical),
    )
    terminal = dict(
        answer_generation=generation,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        body_digest=header["body_digest"],
        bundle_digest=header["bundle_digest"],
        question_digest=question_digest(canonical),
        wake_key=canonical["wake_key"],
        reduced_digest=hashlib.sha256(_REDUCED_ANSWER.encode()).digest(),
    )
    canonical["answer"] = _REDUCED_ANSWER
    runtime = SimpleNamespace(name="chronicler", incarnation=incarnation)
    plan = dict(
        decision_id=str(decision), manifest_digest=(b"m" * 32).hex(), source_name=runtime.name
    )
    assert disposed_answer_matches(runtime, header, canonical, terminal, plan)
    for changed in (
        {"answer": "changed"},
        {"question": "changed"},
        {"wake_key": "changed"},
        {"answer_digest": "ab" * 32},
        {"target_butler": "other"},
        {"status": "routed"},
    ):
        assert not disposed_answer_matches(
            runtime, header, {**canonical, **changed}, terminal, plan
        )
    for changed in (
        {"bundle_digest": None},
        {"question_digest": None},
        {"wake_key": None},
        {"reduced_digest": b"x" * 32},
        {"body_digest": b"x" * 32},
        {"decision_id": uuid4()},
    ):
        assert not disposed_answer_matches(
            runtime, header, canonical, {**terminal, **changed}, plan
        )
    answer = dict(
        source_name=runtime.name,
        answer_generation=str(generation),
        ledger_id=str(ledger),
        body_digest=header["body_digest"].hex(),
        bundle_digest=header["bundle_digest"].hex(),
        complete_input=True,
    )
    loan = dict(
        loan_id=str(loan_id),
        receiver_name="relationship",
        source_incarnation=str(incarnation),
        receiving_generation=str(receiving),
        receiving_incarnation=str(receiver_inc),
        bundle_digest=answer["bundle_digest"],
    )
    result = dict(
        source_name=runtime.name,
        source_incarnation=str(incarnation),
        decision_id=plan["decision_id"],
        manifest_digest=plan["manifest_digest"],
        answer_generation=answer["answer_generation"],
        ledger_id=str(ledger),
        **{
            key: loan[key]
            for key in ("loan_id", "receiving_generation", "receiving_incarnation", "bundle_digest")
        },
        receipt_id=str(receipt),
    )
    assert receiver_observation_matches(runtime, plan, answer, loan, result)
    for key in result:
        if key != "receipt_id":
            assert not receiver_observation_matches(
                runtime, plan, answer, loan, {**result, key: "changed"}
            )
    assert not receiver_observation_matches(
        runtime, plan, {**answer, "complete_input": False}, loan, result
    )
    assert not receiver_observation_matches(
        runtime, plan, answer, {**loan, "source_incarnation": str(uuid4())}, result
    )

    # Child receipt selection cannot bless another same-name Tool execution.
    from butlers.chronicler.location_memory_context import captured_artifact_calls
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    answer_call = dict(
        name="delegate_answer",
        module="core",
        outcome="success",
        input_fingerprint="ab" * 32,
        result=dict(status="ok", ledger_id=str(ledger), answer_recorded=True),
    )
    witness = dict(
        tool_generation=uuid4(),
        tool_name="delegate_answer",
        module_name="core",
        outcome="success",
        input_digest=bytes.fromhex(answer_call["input_fingerprint"]),
        result_digest=bytes.fromhex(fingerprint_tool_call_payload(answer_call["result"])),
        exclusive_inputs=True,
    )
    closed = dict(tool_generation=witness["tool_generation"])
    assert not captured_artifact_calls([answer_call], [], [witness])
    assert captured_artifact_calls([answer_call], [], [witness], closed_answers=[closed])
    assert not captured_artifact_calls(
        [answer_call], [], [witness], closed_answers=[dict(tool_generation=uuid4())]
    )
    assert not captured_artifact_calls(
        [answer_call], [], [{**witness, "exclusive_inputs": False}], closed_answers=[closed]
    )
    assert not captured_artifact_calls(
        [answer_call, answer_call], [], [witness], closed_answers=[closed]
    )
    sibling_call = {**answer_call, "input_fingerprint": "cd" * 32}
    sibling = {**witness, "tool_generation": uuid4(), "input_digest": bytes.fromhex("cd" * 32)}
    assert not captured_artifact_calls(
        [answer_call, sibling_call], [], [witness, sibling], closed_answers=[closed]
    )
    assert captured_artifact_calls(
        [answer_call, sibling_call],
        [],
        [witness, sibling],
        closed_answers=[closed, dict(tool_generation=sibling["tool_generation"])],
    )

    class Pool:
        def __init__(self):
            self.tx = False
            self.trace = []
            self.observed = None
            self.unknown = False
            self.changed = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            assert not self.tx
            self.tx = True
            try:
                yield
            finally:
                self.tx = False
                self.trace.append("commit")

        async def fetchrow(self, sql, *args):
            if "FROM location_native_answer_loans" in sql:
                assert self.tx and args == (loan_id,)
                return dict(
                    answer_generation=generation,
                    ledger_id=ledger,
                    body_digest=header["body_digest"],
                    answer_bundle=header["bundle_digest"],
                    bundle_digest=header["bundle_digest"],
                    receiver_name="relationship",
                    source_incarnation=uuid4() if self.changed else incarnation,
                    receiving_generation=receiving,
                    receiving_incarnation=receiver_inc,
                )
            assert "FROM location_native_answer_observations" in sql and args == (loan_id,)
            assert not self.tx
            self.trace.append("readback")
            return None if self.unknown else self.observed

        async def execute(self, sql, *args):
            assert self.tx and "INSERT INTO location_native_answer_observations" in sql
            self.trace.append("write")
            self.observed = dict(
                loan_id=args[0],
                decision_id=args[1],
                manifest_digest=args[2],
                receiver_receipt=args[3],
            )

    pool = Pool()
    runtime.domain = pool

    async def lock(conn):
        assert conn is pool and pool.tx
        pool.trace.append("lock")

    runtime.lock_domain = lock
    pending = False
    foreign = False

    async def route(target, tool, args):
        assert not pool.tx and target == "relationship"
        pool.trace.append(tool)
        if tool == "location_retention_prepare_answer":
            assert args == dict(decision_id=str(decision), loan_id=str(loan_id))
            return dict(
                decision_id=str(decision),
                loan_id=str(loan_id),
                receipt_id=None if pending else str(receipt),
            )
        assert tool == "location_retention_answer_status"
        assert args == dict(decision_id=str(decision), receipt_id=str(receipt))
        return {**result, "source_incarnation": str(uuid4())} if foreign else result

    runtime.routed_tool = route
    answer["loans"] = [loan]
    plan["answer_cohort"] = [answer]
    await reconcile_answer_receivers(runtime, plan)
    assert pool.observed["receiver_receipt"] == receipt
    assert pool.trace.index("location_retention_answer_status") < pool.trace.index("lock")
    assert pool.trace.index("write") < pool.trace.index("commit") < pool.trace.index("readback")
    pool.trace.clear()
    pending = True
    await reconcile_answer_receivers(runtime, plan)
    assert pool.trace == ["location_retention_prepare_answer"]
    pending = False
    foreign = True
    pool.trace.clear()
    with pytest.raises(PolicyUnavailableError, match="observation differs"):
        await reconcile_answer_receivers(runtime, plan)
    assert "write" not in pool.trace
    foreign = False
    pool.changed = True
    pool.trace.clear()
    with pytest.raises(PolicyUnavailableError, match="loan readback differs"):
        await reconcile_answer_receivers(runtime, plan)
    assert "write" not in pool.trace
    pool.changed = False
    pool.unknown = True
    with pytest.raises(PolicyUnavailableError, match="observation is unknown"):
        await reconcile_answer_receivers(runtime, plan)
    pool.unknown = False
    await reconcile_answer_receivers(runtime, plan)
    assert pool.observed["receiver_receipt"] == receipt


async def _assert_source_question_profile_values():
    """Full own reduced-question profile plus ancestry; software SQL double only."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_disposal import (
        _REDUCED_QUESTION,
        _REDUCED_REASON,
        source_question_status,
    )
    from butlers.chronicler.location_policy import PolicyUnavailableError

    decision, receipt, generation, ledger, parent, output = [uuid4() for _ in range(6)]
    canonical = dict(
        id=ledger,
        asking_butler="chronicler",
        question=_REDUCED_QUESTION,
        target_butler="relationship",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
        status="failed",
        reason=_REDUCED_REASON,
        wake_state="not_applicable",
        **{
            key: None
            for key in (
                "answer",
                "answer_digest",
                "answered_at",
                "answering_butler",
                "wake_key",
                "wake_task_id",
                "wake_task_name",
                "wake_updated_at",
            )
        },
    )
    terminal = dict(
        question_generation=generation,
        ledger_id=ledger,
        decision_id=decision,
        receipt_id=receipt,
        body_digest=b"q" * 32,
        original_digest=b"q" * 32,
        manifest_digest=b"m" * 32,
        plan_manifest=b"m" * 32,
        reduced_question_digest=question_digest(canonical),
    )
    header = dict(
        question_generation=generation,
        ledger_id=ledger,
        body_digest=b"q" * 32,
        parent_count=1,
        exclusive_input=True,
    )
    parents = [dict(parent_kind="native_copy", parent_generation=parent, parent_digest=b"p" * 32)]

    class Pool:
        def __init__(self):
            self.tx = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            assert not self.tx
            self.tx = True
            try:
                yield
            finally:
                self.tx = False

        async def fetchrow(self, sql, *args):
            assert self.tx
            if "FROM location_native_question_answer_observations" in sql:
                assert args == (generation,)
                return None
            if "FROM location_native_delegation_dispositions" in sql:
                assert args in ((decision, receipt), (generation,))
                return terminal
            assert "FROM public.delegation_ledger" in sql and args == (ledger,)
            return canonical

        async def fetchval(self, sql, *args):
            assert self.tx and "FROM location_native_delegation_dispositions" in sql
            return True

        async def fetch(self, sql, *args):
            assert self.tx
            if "FROM location_retention_plan_outputs" in sql:
                return [dict(output_kind="point_event", output_id=output)]
            if "FROM location_native_delegation_inputs" in sql:
                return [header] if args == (None,) else []
            if "FROM location_native_delegation_parents" in sql:
                assert args == (generation,)
                return parents
            if "FROM location_native_copy_births" in sql:
                return [
                    dict(
                        output_kind="point_event",
                        output_id=output,
                        input_digest=b"p" * 32,
                        lineage_known=True,
                        exclusive_input=True,
                    )
                ]
            assert "FROM location_native_delegation_loans" in sql
            return []

    pool = Pool()

    async def lock(conn):
        assert conn is pool and pool.tx

    runtime = SimpleNamespace(active=True, name="chronicler", domain=pool, lock_domain=lock)
    first = await source_question_status(runtime, decision, receipt)
    assert first["receipt_id"] == str(receipt) and first["body_digest"] == ("71" * 32)
    assert first["reduced_question_digest"] == terminal["reduced_question_digest"].hex()
    for key, value in (
        ("metadata", {"copied": "planted source"}),
        ("catalog_score", 0.5),
        ("status", "routed"),
        ("reason", "other"),
        ("question", "changed"),
        ("answer", "raw answer"),
    ):
        original = canonical[key]
        canonical[key] = value
        with pytest.raises(PolicyUnavailableError, match="source question is unknown"):
            await source_question_status(runtime, decision, receipt)
        canonical[key] = original
    original = terminal["reduced_question_digest"]
    terminal["reduced_question_digest"] = None
    with pytest.raises(PolicyUnavailableError, match="receipt is unavailable"):
        await source_question_status(runtime, decision, receipt)
    terminal["reduced_question_digest"] = original
    parent_row = parents.pop()
    with pytest.raises(PolicyUnavailableError, match="ancestry is unknown"):
        await source_question_status(runtime, decision, receipt)
    parents.append(parent_row)
    assert await source_question_status(runtime, decision, receipt) == first
    runtime.name = "other"
    with pytest.raises(PolicyUnavailableError, match="constructor differs"):
        await source_question_status(runtime, decision, receipt)


async def _assert_answered_question_reference_values():
    """Frozen two-owner identities and reduced profiles; no SQL/route claim."""
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_answer_sources import (
        _REDUCED_ANSWER,
        _REDUCED_DIGEST,
        disposed_answer_matches,
    )
    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON
    from butlers.chronicler.location_question_sources import answered_question_matches

    decision, answer, question, ledger, answer_receipt, question_receipt = [
        uuid4() for _ in range(6)
    ]
    runtime = SimpleNamespace(name="relationship")
    plan = dict(decision_id=decision, manifest_digest=b"m" * 32)
    wire_plan = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    canonical = dict(
        id=ledger,
        asking_butler="chronicler",
        question="Synthetic original question",
        target_butler="relationship",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
        status="answered",
        answer=_REDUCED_ANSWER,
        answering_butler="relationship",
        answer_digest=(b"a" * 32).hex(),
        wake_key="fixed wake",
        reason=None,
    )
    original_digest = question_digest(canonical)
    header = dict(
        answer_generation=answer, ledger_id=ledger, body_digest=b"a" * 32, bundle_digest=b"b" * 32
    )
    receipt = dict(
        answer_generation=answer,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        body_digest=b"a" * 32,
        bundle_digest=b"b" * 32,
        question_digest=original_digest,
        wake_key="fixed wake",
        reduced_digest=_REDUCED_DIGEST,
        question_owner="chronicler",
    )
    qheader = dict(question_generation=question)
    answer_observation = dict(
        question_generation=question,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        answer_owner="relationship",
        answer_generation=answer,
        answer_receipt=answer_receipt,
        answer_body_digest=b"a" * 32,
        answer_bundle_digest=b"b" * 32,
        wake_key="fixed wake",
    )
    assert answered_question_matches(canonical, answer_observation, qheader, plan)
    assert disposed_answer_matches(runtime, header, canonical, receipt, wire_plan)
    for key, value in [
        ("answer_owner", "other"),
        ("answer_body_digest", b"x" * 32),
        ("manifest_digest", b"x" * 32),
        ("wake_key", "other"),
        ("question_generation", uuid4()),
    ]:
        assert not answered_question_matches(
            canonical, answer_observation | {key: value}, qheader, plan
        )
    for key, value in [
        ("metadata", {"independent": "preserve"}),
        ("answer", "raw prose"),
        ("status", "routed"),
        ("reason", "preserve independent error"),
        ("answering_butler", "other"),
    ]:
        assert not answered_question_matches(
            canonical | {key: value}, answer_observation, qheader, plan
        )
    # Original comparison cannot be waived when another owner reduces the
    # question. Only a stored observation of its actual full receipt can bind it.
    canonical.update(question=_REDUCED_QUESTION, reason=_REDUCED_REASON)
    assert not disposed_answer_matches(runtime, header, canonical, receipt, wire_plan)
    question_observation = dict(
        answer_generation=answer,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        question_owner="chronicler",
        question_generation=question,
        question_receipt=question_receipt,
        original_question_digest=original_digest,
        reduced_question_digest=question_digest(canonical),
    )
    assert disposed_answer_matches(
        runtime, header, canonical, receipt, wire_plan, question_observation
    )
    for key, value in [
        ("answer_generation", uuid4()),
        ("decision_id", uuid4()),
        ("manifest_digest", b"x" * 32),
        ("question_owner", "other"),
        ("original_question_digest", b"x" * 32),
        ("reduced_question_digest", b"x" * 32),
    ]:
        assert not disposed_answer_matches(
            runtime, header, canonical, receipt, wire_plan, question_observation | {key: value}
        )
    for key, value in [
        ("question", "changed"),
        ("reason", "other"),
        ("metadata", {"copied": "new raw precision"}),
    ]:
        assert not disposed_answer_matches(
            runtime, header, canonical | {key: value}, receipt, wire_plan, question_observation
        )
    assert not disposed_answer_matches(
        runtime,
        header,
        canonical,
        receipt | {"question_owner": None},
        wire_plan,
        question_observation,
    )
    assert answered_question_matches(canonical, answer_observation, qheader, plan)
    assert disposed_answer_matches(
        runtime, header, canonical, receipt, wire_plan, question_observation
    )


async def _assert_answer_question_observation_values():
    """Actual observer/status functions over strict owning software transport only."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_answer_sources import (
        _REDUCED_ANSWER,
        _REDUCED_DIGEST,
        observe_source_question,
    )
    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON
    from butlers.chronicler.location_policy import PolicyUnavailableError

    decision, answer, question, ledger, ar, qr, parent, loan, incarnation = [
        uuid4() for _ in range(9)
    ]
    canonical = dict(
        id=ledger,
        asking_butler="chronicler",
        question="Original frozen question",
        target_butler="relationship",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
        status="answered",
        answer=_REDUCED_ANSWER,
        answering_butler="relationship",
        answer_digest=(b"a" * 32).hex(),
        wake_key="fixed wake",
        reason=None,
    )
    original = question_digest(canonical)
    canonical.update(question=_REDUCED_QUESTION, reason=_REDUCED_REASON)
    header = dict(
        answer_generation=answer,
        ledger_id=ledger,
        body_digest=b"a" * 32,
        bundle_digest=b"b" * 32,
        parent_count=1,
        exclusive_input=True,
    )
    receipt = dict(
        answer_generation=answer,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        body_digest=b"a" * 32,
        bundle_digest=b"b" * 32,
        question_digest=original,
        wake_key="fixed wake",
        reduced_digest=_REDUCED_DIGEST,
        question_owner="chronicler",
        receipt_id=ar,
        ledger_id=ledger,
    )
    parent_row = dict(
        parent_kind="received_question", parent_generation=parent, parent_digest=b"p" * 32
    )
    admitted = dict(
        body_digest=b"p" * 32,
        exclusive_input=True,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        receiving_incarnation=incarnation,
        floor_incarnation=incarnation,
        floor_digest=b"p" * 32,
        question_generation=question,
        floor_question=question,
        loan_id=loan,
        floor_loan=loan,
        ledger_id=ledger,
        floor_ledger=ledger,
        source_name="chronicler",
        floor_source="chronicler",
    )
    qstatus = dict(
        source_name="chronicler",
        decision_id=str(decision),
        manifest_digest=(b"m" * 32).hex(),
        ledger_id=str(ledger),
        body_digest=original.hex(),
        answer_generation=str(answer),
        answer_receipt=str(ar),
        receipt_id=str(qr),
        question_generation=str(question),
        reduced_question_digest=question_digest(canonical).hex(),
    )
    source_plan = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex())

    class Pool:
        tx = False
        observed = None
        unavailable_readback = False
        acquisitions = 0

        @asynccontextmanager
        async def acquire(self):
            self.acquisitions += 1
            yield self

        @asynccontextmanager
        async def transaction(self):
            assert self.tx is False
            self.tx = True
            try:
                yield
            finally:
                self.tx = False

        async def fetchrow(self, sql, *args):
            if "location_native_answer_question_observations" in sql:
                assert args == (answer,)
                if not self.tx and self.unavailable_readback:
                    return None
                return self.observed
            if "FROM location_native_delegation_answer_dispositions" in sql:
                assert args in ((decision, ar), (answer,))
                return receipt
            if "FROM location_native_delegation_answers" in sql:
                assert args == (answer,)
                return header
            if "FROM location_received_delegation_inputs" in sql:
                assert self.tx and args == (parent,)
                return admitted
            assert "FROM public.delegation_ledger" in sql and args == (ledger,) and self.tx
            return canonical

        async def fetch(self, sql, *args):
            assert self.tx
            if "FROM location_native_delegation_answers" in sql:
                return [header] if args == (None,) else []
            if "FROM location_native_delegation_answer_parents" in sql:
                assert args == (answer,)
                return [parent_row]
            assert "FROM location_native_answer_loans" in sql and args == (answer,)
            return []

        async def execute(self, sql, *args):
            assert self.tx and "INSERT INTO location_native_answer_question_observations" in sql
            values = dict(
                zip(
                    (
                        "answer_generation",
                        "decision_id",
                        "manifest_digest",
                        "question_owner",
                        "question_generation",
                        "question_receipt",
                        "original_question_digest",
                        "reduced_question_digest",
                    ),
                    args,
                )
            )
            if self.observed is None:
                self.observed = values

    pool = Pool()
    routed = []

    async def lock(conn):
        assert conn is pool and pool.tx

    async def route(target, tool, args):
        assert not pool.tx and target == "chronicler"
        routed.append((tool, args))
        if tool == "location_retention_source_question_status":
            assert args == dict(decision_id=str(decision), receipt_id=str(qr))
            return qstatus
        assert tool == "chronicler_location_retention_status"
        assert args == dict(decision_id=str(decision))
        return source_plan

    runtime = SimpleNamespace(
        active=True,
        name="relationship",
        incarnation=incarnation,
        domain=pool,
        lock_domain=lock,
        routed_tool=route,
    )
    for key, changed in [
        ("answer_generation", str(uuid4())),
        ("answer_receipt", str(uuid4())),
        ("source_name", "other"),
        ("body_digest", "78" * 32),
        ("manifest_digest", "78" * 32),
        ("receipt_id", str(uuid4())),
    ]:
        original_field = qstatus[key]
        qstatus[key] = changed
        with pytest.raises(PolicyUnavailableError, match="observation differs"):
            await observe_source_question(runtime, decision, ar, qr)
        assert pool.observed is None
        qstatus[key] = original_field
    # A remote positive is not success before separate owning COMMIT readback.
    pool.unavailable_readback = True
    with pytest.raises(PolicyUnavailableError, match="observation is unknown"):
        await observe_source_question(runtime, decision, ar, qr)
    assert pool.observed is not None
    pool.unavailable_readback = False
    first = await observe_source_question(runtime, decision, ar, qr)
    assert first["receipt_id"] == str(ar) and first["question_digest"] == original.hex()
    assert await observe_source_question(runtime, decision, ar, qr) == first
    frozen = dict(pool.observed)
    canonical["metadata"] = {"new copied precision": True}
    with pytest.raises(PolicyUnavailableError, match="committed binding differs"):
        await observe_source_question(runtime, decision, ar, qr)
    canonical["metadata"] = {}
    assert pool.observed == frozen
    before_routes = len(routed)
    receipt["question_owner"] = None
    with pytest.raises(PolicyUnavailableError, match="original question owner is unknown"):
        await observe_source_question(runtime, decision, ar, qr)
    assert len(routed) == before_routes
    receipt["question_owner"] = "chronicler"
    assert await observe_source_question(runtime, decision, ar, qr) == first


async def _assert_source_question_tool_values():
    from uuid import uuid4

    from butlers.chronicler.location_question_sources import source_question_tool_finished
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    session, ledger, tool = uuid4(), uuid4(), uuid4()
    canonical = dict(question="Synthetic original question", target_butler="relationship")
    result = dict(status="routed", ledger_id=str(ledger), target_butler="relationship")
    call = dict(
        name="delegate_ask",
        module="core",
        outcome="success",
        input_fingerprint=fingerprint_tool_call_payload({"question": canonical["question"]}),
        result=result,
    )
    witness = dict(
        tool_generation=tool,
        module_name="core",
        tool_name="delegate_ask",
        outcome="success",
        exclusive_inputs=True,
        input_digest=bytes.fromhex(call["input_fingerprint"]),
        result_digest=bytes.fromhex(fingerprint_tool_call_payload(result)),
    )
    header = dict(receiving_session=session, ledger_id=ledger, tool_generation=tool)
    source_session = dict(tool_calls=[call])

    class Conn:
        rows = [witness]

        async def fetch(self, sql, *args):
            assert "FROM location_runtime_tool_intents" in sql and args == (session,)
            return self.rows

    conn = Conn()
    # A private same-session sibling cannot vanish from the complete trace.
    sibling_result = result | {"ledger_id": str(uuid4())}
    sibling_call = call | {
        "input_fingerprint": fingerprint_tool_call_payload({"question": "Earlier original ask"}),
        "result": sibling_result,
    }
    sibling = witness | {
        "tool_generation": uuid4(),
        "input_digest": bytes.fromhex(sibling_call["input_fingerprint"]),
        "result_digest": bytes.fromhex(fingerprint_tool_call_payload(sibling_result)),
    }
    conn.rows = [witness, sibling | {"outcome": None, "result_digest": None}]
    assert not await source_question_tool_finished(conn, header, source_session, canonical)
    conn.rows = [witness, sibling]
    assert not await source_question_tool_finished(conn, header, source_session, canonical)
    assert await source_question_tool_finished(
        conn, header, dict(tool_calls=[sibling_call, call]), canonical
    )
    conn.rows = [witness]
    assert await source_question_tool_finished(conn, header, source_session, canonical)
    for key, changed in [
        ("tool_generation", uuid4()),
        ("module_name", "other"),
        ("outcome", "error"),
        ("exclusive_inputs", False),
        ("input_digest", b"x" * 32),
        ("result_digest", b"x" * 32),
    ]:
        conn.rows = [witness | {key: changed}]
        assert not await source_question_tool_finished(conn, header, source_session, canonical)
    conn.rows = [witness]
    assert not await source_question_tool_finished(conn, header, dict(tool_calls=[]), canonical)
    assert not await source_question_tool_finished(
        conn, header, dict(tool_calls=[call, call]), canonical
    )
    for bad in [
        result | {"status": "failed"},
        result | {"error": "private body"},
        result | {"target_butler": "other"},
        result | {"ledger_id": str(uuid4())},
    ]:
        conn.rows = [witness | {"result_digest": bytes.fromhex(fingerprint_tool_call_payload(bad))}]
        assert not await source_question_tool_finished(
            conn, header, dict(tool_calls=[call | {"result": bad}]), canonical
        )
    conn.rows = [witness]
    assert await source_question_tool_finished(conn, header, source_session, canonical)


async def _assert_recursive_question_values():
    """Full own nested child profiles; strict software query double, not SQL/online."""
    from uuid import uuid4

    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_recursive import (
        _closing,
        close_owned_questions,
        closed_owned_question_tools,
    )
    from butlers.core.delegation_source import _writers

    context, question, ledger, tool, decision = [uuid4() for _ in range(5)]
    plan = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    header = dict(
        question_generation=question,
        ledger_id=ledger,
        tool_generation=tool,
        parent_count=2,
        exclusive_input=True,
        body_digest=b"q" * 32,
    )
    parents = [dict(parent_kind="received_question", parent_generation=uuid4()) for _ in range(2)]
    canonical = dict(
        asking_butler="relationship",
        question=_REDUCED_QUESTION,
        target_butler="finance",
        catalog_match_id=None,
        catalog_score=None,
        status="failed",
        reason=_REDUCED_REASON,
        metadata={},
        wake_state="not_applicable",
        answer=None,
        answer_digest=None,
        answered_at=None,
        answering_butler=None,
        wake_key=None,
        wake_task_id=None,
        wake_task_name=None,
        wake_updated_at=None,
    )
    disposition = dict(
        question_generation=question,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        body_digest=header["body_digest"],
        reduced_question_digest=question_digest(canonical),
    )

    class Pool:
        receipt = disposition
        queries = []

        async def fetch(self, sql, *args):
            self.queries.append(sql)
            if '"relationship".location_native_delegation_inputs' in sql:
                assert args == (context,)
                return [header]
            if '"relationship".location_native_delegation_parents' in sql:
                assert args == (question,)
                return parents
            raise AssertionError("Unknown nested child cohort")

        async def fetchrow(self, sql, *args):
            self.queries.append(sql)
            if '"relationship".location_native_delegation_dispositions' in sql:
                assert args == (question,)
                return self.receipt
            if '"relationship".location_native_question_answer_observations' in sql:
                assert args == (question,)
                return None
            assert "FROM public.delegation_ledger" in sql and args == (ledger,)
            return canonical

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool,
        name="relationship",
        registry=object(),
        identity=("relationship", "fixed_own_role"),
    )
    try:
        expected = [{"tool_generation": tool}]
        assert await closed_owned_question_tools(pool, runtime, context, plan) == expected
        saved = parents.pop()
        assert await closed_owned_question_tools(pool, runtime, context, plan) == []
        parents.append(saved)
        parents.append(dict(parents[0]))
        assert await closed_owned_question_tools(pool, runtime, context, plan) == []
        parents.pop()
        for key, changed in [
            ("manifest_digest", b"x" * 32),
            ("body_digest", b"x" * 32),
            ("reduced_question_digest", None),
            ("decision_id", uuid4()),
        ]:
            pool.receipt = disposition | {key: changed}
            assert await closed_owned_question_tools(pool, runtime, context, plan) == []
        pool.receipt = disposition
        for key, changed in [
            ("asking_butler", "finance"),
            ("question", "unrelated replacement"),
            ("metadata", {"independent": "preserve"}),
        ]:
            before = canonical[key]
            canonical[key] = changed
            assert await closed_owned_question_tools(pool, runtime, context, plan) == []
            canonical[key] = before
        assert await closed_owned_question_tools(pool, runtime, context, plan) == expected
        assert all('"relationship".' in q or "public.delegation_ledger" in q for q in pool.queries)
        # Reentry cannot call a peer or mint a receipt; the exact actual
        # constructor registration is still required before this pending path.
        with pytest.raises(PolicyUnavailableError, match="constructor ended"):
            await close_owned_questions(runtime, decision)
        _writers[pool] = runtime.delegation_writer
        _closing[runtime.delegation_writer] = {decision}
        before = len(pool.queries)
        assert await close_owned_questions(runtime, decision) == dict(
            decision_id=str(decision), receipt_ids=[]
        )
        assert len(pool.queries) == before
    finally:
        _closing.pop(runtime.delegation_writer, None)
        _writers.pop(pool, None)
        runtime.close()


async def _assert_recursive_question_cohort_values():
    """Complete borrowed ancestry and owning plans; software SQL/route doubles only."""
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_recursive import (
        question_owner_cohort,
        question_owner_plan,
    )
    from butlers.core.delegation_source import _writers

    question, ledger, context, session, tool, decision = [uuid4() for _ in range(6)]
    canonical = dict(
        asking_butler="relationship",
        question="Synthetic borrowed original question",
        target_butler="finance",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
    )
    header = dict(
        question_generation=question,
        ledger_id=ledger,
        parent_count=2,
        exclusive_input=True,
        body_digest=question_digest(canonical),
        context_generation=context,
        tool_generation=tool,
        receiving_session=session,
    )
    parents = [
        dict(
            parent_kind="received_question",
            parent_generation=uuid4(),
            parent_digest=bytes([n]) * 32,
        )
        for n in [1, 2]
    ]
    root = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex(), catalog_loans=[])

    class Pool:
        in_transaction = False
        floors = {}
        routed = []

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetch(self, sql, *args):
            if "FROM location_native_delegation_inputs" in sql:
                return [header] if args == (None,) else []
            if "FROM location_native_delegation_parents" in sql:
                assert args == (question,)
                return parents
            assert "FROM location_native_delegation_loans" in sql and args == (question,)
            return []

        async def fetchrow(self, sql, *args):
            if "FROM public.delegation_ledger" in sql:
                assert args == (ledger,)
                return canonical
            if "FROM location_native_delegation_dispositions" in sql:
                assert args == (question,)
                return None
            assert "FROM location_received_delegation_inputs i" in sql and len(args) == 1
            return self.floors.get(args[0])

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "relationship"
            assert sql == "SELECT current_user"
            return "fixed_own_role"

        async def execute(self, sql, *args):
            assert "pg_advisory_xact_lock" in sql and self.in_transaction

    pool = Pool()
    runtime = NativeDelegationRuntime(
        domain=pool,
        name="relationship",
        registry=object(),
        identity=("relationship", "fixed_own_role"),
    )

    async def route(target, tool_name, args):
        assert not pool.in_transaction
        assert target == "chronicler" and tool_name == "chronicler_location_retention_status"
        assert args == dict(decision_id=str(decision))
        pool.routed.append((target, tool_name))
        return root

    runtime.routed_tool = route
    for parent in parents:
        q, loan = uuid4(), uuid4()
        row = dict(
            body_digest=parent["parent_digest"],
            exclusive_input=True,
            decision_id=decision,
            manifest_digest=b"m" * 32,
            receiving_incarnation=runtime.incarnation,
            floor_incarnation=runtime.incarnation,
            floor_digest=parent["parent_digest"],
            question_generation=q,
            floor_question=q,
            loan_id=loan,
            floor_loan=loan,
            ledger_id=uuid4(),
            source_name="chronicler",
            floor_source="chronicler",
        )
        row["floor_ledger"] = row["ledger_id"]
        pool.floors[parent["parent_generation"]] = row
    try:
        complete = await question_owner_cohort(runtime, pool, root)
        assert len(complete) == 1 and complete[0]["complete_input"] is True
        assert complete[0]["parent_count"] == 2
        saved = parents.pop()
        partial = await question_owner_cohort(runtime, pool, root)
        assert len(partial) == 1 and partial[0]["complete_input"] is False
        assert partial[0]["parent_count"] == 2
        parents.append(saved)
        for key, value in [
            ("floor_digest", b"x" * 32),
            ("floor_incarnation", uuid4()),
            ("manifest_digest", b"x" * 32),
            ("floor_loan", uuid4()),
            ("floor_source", "finance"),
        ]:
            row = pool.floors[saved["parent_generation"]]
            before = row[key]
            row[key] = value
            assert (await question_owner_cohort(runtime, pool, root))[0]["complete_input"] is False
            row[key] = before
        parents.append(dict(parents[0]))
        with pytest.raises(PolicyUnavailableError, match="parent set differs"):
            await question_owner_cohort(runtime, pool, root)
        parents.pop()
        canonical["question"] += " unrelated change"
        with pytest.raises(PolicyUnavailableError, match="original body differs"):
            await question_owner_cohort(runtime, pool, root)
        canonical["question"] = "Synthetic borrowed original question"
        assert await question_owner_cohort(runtime, pool, root) == complete
        with pytest.raises(PolicyUnavailableError, match="constructor ended"):
            await question_owner_plan(runtime, decision)
        assert pool.routed == []
        _writers[pool] = runtime.delegation_writer
        plan = await question_owner_plan(runtime, decision)
        assert plan["question_cohort"] == complete
        assert plan["source_name"] == "relationship"
        assert plan["source_incarnation"] == str(runtime.incarnation)
        assert pool.routed == [("chronicler", "chronicler_location_retention_status")]
    finally:
        _writers.pop(pool, None)
        runtime.close()


async def _assert_recursive_question_observation_values():
    """Actual own observation function/order; no real SQL or registered-route claim."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_recursive import observe_question_loans

    decision, question, ledger, loan, receiving, incarnation, receipt = [uuid4() for _ in range(7)]
    native = dict(
        loan_id=loan,
        question_generation=question,
        ledger_id=ledger,
        question_digest=b"q" * 32,
        body_digest=b"q" * 32,
        receiving_generation=receiving,
        receiving_incarnation=incarnation,
        receiver_name="finance",
    )
    loan_row = {
        k: str(v)
        for k, v in native.items()
        if k not in {"question_generation", "ledger_id", "question_digest"}
    }
    loan_row["body_digest"] = (b"q" * 32).hex()
    question_row = dict(
        question_generation=str(question),
        ledger_id=str(ledger),
        body_digest=(b"q" * 32).hex(),
        complete_input=True,
        target_name="finance",
        loans=[loan_row],
    )
    plan = dict(
        decision_id=str(decision),
        manifest_digest=(b"m" * 32).hex(),
        source_name="relationship",
        question_cohort=[question_row],
    )
    status = dict(
        source_name="relationship",
        decision_id=str(decision),
        manifest_digest=plan["manifest_digest"],
        question_generation=str(question),
        ledger_id=str(ledger),
        receipt_id=str(receipt),
        **{
            k: loan_row[k]
            for k in ["loan_id", "body_digest", "receiving_generation", "receiving_incarnation"]
        },
    )

    class Pool:
        in_transaction = False
        committed = {}
        writes = []
        unknown = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            before = dict(self.committed)
            try:
                yield
            except BaseException:
                self.committed = before
                raise
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "FROM location_native_delegation_loans" in sql:
                assert self.in_transaction and args == (loan,)
                return native
            assert "FROM location_native_question_loan_observations" in sql and args == (loan,)
            assert not self.in_transaction
            return None if self.unknown else self.committed.get(loan)

        async def execute(self, sql, *args):
            assert self.in_transaction
            assert "INSERT INTO location_native_question_loan_observations" in sql
            assert args == (loan, decision, b"m" * 32, receipt)
            self.writes.append(args)
            self.committed.setdefault(
                loan,
                dict(decision_id=decision, manifest_digest=b"m" * 32, receiver_receipt=receipt),
            )

    pool = Pool()
    runtime = SimpleNamespace(domain=pool, name="relationship")

    async def lock(conn):
        assert conn is pool and pool.in_transaction

    calls = []
    census_calls = []
    census = dict(
        source_name="relationship",
        ledger_id=str(ledger),
        question_generation=str(question),
        body_digest=(b"q" * 32).hex(),
        decision_id=str(decision),
        manifest_digest=(b"m" * 32).hex(),
        receiver_name="finance",
        receiving_incarnation=str(incarnation),
        attempt_count=1,
        pending=False,
    )

    async def route(target, tool, args):
        assert not pool.in_transaction and target == "finance"
        if tool == "location_retention_prepare_questions":
            assert args == dict(decision_id=str(decision), ledger_id=str(ledger))
            census_calls.append((target, tool, args))
            return dict(decision_id=str(decision), source_census=[census])
        calls.append((target, tool, args))
        if tool == "location_retention_prepare_question_loan":
            assert args["receiving_generation"] == str(receiving)
            args = {k: v for k, v in args.items() if k != "receiving_generation"}
            assert args == dict(decision_id=str(decision), loan_id=str(loan))
            return dict(decision_id=str(decision), loan_id=str(loan), receipt_id=str(receipt))
        assert tool == "location_retention_question_status"
        assert args == dict(decision_id=str(decision), receipt_id=str(receipt))
        return status

    runtime.lock_domain = lock
    runtime.routed_tool = route
    await observe_question_loans(runtime, plan)
    assert pool.committed[loan]["receiver_receipt"] == receipt
    assert len(calls) == 2 and len(pool.writes) == 1
    assert len(census_calls) == 1 and len(calls) + len(census_calls) == 3
    pool.committed.clear()
    pool.writes.clear()
    for key, changed in [
        ("source_name", "home"),
        ("manifest_digest", (b"x" * 32).hex()),
        ("question_generation", str(uuid4())),
        ("body_digest", (b"x" * 32).hex()),
        ("receiving_incarnation", str(uuid4())),
        ("receipt_id", str(uuid4())),
    ]:
        before = status[key]
        status[key] = changed
        with pytest.raises(PolicyUnavailableError, match="receiving status differs"):
            await observe_question_loans(runtime, plan)
        assert pool.writes == [] and pool.committed == {}
        status[key] = before
    native["body_digest"] = b"x" * 32
    with pytest.raises(PolicyUnavailableError, match="own loan changed"):
        await observe_question_loans(runtime, plan)
    assert pool.writes == [] and pool.committed == {}
    native["body_digest"] = b"q" * 32
    pool.unknown = True
    with pytest.raises(PolicyUnavailableError, match="observation is unknown"):
        await observe_question_loans(runtime, plan)
    assert pool.committed[loan]["receiver_receipt"] == receipt
    pool.unknown = False
    await observe_question_loans(runtime, plan)
    assert pool.committed == {
        loan: dict(decision_id=decision, manifest_digest=b"m" * 32, receiver_receipt=receipt)
    }
    assert all(args[3] == receipt for args in pool.writes)

    assert census_calls
    # Zero own loans still invokes the actual original target's complete
    # census; it is not evidence of no unaccepted receiving attempt.
    calls.clear()
    census_calls.clear()
    saved_loans = question_row["loans"]
    question_row["loans"] = []
    try:
        census["attempt_count"] = 0
        await observe_question_loans(runtime, plan)
        assert calls == [] and len(census_calls) == 1
        census["pending"] = True
        with pytest.raises(PolicyUnavailableError, match="census is pending"):
            await observe_question_loans(runtime, plan)
        assert calls == [] and len(census_calls) == 2
        census["pending"] = False
        await observe_question_loans(runtime, plan)
        assert calls == [] and len(census_calls) == 3
    finally:
        question_row["loans"] = saved_loans


async def _assert_unaccepted_question_recovery_values():
    """Actual producer/receiver closure, strict software rows, not routed/PG proof."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_recursive import prepare_question_loan

    decision, loan, generation, question, ledger, incarnation, source_incarnation, server = (
        uuid4() for _ in range(8)
    )
    attempt = dict(
        receiving_generation=generation,
        ledger_id=ledger,
        body_digest=b"q" * 32,
        receiving_incarnation=incarnation,
        source_name="finance",
        server_request=server,
        receiving_session=None,
        tool_generation=None,
    )
    item = dict(
        question_generation=str(question),
        ledger_id=str(ledger),
        body_digest=(b"q" * 32).hex(),
        complete_input=True,
        loans=[
            dict(
                loan_id=str(loan),
                receiver_name="home",
                receiving_generation=str(generation),
                receiving_incarnation=str(incarnation),
                body_digest=(b"q" * 32).hex(),
            )
        ],
    )
    plan = dict(
        decision_id=str(decision),
        manifest_digest=(b"m" * 32).hex(),
        source_name="finance",
        source_incarnation=str(source_incarnation),
        question_cohort=[item],
    )

    class Pool:
        in_transaction = False
        floor = None
        receipt = None
        unknown = False
        writes = []

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "FROM location_received_delegation_inputs i" in sql:
                return None  # Interrupted before admission, durable attempt exists.
            if "FROM location_received_delegation_attempts" in sql:
                return attempt if args[0] == generation else None
            if "FROM location_received_delegation_floors f" in sql:
                if self.unknown or self.receipt is None:
                    return None
                assert args == (decision, self.receipt)
                return self.floor | dict(receipt_id=self.receipt)
            if "FROM location_received_delegation_floors" in sql:
                return self.floor
            if "FROM location_received_delegation_inputs" in sql:
                return None
            raise AssertionError("unexpected unaccepted recovery row")

        async def fetchval(self, sql, *args):
            if "FROM location_received_delegation_dispositions" in sql:
                return self.receipt
            if "FROM location_received_delegation_server_finished" in sql:
                assert args == (generation, server, b"q" * 32)
                return True  # Explicit planted actual-own server end witness.
            raise AssertionError("unexpected unaccepted recovery value")

        async def execute(self, sql, *args):
            assert self.in_transaction
            self.writes.append(sql)
            if "INSERT INTO location_received_delegation_floors" in sql:
                self.floor = dict(
                    zip(
                        (
                            "receiving_generation",
                            "decision_id",
                            "manifest_digest",
                            "source_name",
                            "question_generation",
                            "ledger_id",
                            "loan_id",
                            "body_digest",
                            "receiving_incarnation",
                        ),
                        args,
                    )
                )
            elif "INSERT INTO location_received_delegation_dispositions" in sql:
                self.receipt = args[1]
            else:
                raise AssertionError("unexpected unaccepted recovery write")

    pool = Pool()

    async def lock(conn):
        assert conn is pool and conn.in_transaction

    routed = []

    async def route(target, tool, args):
        assert not pool.in_transaction
        assert target == attempt["source_name"]
        assert tool == "location_retention_question_owner_plan"
        assert args == dict(decision_id=str(decision))
        routed.append(target)
        return plan

    runtime = SimpleNamespace(
        active=True,
        name="home",
        incarnation=incarnation,
        domain=pool,
        lock_domain=lock,
        routed_tool=route,
        delegation_writer=SimpleNamespace(receiving={}, pending={}),
    )
    # A source/loan request alone cannot select a missing local attempt.
    assert (await prepare_question_loan(runtime, decision, loan))["receipt_id"] is None
    assert not routed and not pool.writes
    original_source = attempt["source_name"]
    attempt["source_name"] = None
    assert (await prepare_question_loan(runtime, decision, loan, generation))["receipt_id"] is None
    assert not routed and not pool.writes
    attempt["source_name"] = original_source
    for key, value in [
        ("ledger_id", uuid4()),
        ("body_digest", b"x" * 32),
        ("receiving_incarnation", uuid4()),
    ]:
        old = attempt[key]
        attempt[key] = value
        with pytest.raises(PolicyUnavailableError):
            await prepare_question_loan(runtime, decision, loan, generation)
        assert not pool.writes
        attempt[key] = old
    for key, value in [
        ("loan_id", str(uuid4())),
        ("receiving_generation", str(uuid4())),
        ("receiving_incarnation", str(uuid4())),
        ("receiver_name", "other"),
        ("body_digest", (b"x" * 32).hex()),
    ]:
        original = item["loans"][0][key]
        item["loans"][0][key] = value
        with pytest.raises(PolicyUnavailableError):
            await prepare_question_loan(runtime, decision, loan, generation)
        assert not pool.writes
        item["loans"][0][key] = original
    result = await prepare_question_loan(runtime, decision, loan, generation)
    assert result["receipt_id"] == str(pool.receipt)
    assert pool.floor["source_name"] == original_source
    assert pool.floor["question_generation"] == question
    writes = len(pool.writes)
    assert await prepare_question_loan(runtime, decision, loan, generation) == result
    assert len(pool.writes) == writes + 1  # Same floor retry, never a second receipt.
    pool.unknown = True
    with pytest.raises(PolicyUnavailableError, match="Native receiving receipt is unavailable"):
        await prepare_question_loan(runtime, decision, loan, generation)
    assert result["receipt_id"] == str(pool.receipt)  # Committed evidence survives lost ACK.
    pool.unknown = False
    assert await prepare_question_loan(runtime, decision, loan, generation) == result


async def _assert_receiving_task_disposition_values():
    """Actual own task reducer and profile readers; SQL doubles, not PG/online."""
    import hashlib
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_delegation_disposal import _REDUCED_TASK
    from butlers.chronicler.location_memory_context import captured_artifact_calls
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_tasks import prepare_question_task
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    generation, decision, incarnation, ledger, loan, question, task_id, server, tool = (
        uuid4() for _ in range(9)
    )
    binding = dict(
        receiving_generation=generation,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        source_name="finance",
        question_generation=question,
        ledger_id=ledger,
        loan_id=loan,
        body_digest=b"q" * 32,
        receiving_incarnation=incarnation,
    )
    prompt = "synthetic original copied question task"
    task = dict(
        task_id=task_id,
        prompt=prompt,
        enabled=True,
        prompt_digest=hashlib.sha256(prompt.encode()).digest(),
    )
    admitted = binding | dict(exclusive_input=True, parent_count=1)
    attempt = binding | dict(server_request=server, tool_generation=tool, receiving_session=uuid4())

    class Pool:
        in_transaction = False
        finished = False
        unresolved = False
        previous = None
        fault = False
        unknown = False
        updated = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            old_task, old_receipt = deepcopy(task), deepcopy(self.previous)
            self.in_transaction = True
            try:
                yield
            except BaseException:
                task.clear()
                task.update(old_task)
                self.previous = old_receipt
                raise
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "FROM location_received_delegation_floors" in sql:
                return binding
            if "FROM location_received_delegation_inputs" in sql:
                return admitted
            if "FROM location_received_delegation_attempts" in sql:
                return attempt
            if "FROM location_received_delegation_schedules s" in sql:
                return task
            if "FROM location_received_question_task_dispositions d" in sql:
                return None if self.unknown or self.previous is None else self.previous | task
            if "FROM location_received_question_task_dispositions" in sql:
                return self.previous
            raise AssertionError("unexpected task disposition row")

        async def fetchval(self, sql, *args):
            if "location_received_delegation_server_finished" in sql:
                return self.finished
            if "location_received_delegation_claims c" in sql:
                return self.unresolved
            raise AssertionError("unexpected task disposition value")

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "UPDATE scheduled_tasks" in sql:
                self.updated = True
                task.update(prompt=args[1], enabled=False)
            elif "INSERT INTO location_received_question_task_dispositions" in sql:
                if self.fault:
                    raise RuntimeError("synthetic actual receipt failure")
                self.previous = dict(
                    zip(
                        (
                            "receiving_generation",
                            "task_id",
                            "decision_id",
                            "manifest_digest",
                            "original_prompt_digest",
                            "reduced_prompt_digest",
                            "receipt_id",
                        ),
                        args,
                    )
                )
            else:
                raise AssertionError("unexpected task disposition write")

    pool = Pool()

    async def lock(conn):
        assert conn is pool and conn.in_transaction

    runtime = SimpleNamespace(
        domain=pool,
        lock_domain=lock,
        incarnation=incarnation,
        delegation_writer=SimpleNamespace(receiving={}, pending={}),
    )
    assert not await prepare_question_task(runtime, binding)
    assert task["prompt"] == prompt and pool.previous is None
    pool.finished = True
    pool.unresolved = True
    assert not await prepare_question_task(runtime, binding)
    assert not pool.updated
    pool.unresolved = False
    pool.fault = True
    with pytest.raises(RuntimeError, match="actual receipt failure"):
        await prepare_question_task(runtime, binding)
    assert pool.updated and task["prompt"] == prompt and pool.previous is None
    pool.fault = False
    assert await prepare_question_task(runtime, binding)
    receipt = deepcopy(pool.previous)
    assert task["prompt"] == _REDUCED_TASK and task["enabled"] is False
    assert await prepare_question_task(runtime, binding) and pool.previous == receipt
    pool.unknown = True
    with pytest.raises(PolicyUnavailableError, match="Committed native question task"):
        await prepare_question_task(runtime, binding)
    assert pool.previous == receipt
    pool.unknown = False
    task["prompt"] = "independent changed current task"
    with pytest.raises(PolicyUnavailableError, match="task disposition differs"):
        await prepare_question_task(runtime, binding)
    task["prompt"] = _REDUCED_TASK
    assert await prepare_question_task(runtime, binding)

    call = dict(
        name="delegate_receive",
        module="core",
        outcome="success",
        input_fingerprint="ab" * 32,
        result=dict(status="scheduled", ledger_id=str(ledger), task_id=str(task_id)),
    )
    witness = dict(
        tool_generation=tool,
        tool_name="delegate_receive",
        module_name="core",
        outcome="success",
        input_digest=bytes.fromhex(call["input_fingerprint"]),
        result_digest=bytes.fromhex(fingerprint_tool_call_payload(call["result"])),
        exclusive_inputs=True,
    )
    assert not captured_artifact_calls([call], [], [witness])
    assert captured_artifact_calls(
        [call], [], [witness], closed_receives=[dict(tool_generation=tool)]
    )
    sibling = uuid4()
    assert not captured_artifact_calls(
        [call, call],
        [],
        [witness, witness | dict(tool_generation=sibling)],
        closed_receives=[dict(tool_generation=tool)],
    )
    assert captured_artifact_calls(
        [call, call],
        [],
        [witness, witness | dict(tool_generation=sibling)],
        closed_receives=[dict(tool_generation=tool), dict(tool_generation=sibling)],
    )
    assert not captured_artifact_calls(
        [call | dict(result=call["result"] | dict(answer="mixed body"))],
        [],
        [witness],
        closed_receives=[dict(tool_generation=tool)],
    )
    assert not captured_artifact_calls(
        [call | dict(outcome="error")], [], [witness], closed_receives=[dict(tool_generation=tool)]
    )
    from butlers.chronicler.location_question_tasks import closed_received_question_tools

    session = attempt["receiving_session"]
    row = admitted | dict(
        tool_generation=tool,
        receiving_session=session,
        task_id=task_id,
        prompt_digest=task["prompt_digest"],
        decision_id=decision,
        manifest_digest=b"m" * 32,
        original_prompt_digest=task["prompt_digest"],
        reduced_prompt_digest=hashlib.sha256(_REDUCED_TASK.encode()).digest(),
        prompt=_REDUCED_TASK,
        enabled=False,
        floor_source=admitted["source_name"],
        floor_digest=admitted["body_digest"],
        floor_question=question,
        floor_loan=loan,
        floor_ledger=ledger,
        floor_decision=decision,
        floor_manifest=b"m" * 32,
        floor_incarnation=incarnation,
        outcome="success",
        result_digest=witness["result_digest"],
        exclusive_inputs=True,
        module_name="core",
        tool_name="delegate_receive",
        tool_session=session,
    )

    class Profile:
        attempts = [attempt]
        rows = {generation: row}

        async def fetch(self, sql, *args):
            assert '"home".location_received_delegation_attempts' in sql and args == (session,)
            return self.attempts

        async def fetchrow(self, sql, *args):
            if '"home".location_received_question_refusals' in sql:
                return None  # Exact installed table; no producer stage planted here.
            assert '"home".location_received_question_task_dispositions' in sql
            assert '"home".location_runtime_tool_intents' in sql
            assert "chronicler." not in sql
            return self.rows.get(args[0])

    profile = Profile()
    selected_plan = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    assert await closed_received_question_tools(
        profile, runtime, '"home"', session, selected_plan
    ) == [dict(tool_generation=tool)]
    for key, bad in [
        ("floor_manifest", b"x" * 32),
        ("floor_decision", uuid4()),
        ("floor_incarnation", uuid4()),
        ("floor_digest", b"x" * 32),
        ("floor_loan", uuid4()),
        ("floor_question", uuid4()),
        ("module_name", "memory"),
        ("tool_name", "other"),
        ("tool_session", uuid4()),
        ("result_digest", b"x" * 32),
        ("prompt", "independent current prompt"),
        ("exclusive_inputs", False),
    ]:
        old = row[key]
        row[key] = bad
        assert (
            await closed_received_question_tools(profile, runtime, '"home"', session, selected_plan)
            == []
        )
        row[key] = old
    # A second original attempt of the same Tool cannot disappear in an
    # admitted JOIN and a closed successful sibling cannot proxy its receipt.
    second = uuid4()
    profile.attempts = [attempt, attempt | dict(receiving_generation=second)]
    assert (
        await closed_received_question_tools(profile, runtime, '"home"', session, selected_plan)
        == []
    )
    second_task = uuid4()
    profile.rows[second] = row | dict(task_id=second_task)
    assert (
        await closed_received_question_tools(profile, runtime, '"home"', session, selected_plan)
        == []
    )  # One actual Tool result cannot match two different scheduled tasks.
    second_tool = uuid4()
    profile.attempts[1] = profile.attempts[1] | dict(tool_generation=second_tool)
    profile.rows[second].update(
        tool_generation=second_tool,
        result_digest=bytes.fromhex(
            fingerprint_tool_call_payload(
                dict(
                    status="scheduled",
                    ledger_id=str(ledger),
                    task_id=str(second_task),
                )
            )
        ),
    )
    assert {
        x["tool_generation"]
        for x in await closed_received_question_tools(
            profile, runtime, '"home"', session, selected_plan
        )
    } == {tool, second_tool}
    profile.rows[second]["exclusive_input"] = False
    assert await closed_received_question_tools(
        profile, runtime, '"home"', session, selected_plan
    ) == [dict(tool_generation=tool)]
    await _assert_receiving_refusal_stage_values()


async def _assert_receiving_refusal_stage_values():
    """Actual producer and profile; SQL/private-lifetime doubles, no PG proof."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_delegation_receivers import _QuestionReceiveRefusal
    from butlers.chronicler.location_memory_context import captured_artifact_calls
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_refusals import (
        _REFUSED_RESULT,
        _refused_digest,
        record_question_refusal,
    )
    from butlers.chronicler.location_question_tasks import closed_received_question_tools
    from butlers.chronicler.location_tool_copies import _current_tool_copy, _ToolCopy
    from butlers.core.delegation_source import (
        clear_writer,
        register_writer,
        reject_question_receive,
    )
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    generation, ledger, incarnation, session, decision, question, loan = (uuid4() for _ in range(7))

    class Pool:
        def __init__(self):
            self.receipt, self.admitted, self.unknown, self.fail, self.intent = (
                None,
                False,
                False,
                False,
                True,
            )
            self.trace = []
            self.attempt = dict(
                receiving_generation=generation,
                ledger_id=ledger,
                source_name="finance",
                body_digest=b"b" * 32,
                receiving_incarnation=incarnation,
                receiving_session=session,
                tool_generation=uuid4(),
                server_request=None,
            )

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            old = deepcopy(self.receipt)
            self.trace.append("begin")
            try:
                yield
            except BaseException:
                self.receipt = old
                self.trace.append("rollback")
                raise
            self.trace.append("commit")

        async def fetchrow(self, sql, *args):
            if "FROM location_received_delegation_attempts" in sql:
                return self.attempt
            assert "FROM location_received_question_refusals" in sql
            assert args == (generation,)
            self.trace.append("readback")
            return None if self.unknown else self.receipt

        async def fetchval(self, sql, *args):
            if "FROM location_received_delegation_inputs" in sql:
                assert "location_received_delegation_schedules" in sql
                return self.admitted
            assert "FROM location_runtime_tool_intents" in sql
            return self.intent

        async def execute(self, sql, *args):
            assert "INSERT INTO location_received_question_refusals" in sql
            self.receipt = dict(
                zip(
                    (
                        "receiving_generation",
                        "tool_generation",
                        "receiving_incarnation",
                        "body_digest",
                        "result_digest",
                        "receipt_id",
                    ),
                    args,
                )
            )
            self.trace.append("stage")
            if self.fail:
                raise RuntimeError("planted stage INSERT failure")

    pool = Pool()

    async def lock(conn):
        assert conn is pool
        pool.trace.append("policy")

    runtime = SimpleNamespace(domain=pool, incarnation=incarnation, active=True, lock_domain=lock)
    writer = SimpleNamespace(runtime=runtime, receiving={}, pending={})
    tool = _ToolCopy(runtime, pool.attempt["tool_generation"], session, "delegate_receive", "core")
    pending = SimpleNamespace(
        ledger=ledger, source="finance", digest=b"b" * 32, receiving=generation, tool=tool
    )

    def failure():
        return _QuestionReceiveRefusal(writer, pending, tool)

    token = _current_tool_copy.set(tool)
    register_writer(pool, writer)
    try:
        await reject_question_receive(
            pool, PolicyUnavailableError("Native question input is unavailable")
        )
        assert (
            pool.receipt is None and not tool.read_observed
        )  # Same text cannot mint stage authority.
        pool.admitted = True
        with pytest.raises(PolicyUnavailableError, match="admission is unknown"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is None and not tool.read_observed
        pool.admitted = False
        pool.intent = False
        with pytest.raises(PolicyUnavailableError, match="Tool differs"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is None and not tool.read_observed
        pool.intent = True
        pool.fail = True
        with pytest.raises(RuntimeError, match="stage INSERT"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is None and not tool.read_observed and "rollback" in pool.trace
        pool.fail = False
        tool.active = False
        with pytest.raises(PolicyUnavailableError, match="lifetime differs"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is None
        tool.active = True
        old = pool.attempt["body_digest"]
        pool.attempt["body_digest"] = b"x" * 32
        with pytest.raises(PolicyUnavailableError, match="attempt differs"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is None and not tool.read_observed
        pool.attempt["body_digest"] = old
        pool.unknown = True
        with pytest.raises(PolicyUnavailableError, match="rejection is unknown"):
            await record_question_refusal(writer, failure())
        assert pool.receipt is not None and not tool.read_observed
        pool.receipt = None  # Disposable double dataset, not production row repair.
        pool.unknown = False
        pool.trace.clear()
        stage = failure()
        tool.mixed_inputs = True
        await reject_question_receive(pool, stage)
        assert not stage.active and tool.read_observed and tool.mixed_inputs
        assert pool.receipt["result_digest"] == _refused_digest()
        assert pool.trace.index("policy") < pool.trace.index("stage") < pool.trace.index("commit")
        assert pool.trace.index("commit") < pool.trace.index("readback")
        with pytest.raises(PolicyUnavailableError, match="stage differs"):
            await record_question_refusal(writer, stage)
    finally:
        clear_writer(pool, writer)
        _current_tool_copy.reset(token)
    plan = dict(decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    row = dict(
        receiving_generation=generation,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        source_name="finance",
        question_generation=question,
        loan_id=loan,
        ledger_id=ledger,
        body_digest=b"b" * 32,
        receiving_incarnation=incarnation,
        rejection_incarnation=incarnation,
        rejection_digest=b"b" * 32,
        rejection_result=_refused_digest(),
        result_digest=_refused_digest(),
        tool_generation=tool.generation,
        tool_session=session,
        module_name="core",
        tool_name="delegate_receive",
        outcome="success",
        exclusive_inputs=True,
    )

    class Profile:
        attempts = [pool.attempt]
        rows = {generation: row}
        admitted = False

        async def fetch(self, sql, *args):
            assert '"home".location_received_delegation_attempts' in sql and args == (session,)
            return self.attempts

        async def fetchrow(self, sql, *args):
            assert "chronicler." not in sql
            if '"home".location_received_question_task_dispositions' in sql:
                return None
            assert '"home".location_received_question_refusals' in sql
            assert "NOT EXISTS" in sql and "location_received_delegation_inputs" in sql
            assert "location_received_delegation_schedules" in sql
            return None if self.admitted else self.rows.get(args[0])

    profile = Profile()
    assert await closed_received_question_tools(profile, runtime, '"home"', session, plan) == [
        dict(tool_generation=tool.generation)
    ]
    for key, bad in [
        ("rejection_digest", b"x" * 32),
        ("rejection_incarnation", uuid4()),
        ("manifest_digest", b"x" * 32),
        ("decision_id", uuid4()),
        ("tool_session", uuid4()),
        ("result_digest", b"x" * 32),
        ("rejection_result", b"x" * 32),
        ("exclusive_inputs", False),
        ("module_name", "memory"),
        ("outcome", "error"),
    ]:
        old = row[key]
        row[key] = bad
        assert await closed_received_question_tools(profile, runtime, '"home"', session, plan) == []
        row[key] = old
    profile.admitted = True
    assert await closed_received_question_tools(profile, runtime, '"home"', session, plan) == []
    profile.admitted = False
    second = uuid4()
    profile.attempts = [pool.attempt, pool.attempt | dict(receiving_generation=second)]
    assert await closed_received_question_tools(profile, runtime, '"home"', session, plan) == []
    profile.rows[second] = row | dict(receiving_generation=second)
    assert await closed_received_question_tools(profile, runtime, '"home"', session, plan) == [
        dict(tool_generation=tool.generation)
    ]
    call = dict(
        name="delegate_receive",
        arguments={"ledger_id": str(ledger), "question": "synthetic", "asking_butler": "finance"},
        result=_REFUSED_RESULT,
        outcome="success",
    )
    call["module"] = "core"
    call["input_fingerprint"] = fingerprint_tool_call_payload(call["arguments"])
    witness = dict(
        tool_generation=tool.generation,
        tool_name="delegate_receive",
        module_name="core",
        input_digest=bytes.fromhex(fingerprint_tool_call_payload(call["arguments"])),
        result_digest=_refused_digest(),
        outcome="success",
        exclusive_inputs=True,
    )
    assert not captured_artifact_calls([call], [], [witness])
    assert captured_artifact_calls(
        [call], [], [witness], closed_receives=[dict(tool_generation=tool.generation)]
    )
    witness["exclusive_inputs"] = False
    assert not captured_artifact_calls(
        [call], [], [witness], closed_receives=[dict(tool_generation=tool.generation)]
    )
    await _assert_question_source_floor_values()


async def _assert_question_source_floor_values():
    """Actual owning fence producer and readback; private plan/SQL doubles only."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_reconciliation import seal_question_source_floor
    from butlers.core.delegation_source import clear_writer, register_writer

    ledger, generation, decision = uuid4(), uuid4(), uuid4()
    canonical = dict(
        id=ledger,
        asking_butler="finance",
        target_butler="home",
        question="synthetic original source",
        catalog_match_id=None,
        catalog_score=None,
        metadata={},
        status="pending",
    )
    digest = question_digest(canonical)
    question = dict(
        ledger_id=str(ledger),
        question_generation=str(generation),
        body_digest=digest.hex(),
        current_body_digest=digest.hex(),
        target_name="home",
        complete_input=True,
    )
    plan = dict(source_name="finance", decision_id=str(decision), manifest_digest=(b"m" * 32).hex())

    class Pool:
        def __init__(self):
            self.floor = None
            self.unknown = False
            self.fail = False
            self.trace = []

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            old = deepcopy(self.floor)
            self.trace.append("begin")
            try:
                yield
            except BaseException:
                self.floor = old
                self.trace.append("rollback")
                raise
            self.trace.append("commit")

        async def fetchrow(self, sql, *args):
            if "FROM public.delegation_ledger" in sql:
                assert args == (ledger,) and "FOR UPDATE" in sql
                return canonical
            assert "FROM location_received_question_source_floors" in sql
            assert args == ("finance", ledger)
            self.trace.append("readback")
            return None if self.unknown and self.trace[-2] == "acquire" else self.floor

        async def execute(self, sql, *args):
            assert (
                "INSERT INTO location_received_question_source_floors" in sql
                and "ON CONFLICT DO NOTHING" in sql
            )
            self.trace.append("floor")
            if self.floor is None:
                self.floor = dict(
                    zip(
                        (
                            "source_name",
                            "ledger_id",
                            "question_generation",
                            "body_digest",
                            "decision_id",
                            "manifest_digest",
                        ),
                        args,
                    )
                )
            if self.fail:
                raise RuntimeError("planted source fence receipt failure")

    pool = Pool()

    async def lock(conn):
        assert conn is pool
        pool.trace.append("policy")

    runtime = SimpleNamespace(domain=pool, name="home", active=True, lock_domain=lock)
    writer = SimpleNamespace(runtime=runtime)
    register_writer(pool, writer)
    try:
        assert not await seal_question_source_floor(
            runtime, plan, question | dict(complete_input=False)
        )
        assert not await seal_question_source_floor(
            runtime, plan, question | dict(target_name="other")
        )
        assert pool.floor is None
        with pytest.raises(PolicyUnavailableError, match="original binding differs"):
            await seal_question_source_floor(
                runtime, plan, {k: v for k, v in question.items() if k != "current_body_digest"}
            )
        assert pool.floor is None
        canonical["question"] = "independent changed canonical body"
        with pytest.raises(PolicyUnavailableError, match="current body differs"):
            await seal_question_source_floor(runtime, plan, question)
        assert pool.floor is None
        canonical["question"] = "synthetic original source"
        pool.fail = True
        with pytest.raises(RuntimeError, match="source fence receipt failure"):
            await seal_question_source_floor(runtime, plan, question)
        assert pool.floor is None and "rollback" in pool.trace
        pool.fail = False
        pool.trace.clear()
        assert await seal_question_source_floor(runtime, plan, question)
        stored = deepcopy(pool.floor)
        assert (
            pool.floor["question_generation"] == generation and pool.floor["body_digest"] == digest
        )
        assert pool.trace.index("policy") < pool.trace.index("floor") < pool.trace.index("commit")
        assert pool.trace.index("commit") < len(pool.trace) - 1 and pool.trace[-1] == "readback"
        assert await seal_question_source_floor(runtime, plan, question) and pool.floor == stored
        with pytest.raises(PolicyUnavailableError, match="source floor differs"):
            await seal_question_source_floor(
                runtime, plan | dict(manifest_digest=(b"x" * 32).hex()), question
            )
        assert pool.floor == stored
        pool.unknown = True
        with pytest.raises(PolicyUnavailableError, match="census floor is unknown"):
            await seal_question_source_floor(runtime, plan, question)
        assert pool.floor == stored
        pool.unknown = False
        assert await seal_question_source_floor(runtime, plan, question)
        runtime.active = False
        with pytest.raises(PolicyUnavailableError, match="constructor differs"):
            await seal_question_source_floor(runtime, plan, question)
        assert pool.floor == stored
    finally:
        clear_writer(pool, writer)
    await _assert_question_attempt_census_values()


async def _assert_question_attempt_census_values():
    """Actual full left-joined census and fixed-result checker; software doubles."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_reconciliation import (
        question_attempt_census,
        require_question_census,
    )

    ledger, question, decision, incarnation = uuid4(), uuid4(), uuid4(), uuid4()
    plan = dict(source_name="finance", decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    selected = dict(
        ledger_id=str(ledger), question_generation=str(question), body_digest=(b"b" * 32).hex()
    )
    row = dict(
        source_name="finance",
        binding_count=1,
        body_digest=b"b" * 32,
        receiving_incarnation=incarnation,
        floor_incarnation=incarnation,
        floor_question=question,
        floor_source="finance",
        floor_digest=b"b" * 32,
        decision_id=decision,
        manifest_digest=b"m" * 32,
        receipt_id=uuid4(),
    )

    class Pool:
        rows = []
        floor = dict(
            question_generation=question,
            body_digest=b"b" * 32,
            decision_id=decision,
            manifest_digest=b"m" * 32,
        )

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchrow(self, sql, *args):
            assert "FROM location_received_question_source_floors" in sql and args == (
                "finance",
                ledger,
            )
            return self.floor

        async def fetch(self, sql, *args):
            assert "LEFT JOIN location_received_delegation_floors" in sql
            assert "LEFT JOIN location_received_delegation_dispositions" in sql
            assert "a.source_name IS NULL" in sql and args == (ledger, "finance")
            return self.rows

    pool = Pool()

    async def lock(conn):
        assert conn is pool

    writer = SimpleNamespace(pending={}, receiving={})
    runtime = SimpleNamespace(
        domain=pool,
        name="home",
        incarnation=incarnation,
        lock_domain=lock,
        delegation_writer=writer,
    )
    result = await question_attempt_census(runtime, plan, selected)
    assert result["attempt_count"] == 0 and result["pending"] is False
    require_question_census(dict(source_census=[result]), plan, selected, "home")
    pool.rows = [row]
    result = await question_attempt_census(runtime, plan, selected)
    assert result["attempt_count"] == 1 and result["pending"] is False
    require_question_census(dict(source_census=[result]), plan, selected, "home")
    for field, bad in [
        ("binding_count", 0),
        ("binding_count", 2),
        ("source_name", None),
        ("body_digest", b"x" * 32),
        ("receiving_incarnation", uuid4()),
        ("floor_incarnation", uuid4()),
        ("floor_question", uuid4()),
        ("floor_source", None),
        ("floor_digest", None),
        ("decision_id", uuid4()),
        ("manifest_digest", b"x" * 32),
        ("receipt_id", None),
    ]:
        old = row[field]
        row[field] = bad
        pending = await question_attempt_census(runtime, plan, selected)
        assert pending["pending"] is True and pending["attempt_count"] == 1
        with pytest.raises(PolicyUnavailableError, match="census is pending"):
            require_question_census(dict(source_census=[pending]), plan, selected, "home")
        row[field] = old
    pool.rows = [row, row | dict(receipt_id=None)]
    result = await question_attempt_census(runtime, plan, selected)
    assert (
        result["attempt_count"] == 2 and result["pending"] is True
    )  # No surviving terminal JOIN census.
    pool.rows = [row]
    writer.pending["actual private cell"] = SimpleNamespace(ledger=ledger)
    assert (await question_attempt_census(runtime, plan, selected))["pending"] is True
    writer.pending.clear()
    writer.receiving[uuid4()] = SimpleNamespace(ledger=ledger)
    assert (await question_attempt_census(runtime, plan, selected))["pending"] is True
    writer.receiving.clear()
    result = await question_attempt_census(runtime, plan, selected)
    assert result["pending"] is False
    for prepared in (
        {},
        None,
        dict(source_census=None),
        dict(source_census=[None]),
        dict(source_census=[]),
        dict(source_census=[result, result]),
    ):
        with pytest.raises(PolicyUnavailableError, match="census is unavailable"):
            require_question_census(prepared, plan, selected, "home")
    for field, bad in [
        ("source_name", "other"),
        ("body_digest", (b"x" * 32).hex()),
        ("ledger_id", str(uuid4())),
        ("decision_id", str(uuid4())),
        ("manifest_digest", (b"x" * 32).hex()),
        ("receiver_name", "other"),
        ("attempt_count", True),
        ("attempt_count", -1),
        ("pending", None),
    ]:
        with pytest.raises(PolicyUnavailableError, match="census is pending"):
            require_question_census(
                dict(source_census=[result | {field: bad}]), plan, selected, "home"
            )
    with pytest.raises(PolicyUnavailableError, match="incarnation differs"):
        require_question_census(
            dict(source_census=[result | dict(receiving_incarnation="forged")]),
            plan,
            selected,
            "home",
        )
    require_question_census(dict(source_census=[result]), plan, selected, "home")
    await _assert_disposition_only_question_recovery_values()
    await _assert_selected_question_census_plan_values()


async def _assert_disposition_only_question_recovery_values():
    """Real owning producer functions with strict rows; no online/SQL credit."""
    import copy
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_recovery import recover_rejected_questions
    from butlers.core.delegation_source import _writers

    receiving, ledger, question, decision, incarnation, server = [uuid4() for _ in range(6)]
    attempt = dict(
        receiving_generation=receiving,
        ledger_id=ledger,
        source_name="finance",
        body_digest=b"b" * 32,
        receiving_incarnation=incarnation,
        server_request=server,
        receiving_session=None,
        tool_generation=None,
    )
    floor = dict(
        source_name="finance",
        ledger_id=ledger,
        question_generation=question,
        body_digest=b"b" * 32,
        decision_id=decision,
        manifest_digest=b"m" * 32,
    )
    plan = dict(source_name="finance", decision_id=str(decision), manifest_digest=(b"m" * 32).hex())
    selected = dict(
        complete_input=True,
        target_name="home",
        ledger_id=str(ledger),
        question_generation=str(question),
        body_digest=(b"b" * 32).hex(),
        loans=[],
    )

    class Pool:
        rows = [attempt]
        recovery = None
        receipt = None
        unavailable = False
        admission = False
        finished = True
        fault_binding = False
        fault_receipt = False
        readbacks = 0
        stop_on_terminal_readback = False
        in_transaction = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            previous = copy.deepcopy((self.recovery, self.receipt))
            self.in_transaction = True
            try:
                yield
            except BaseException:
                self.recovery, self.receipt = previous
                raise
            finally:
                self.in_transaction = False

        async def fetchrow(self, sql, *args):
            if "FROM location_received_question_source_floors" in sql:
                assert self.in_transaction and args == ("finance", ledger)
                return floor
            assert "FROM location_received_question_recoveries" in sql and args == (receiving,)
            if not self.in_transaction:
                self.readbacks += 1
                if self.unavailable:
                    return None
                if "JOIN location_received_question_recovery_dispositions" in sql:
                    if self.stop_on_terminal_readback:
                        runtime.active = False
                    return (
                        None
                        if self.receipt is None
                        else self.recovery | dict(receipt_id=self.receipt)
                    )
                return self.recovery
            return self.recovery

        async def fetch(self, sql, *args):
            assert self.in_transaction and "FROM location_received_delegation_attempts" in sql
            assert args == (ledger,)
            return self.rows

        async def fetchval(self, sql, *args):
            if "FROM location_received_delegation_inputs" in sql:
                assert args == (receiving,) and self.in_transaction
                return self.admission
            if "FROM location_received_delegation_server_finished" in sql:
                assert args == (receiving, server, b"b" * 32) and self.in_transaction
                return self.finished
            assert "SELECT receipt_id FROM location_received_question_recovery_dispositions" in sql
            assert args == (receiving,)
            return None if self.unavailable and not self.in_transaction else self.receipt

        async def execute(self, sql, *args):
            assert self.in_transaction
            if "INSERT INTO location_received_question_recoveries" in sql:
                row = dict(
                    zip(
                        [
                            "receiving_generation",
                            "source_name",
                            "ledger_id",
                            "question_generation",
                            "body_digest",
                            "decision_id",
                            "manifest_digest",
                            "receiving_incarnation",
                        ],
                        args,
                        strict=True,
                    )
                )
                if self.recovery is None:
                    self.recovery = row
                if self.fault_binding:
                    raise RuntimeError("planted recovery binding failure")
                return
            assert "INSERT INTO location_received_question_recovery_dispositions" in sql
            assert args[0] == receiving
            self.receipt = args[1]
            if self.fault_receipt:
                raise RuntimeError("planted recovery receipt failure")

    pool = Pool()
    writer = SimpleNamespace(pending={}, receiving={})
    runtime = SimpleNamespace(
        domain=pool, name="home", incarnation=incarnation, active=True, delegation_writer=writer
    )
    writer.runtime = runtime

    async def lock(conn):
        assert conn is pool and pool.in_transaction and runtime.active

    runtime.lock_domain = lock
    _writers[pool] = writer
    try:
        assert (
            await recover_rejected_questions(runtime, plan, selected | dict(complete_input=False))
            == []
        )
        for field, value in [
            ("source_name", None),
            ("body_digest", b"x" * 32),
            ("receiving_incarnation", uuid4()),
        ]:
            saved = attempt[field]
            attempt[field] = value
            assert await recover_rejected_questions(runtime, plan, selected) == []
            assert pool.recovery is None and pool.receipt is None
            attempt[field] = saved
        writer.pending["private live cell"] = SimpleNamespace(receiving=receiving)
        assert await recover_rejected_questions(runtime, plan, selected) == []
        writer.pending.clear()
        writer.receiving[receiving] = object()
        assert await recover_rejected_questions(runtime, plan, selected) == []
        writer.receiving.clear()
        pool.admission = True
        assert await recover_rejected_questions(runtime, plan, selected) == []
        assert pool.recovery is None
        pool.admission = False
        pool.finished = False
        assert await recover_rejected_questions(runtime, plan, selected) == []
        assert pool.recovery is None
        pool.finished = True
        pool.fault_binding = True
        with pytest.raises(RuntimeError, match="binding failure"):
            await recover_rejected_questions(runtime, plan, selected)
        assert pool.recovery is None and pool.receipt is None
        pool.fault_binding = False
        pool.fault_receipt = True
        with pytest.raises(RuntimeError, match="receipt failure"):
            await recover_rejected_questions(runtime, plan, selected)
        assert pool.recovery is not None and pool.receipt is None
        pool.fault_receipt = False
        pool.unavailable = True
        with pytest.raises(PolicyUnavailableError, match="recovery is unknown"):
            await recover_rejected_questions(runtime, plan, selected)
        assert pool.receipt is None
        pool.unavailable = False
        receipts = await recover_rejected_questions(runtime, plan, selected)
        assert receipts == [str(pool.receipt)] and pool.readbacks > 0
        saved = copy.deepcopy(pool.recovery), pool.receipt
        assert await recover_rejected_questions(runtime, plan, selected) == receipts
        assert (pool.recovery, pool.receipt) == saved
        assert "loan_id" not in pool.recovery
        pool.stop_on_terminal_readback = True
        with pytest.raises(PolicyUnavailableError, match="terminal is unknown"):
            await recover_rejected_questions(runtime, plan, selected)
        assert (pool.recovery, pool.receipt) == saved
        pool.stop_on_terminal_readback = False
        runtime.active = True
        assert await recover_rejected_questions(runtime, plan, selected) == receipts
        floor["manifest_digest"] = b"x" * 32
        with pytest.raises(PolicyUnavailableError, match="source floor differs"):
            await recover_rejected_questions(runtime, plan, selected)
        assert (pool.recovery, pool.receipt) == saved
        floor["manifest_digest"] = b"m" * 32
        assert await recover_rejected_questions(runtime, plan, selected) == receipts
    finally:
        _writers.pop(pool, None)


async def _assert_selected_question_census_plan_values():
    """Stored selector only routes; exact native owner plan is separately required."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_question_reconciliation import selected_question_source_plan

    decision, ledger = uuid4(), uuid4()
    canonical = dict(asking_butler="finance", target_butler="home")
    question = dict(ledger_id=str(ledger), target_name="home")
    plan = dict(source_name="finance", decision_id=str(decision), question_cohort=[question])
    calls = []

    class Pool:
        locked = False

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            try:
                yield
            finally:
                self.locked = False

        async def fetchrow(self, sql, *args):
            assert self.locked and "public.delegation_ledger" in sql and args == (ledger,)
            return canonical

    pool = Pool()

    async def lock(conn):
        assert conn is pool
        pool.locked = True

    async def route(target, tool, args):
        assert not pool.locked
        calls.append((target, tool, args))
        assert target == "finance" and tool == "location_retention_question_owner_plan"
        assert args == dict(decision_id=str(decision))
        return plan

    runtime = SimpleNamespace(domain=pool, name="home", lock_domain=lock, routed_tool=route)
    assert await selected_question_source_plan(runtime, decision, ledger) == plan
    assert len(calls) == 1
    canonical["target_butler"] = "relationship"
    with pytest.raises(PolicyUnavailableError, match="target is unavailable"):
        await selected_question_source_plan(runtime, decision, ledger)
    assert len(calls) == 1
    canonical["target_butler"] = "home"
    for field, bad in [("source_name", "relationship"), ("decision_id", str(uuid4()))]:
        original = plan[field]
        plan[field] = bad
        with pytest.raises(PolicyUnavailableError, match="owning source differs"):
            await selected_question_source_plan(runtime, decision, ledger)
        plan[field] = original
    for cohort in [
        [],
        [question, question],
        [question | dict(ledger_id=str(uuid4()))],
        [question | dict(target_name="relationship")],
    ]:
        plan["question_cohort"] = cohort
        with pytest.raises(PolicyUnavailableError, match="original question differs"):
            await selected_question_source_plan(runtime, decision, ledger)
    plan["question_cohort"] = [question]
    assert await selected_question_source_plan(runtime, decision, ledger) == plan


async def _assert_catalog_terminal_complete_ancestry_values():
    """Actual terminal producer under a strict software writer, no SQL credit."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from uuid import uuid4

    from butlers.chronicler.location_catalog_copies import (
        catalog_frontier_closed,
        catalog_holder_inventory,
        dispose_catalog_artifacts,
        require_catalog_artifact_ancestry,
    )
    from butlers.chronicler.location_memory_copies import _receivers, artifact_content_digest
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import _plan_status_on_conn

    decision, generation, identifier, bundle = (uuid4() for _ in range(4))
    original = dict(id=identifier, content="native rule sentinel", metadata={})
    artifact = dict(
        artifact_generation=generation,
        artifact_id=identifier,
        input_generation=bundle,
        memory_table="rules",
        body_digest=content_digest({"memory_artifact": _digest_value(original)}),
        content_digest=artifact_content_digest("rules", original),
    )
    full = [
        dict(
            artifact_generation=generation,
            parent_count=2,
            copy_generation=uuid4(),
            input_digest=b"p" * 32,
            birth_digest=b"p" * 32,
            output_id=uuid4(),
        )
        for _ in range(2)
    ]

    reduced_catalog = dict(
        id=uuid4(),
        source_schema="chronicler_mem",
        source_butler="chronicler",
        source_table="rules",
        source_id=identifier,
        artifact_id=identifier,
        memory_table="rules",
        original_artifact_generation=uuid4(),
        current_generation=uuid4(),
        head_disposed=True,
        summary="",
        confidence=0,
        invalid_at="fixed software timestamp",
        title=None,
        predicate=None,
        scope=None,
        valid_at=None,
        embedding=None,
        search_vector=None,
        entity_id=None,
        object_entity_id=None,
        importance=None,
        tenant_id="",
        memory_type="rule",
        retention_class=None,
        sensitivity=None,
    )
    reduced_catalog["original_catalog_id"] = reduced_catalog["id"]

    class Pool:
        catalog_rows = []
        parents = full
        body = deepcopy(original)
        terminal = None
        selected_catalog = None
        generations = []
        fault = False
        unknown_ack = False
        trace = []

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("acquire")
            yield self

        @asynccontextmanager
        async def transaction(self):
            snapshot = deepcopy((self.body, self.terminal, self.selected_catalog))
            try:
                yield
            except BaseException:
                self.body, self.terminal, self.selected_catalog = snapshot
                raise
            else:
                self.trace.append("commit")

        async def fetch(self, sql, *args):
            if "SELECT a.artifact_generation,i.parent_count" in sql:
                self.trace.append("ancestry")
                return self.parents
            if sql.startswith("SELECT DISTINCT g.catalog_id AS original_catalog_id"):
                self.trace.append("current_catalog_read")
                cursor = None if args[1] is None else (args[1], args[2])
                return [
                    row
                    for row in sorted(
                        self.catalog_rows,
                        key=lambda row: (
                            row["original_catalog_id"],
                            row["original_artifact_generation"],
                        ),
                    )
                    if cursor is None
                    or (row["original_catalog_id"], row["original_artifact_generation"]) > cursor
                ][:64]
            if sql.startswith("WITH artifacts AS"):
                return [dict(holder_generation=generation, receipt_id=self.terminal)]
            if "SELECT DISTINCT a.*" in sql:
                return [artifact] if self.terminal is None else []
            if "SELECT * FROM chronicler.location_native_catalog_generations" in sql:
                return self.generations
            if "location_native_memory_mutations" in sql:
                return []
            raise AssertionError("Unmodeled catalog fetch")

        async def fetchrow(self, sql, *args):
            if "FROM location_retention_plans" in sql:
                self.trace.append("plan_read")
                return None  # Deliberately absent locator, after ancestry validation.
            if "location_retention_policy" in sql:
                self.trace.append("policy")
                return dict(version=1)
            if "location_runtime_context_artifacts" in sql:
                return None
            if "public.memory_catalog" in sql:
                return self.selected_catalog
            if sql.startswith("SELECT * FROM rules"):
                self.trace.append("body_read")
                return self.body
            raise AssertionError("Unmodeled catalog row read")

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "chronicler_mem"
            if sql == "SELECT current_user":
                return "own-role-double"
            if sql.startswith("DELETE FROM rules"):
                self.trace.append("delete")
                self.body = None
                return identifier
            if sql.startswith("SELECT EXISTS(SELECT 1 FROM rules"):
                self.trace.append("body_readback")
                return self.body is not None
            if sql.startswith(
                "SELECT receipt_id FROM location_native_memory_artifact_dispositions"
            ):
                self.trace.append("receipt_readback")
                return None if self.unknown_ack else self.terminal
            if sql.startswith("SELECT EXISTS(SELECT 1 FROM location_native_memory_artifacts"):
                return self.terminal is None
            if any(
                part in sql
                for part in (
                    "SELECT NOT EXISTS(SELECT 1 FROM chronicler.location_native_dispatch_parents",
                    "location_native_processing_parents",
                    "location_native_catalog_loans",
                    "memory_links",
                    "public.memory_catalog",
                    "location_native_catalog_generations",
                    "location_native_memory_artifacts",
                )
            ):
                return False  # All modeled candidate lifetime/selection blockers are closed.
            raise AssertionError("Unmodeled catalog value read")

        async def execute(self, sql, *args):
            if "pg_advisory_xact_lock" in sql:
                return
            if sql.startswith("UPDATE public.memory_catalog SET"):
                assert args == (self.selected_catalog["id"], "rule")
                self.trace.append("catalog_update")
                self.selected_catalog.update(
                    summary="",
                    title=None,
                    predicate=None,
                    scope=None,
                    valid_at=None,
                    embedding=None,
                    search_vector=None,
                    entity_id=None,
                    object_entity_id=None,
                    confidence=0,
                    importance=None,
                    tenant_id="",
                    memory_type=args[1],
                    retention_class=None,
                    sensitivity=None,
                    invalid_at="reduced timestamp",
                )
                return
            if "INSERT INTO chronicler.location_native_catalog_dispositions" in sql:
                self.trace.append("catalog_receipt")
                return
            if "INSERT INTO chronicler.location_native_memory_artifact_dispositions" in sql:
                self.trace.append("receipt")
                if self.fault:
                    raise RuntimeError("synthetic receipt fault")
                self.terminal = args[3]
                return
            raise AssertionError("Unmodeled catalog write")

    pool, domain = Pool(), object()
    previous = _receivers.get(domain)
    _receivers[domain] = (pool, "chronicler_mem", "own-role-double")
    # The actual producer uses the domain for candidates/readback; same fixed
    # strict writer is registered here, with identity supplied by _lock.
    _receivers[pool] = _receivers[domain]
    try:
        malformed = [
            full[:1],
            [full[0], full[1] | dict(birth_digest=b"x" * 32)],
            [*full, full[0] | dict(copy_generation=uuid4(), output_id=uuid4())],
            [
                full[0]
                | dict(copy_generation=None, input_digest=None, birth_digest=None, output_id=None)
            ],
            [full[0] | dict(parent_count=None)],
        ]
        for parents in malformed:
            pool.parents = parents
            pool.trace.clear()
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                await dispose_catalog_artifacts(pool, decision)
            assert pool.body == original and pool.terminal is None
            assert "delete" not in pool.trace and "receipt" not in pool.trace
            assert pool.trace.index("policy") < pool.trace.index("ancestry")
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                await catalog_frontier_closed(pool, decision)
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                await catalog_holder_inventory(pool, decision)
            with pytest.raises(PolicyUnavailableError, match="complete input ancestry"):
                await _plan_status_on_conn(pool, decision)
            assert "plan_read" not in pool.trace
        pool.parents = []
        with pytest.raises(PolicyUnavailableError, match="ancestry is unavailable"):
            await require_catalog_artifact_ancestry(pool, generation)
        pool.parents = full
        with pytest.raises(PolicyUnavailableError, match="Stored retention decision"):
            await _plan_status_on_conn(pool, decision)
        assert "plan_read" in pool.trace
        assert not await catalog_frontier_closed(pool, decision)
        assert (await catalog_holder_inventory(pool, decision))[0]["receipt_id"] is None
        from butlers.chronicler.location_catalog_copies import _body

        catalog_id, catalog_generation = uuid4(), uuid4()
        native_catalog = reduced_catalog | dict(
            id=catalog_id,
            source_generation=catalog_generation,
            bound_catalog_id=catalog_id,
            bound_artifact_generation=generation,
            summary="native rule sentinel",
            tenant_id="shared",
            memory_type="source-derived freeform type",
            retention_class="source-derived freeform class",
            sensitivity="source-derived freeform sensitivity",
            invalid_at=None,
            updated_at=None,
        )
        native_catalog["body_digest"] = content_digest({"catalog_body": _body(native_catalog)})
        pool.generations = [dict(source_generation=catalog_generation, catalog_id=catalog_id)]
        for drift in (
            dict(source_butler="finance"),
            dict(source_schema="finance_mem"),
            dict(source_table="facts"),
            dict(source_id=uuid4()),
            dict(bound_catalog_id=uuid4()),
            dict(bound_artifact_generation=uuid4()),
            dict(source_generation=uuid4()),
        ):
            pool.selected_catalog = native_catalog | drift
            # Match the stored body witness: identity must refuse independently,
            # not merely because changing a row also changed its body digest.
            pool.selected_catalog["body_digest"] = content_digest(
                {"catalog_body": _body(pool.selected_catalog)}
            )
            snapshot = deepcopy(pool.selected_catalog)
            pool.trace.clear()
            await dispose_catalog_artifacts(pool, decision)
            assert pool.body == original and pool.terminal is None
            assert pool.selected_catalog == snapshot and "catalog_update" not in pool.trace
        pool.selected_catalog = deepcopy(native_catalog)
        pool.trace.clear()
        await dispose_catalog_artifacts(pool, decision)
        assert pool.body is None and pool.terminal is not None
        assert pool.selected_catalog["tenant_id"] == ""
        assert pool.selected_catalog["memory_type"] == "rule"
        assert pool.selected_catalog["retention_class"] is None
        assert pool.selected_catalog["sensitivity"] is None
        for key in ("id", "source_schema", "source_table", "source_id", "source_butler"):
            assert pool.selected_catalog[key] == native_catalog[key]
        assert pool.trace.index("catalog_update") < pool.trace.index("delete")
        pool.body, pool.terminal, pool.selected_catalog = deepcopy(original), None, None
        pool.generations = []
        pool.fault = True
        with pytest.raises(RuntimeError, match="synthetic receipt fault"):
            await dispose_catalog_artifacts(pool, decision)
        assert pool.body == original and pool.terminal is None
        assert "delete" in pool.trace and "receipt" in pool.trace
        pool.fault = False
        pool.unknown_ack = True
        with pytest.raises(
            PolicyUnavailableError, match="Committed artifact disposition is unknown"
        ):
            await dispose_catalog_artifacts(pool, decision)
        assert pool.body is None and pool.terminal is not None
        assert "receipt_readback" in pool.trace
        # This immutable receipt is durable despite the deliberately lost ACK.
        # Reset only this software fixture's original data for the separately
        # positioned actual producer positive; do not infer terminal erasure
        # from the failed business return.
        pool.unknown_ack = False
        pool.body, pool.terminal = deepcopy(original), None
        pool.trace.clear()
        await dispose_catalog_artifacts(pool, decision)
        assert pool.body is None and pool.terminal is not None
        assert pool.trace.index("policy") < pool.trace.index("ancestry")
        assert pool.trace.index("ancestry") < pool.trace.index("body_read")
        assert pool.trace.index("delete") < pool.trace.index("receipt") < pool.trace.index("commit")
        assert pool.trace.index("commit") < pool.trace.index("receipt_readback")
        assert await catalog_frontier_closed(pool, decision)
        # Planted retained body/head beside exact reduced-profile positive;
        # no missing rows can make these drift controls vacuous.
        pool.catalog_rows = [deepcopy(reduced_catalog)]
        assert await catalog_frontier_closed(pool, decision)
        for drift in (
            {"summary": "synthetic resurrected source"},
            {"title": "synthetic resurrected title"},
            {"embedding": [0.1]},
            {"search_vector": "synthetic"},
            {"source_id": uuid4()},
            {"source_schema": "finance_mem"},
            {"source_butler": "finance"},
            {"source_table": "facts"},
            {"invalid_at": None},
            {"head_disposed": False},
            {"id": None},
            {"tenant_id": "synthetic precise source copied into tenant label"},
            {"memory_type": "synthetic precise source copied into type"},
            {"retention_class": "synthetic precise source copied into class"},
            {"sensitivity": "synthetic precise source copied into sensitivity"},
        ):
            pool.catalog_rows = [reduced_catalog | drift]
            assert not await catalog_frontier_closed(pool, decision)
        pool.catalog_rows = [deepcopy(reduced_catalog)]
        assert await catalog_frontier_closed(pool, decision)
        many = []
        for _ in range(65):
            catalog_id, artifact_id = uuid4(), uuid4()
            many.append(
                reduced_catalog
                | dict(
                    id=catalog_id,
                    original_catalog_id=catalog_id,
                    original_artifact_generation=uuid4(),
                    source_id=artifact_id,
                    artifact_id=artifact_id,
                )
            )
        many.sort(key=lambda row: (row["original_catalog_id"], row["original_artifact_generation"]))
        pool.catalog_rows = many
        pool.trace.clear()
        assert await catalog_frontier_closed(pool, decision)
        complete_pages = pool.trace.count("current_catalog_read")
        pool.catalog_rows = [*many[:-1], many[-1] | {"title": "planted later-page source"}]
        assert not await catalog_frontier_closed(pool, decision)
        assert complete_pages == 2
        pool.catalog_rows = [deepcopy(reduced_catalog)]
        assert await catalog_frontier_closed(pool, decision)
        receipt = pool.terminal
        await dispose_catalog_artifacts(pool, decision)
        assert pool.terminal == receipt
    finally:
        _receivers.pop(pool, None)
        if previous is None:
            _receivers.pop(domain, None)
        else:
            _receivers[domain] = previous


async def _assert_owntracks_input_complete_cohort_and_server_lifetime():
    """REQ-location-retention-005/006; source/task software, no real SQL/role proof."""
    import asyncio
    from copy import deepcopy
    from uuid import uuid4

    from butlers.connectors.owntracks_input_copies import (
        OwnTracksInputMiddleware,
        require_inputs_ended,
    )

    logical, raw = b"L" * 32, b"R" * 32
    incarnation, bundle, server, processing = (uuid4() for _ in range(4))
    rows = [
        dict(
            copy_generation=generation,
            incarnation=incarnation,
            copy_bundle=bundle,
            bundle_count=2,
            logical_source_digest=logical,
            raw_digest=raw,
            copy_kind=kind,
            producer_contract=1,
            ended_digest=raw,
            server_generation=bundle,
        )
        for generation, kind in ((server, 1), (processing, 2))
    ]

    class Connection:
        captured = rows
        pending_server = False

        async def fetchval(self, sql, actual):
            assert "owntracks_input_server_births" in sql and actual == logical
            return self.pending_server

        async def fetch(self, sql, actual):
            assert "LEFT JOIN" in sql and "owntracks_input_copy_ends" in sql
            assert actual == logical
            return self.captured

    connection = Connection()
    await require_inputs_ended(connection, processing, logical, raw)
    connection.pending_server = True
    with pytest.raises(ValueError, match="server cohort is still active"):
        await require_inputs_ended(connection, processing, logical, raw)
    connection.pending_server = False
    with pytest.raises(ValueError, match="original source birth"):
        await require_inputs_ended(connection, None, logical, raw)
    for changed in (
        rows[1:],  # Full original two-member bundle cannot become one closed input.
        [rows[0]],  # Missing selected processing birth is not an empty positive.
        [*rows, {**rows[0], "copy_generation": uuid4()}],
        [{**rows[0], "ended_digest": None}, rows[1]],
        [{**rows[0], "raw_digest": b"X" * 32}, rows[1]],
        [{**rows[0], "incarnation": uuid4()}, rows[1]],
    ):
        connection.captured = deepcopy(changed)
        with pytest.raises(ValueError):
            await require_inputs_ended(connection, processing, logical, raw)
    connection.captured = rows
    await require_inputs_ended(connection, processing, logical, raw)

    observed, binding, header = [], object(), object()

    class Connector:
        committed = False
        commit_gate = None
        commit_failed = False

        def _native_server_authorized(self, scope):
            return True

        def _allocate_native_server(self):
            return header

        async def _commit_native_server(self, actual):
            assert actual is header
            if self.commit_gate is not None:
                await self.commit_gate.wait()
            if self.commit_failed:
                raise ValueError("synthetic admission readback unavailable")
            self.committed = True

        def _observe_native_server_end(self, actual):
            assert actual is header
            observed.append("webhook_server")

    connector = Connector()

    entered = []

    async def application(scope, receive, send):
        entered.append(True)
        assert connector.committed
        assert scope["_owntracks_native_input"] == [header]
        scope["_owntracks_native_input"].append(binding)

    wrapper = OwnTracksInputMiddleware(application, connector=connector)
    inner_ended, finish_server = asyncio.Event(), asyncio.Event()

    async def actual_server_task():
        await wrapper({"type": "http", "path": "/owntracks/webhook"}, None, None)
        inner_ended.set()
        await finish_server.wait()  # Source-owned server task still retains its lifetime.

    task = asyncio.create_task(actual_server_task())
    try:
        await inner_ended.wait()
        assert observed == []  # Inner ASGI return alone is not actual server-task completion.
        from contextlib import asynccontextmanager

        from tests.chronicler.owntracks_input_transport_helpers import wait_owntracks_inputs_ended

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield connection

        reconciled = asyncio.Event()

        class Runtime:
            async def reconcile_observed_ends(self):
                # Metadata I/O is doubled; the observer is the real original
                # middleware Task done callback, not a caller end/HTTP result.
                connection.pending_server = observed == []
                reconciled.set()

        point = dict(
            source_input_generation=processing,
            logical_source_digest=logical,
            content_digest=raw,
        )
        qualification = asyncio.create_task(wait_owntracks_inputs_ended(Runtime(), Pool(), point))
        await reconciled.wait()
        await asyncio.sleep(0)
        assert not qualification.done() and connection.pending_server
        finish_server.set()
        await task
        await asyncio.sleep(0)  # Execute actual Task done callbacks, not a duration witness.
        assert observed == ["webhook_server"]
        await qualification
        assert not connection.pending_server
        connection.captured = rows[1:]
        with pytest.raises(ValueError, match="original bundle is incomplete"):
            await wait_owntracks_inputs_ended(Runtime(), Pool(), point)
        connection.captured = rows
        await wait_owntracks_inputs_ended(Runtime(), Pool(), point)
    finally:
        if "qualification" in locals():
            if not qualification.done():
                qualification.cancel()
            await asyncio.gather(qualification, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    observed.clear()
    inner_ended.clear()
    finish_server.clear()
    cancelled = asyncio.create_task(actual_server_task())
    await inner_ended.wait()
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    await asyncio.sleep(0)
    assert observed == []  # Cancelled server task does not manufacture terminal history.

    observed.clear()
    entered.clear()
    connector.committed = False
    connector.commit_gate = asyncio.Event()
    admitted = asyncio.create_task(
        wrapper({"type": "http", "path": "/owntracks/webhook"}, None, None)
    )
    await asyncio.sleep(0)
    assert entered == [] and observed == []
    connector.commit_gate.set()
    await admitted
    await asyncio.sleep(0)
    assert entered == [True] and observed == ["webhook_server"]

    observed.clear()
    entered.clear()
    connector.commit_gate = None
    connector.commit_failed = True
    connector.committed = False
    messages = []

    async def send_refusal(message):
        messages.append(message)

    refused = asyncio.create_task(
        wrapper({"type": "http", "path": "/owntracks/webhook"}, None, send_refusal)
    )
    await refused
    await asyncio.sleep(0)
    assert entered == [] and not connector.committed
    assert messages[0]["status"] == 503 and messages[1]["type"] == "http.response.body"
    assert observed == ["webhook_server"]


async def _assert_native_owntracks_replay_reader_lifetime():
    """Actual native reader order/network lifetime; SQL selectors doubled only."""
    import asyncio
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.connectors.owntracks import OwnTracksConnector

    trace, terminal = [], []
    row_id, header, binding = uuid4(), object(), object()
    endpoint = "owntracks:synthetic"
    payload = {
        "source": {"channel": "owntracks", "provider": "owntracks", "endpoint_identity": endpoint},
        "payload": {"raw": {"_type": "location", "tst": 1700000000, "tid": "synthetic"}},
    }

    class Connection:
        transaction_open = False
        status = "replay_pending"

        @asynccontextmanager
        async def transaction(self):
            assert not self.transaction_open
            self.transaction_open = True
            try:
                yield
            finally:
                self.transaction_open = False
                trace.append("commit")

        async def fetch(self, sql, actual):
            assert actual == endpoint and "SELECT id,received_at" in sql
            assert "header_readback" in trace
            trace.append("stored_read")
            return [{"id": row_id, "received_at": datetime(2026, 1, 1, tzinfo=UTC)}]

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_user":
                return "connector_writer"
            if "pg_try_advisory_lock" in sql:
                return True
            if "SELECT full_payload" in sql:
                assert self.transaction_open
                trace.append("payload_read")
                return payload
            if "UPDATE connectors.filtered_events" in sql:
                assert self.transaction_open and args[0] == "replay_complete"
                assert args[-1] == payload
                self.status = args[0]
                return self.status
            assert "SELECT status FROM connectors.filtered_events" in sql
            assert not self.transaction_open
            trace.append("status_readback")
            return self.status

        async def execute(self, sql, *args):
            assert sql.startswith("SET LOCAL") or "pg_advisory" in sql
            if "pg_advisory_unlock" in sql:
                trace.append("claim_release")

    conn = Connection()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    class Runtime:
        pool = Pool()

        def allocate_server(self):
            return header

        async def commit_server(self, actual):
            assert actual is header
            trace.append("header_readback")

        async def reserve(self, actual, raw, *, replay):
            assert actual == endpoint and raw == payload["payload"]["raw"] and replay
            assert not conn.transaction_open
            trace.append("birth_readback")
            return binding

        def require_body(self, actual, source, raw):
            assert actual is binding and source == endpoint and raw == payload["payload"]["raw"]

        def processing_started(self, actual):
            assert actual is binding

    connector = OwnTracksConnector.__new__(OwnTracksConnector)
    connector._input_copies = Runtime()
    connector._db_pool = connector._input_copies.pool
    connector._observe_native_server_end = lambda actual: terminal.append((actual, "server"))
    connector._observe_native_input_end = lambda actual, kind: terminal.append((actual, kind))

    async def submit(envelope):
        assert not conn.transaction_open  # Domain locks never surround provider I/O.
        assert trace.index("payload_read") < trace.index("birth_readback")
        assert "birth_readback" in trace and terminal == []
        trace.append("send")

    connector._submit_envelope = submit
    inner_ended, finish = asyncio.Event(), asyncio.Event()

    async def native_task():
        await connector._drain_native_replay(endpoint)
        inner_ended.set()
        await finish.wait()

    task = asyncio.create_task(native_task())
    try:
        await asyncio.wait_for(inner_ended.wait(), 2)
        assert terminal == [] and conn.status == "replay_complete"
        assert trace.index("header_readback") < trace.index("stored_read")
        assert trace.index("send") < trace.index("status_readback") < trace.index("claim_release")
        finish.set()
        await task
        await asyncio.sleep(0)
        assert terminal == [(header, "server"), (binding, "replay_processing")]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _assert_native_input_dispatch_submission_fence():
    """Actual dispatch ordering under a positioned cross-loop scheduler double."""
    from concurrent.futures import Future
    from types import SimpleNamespace
    from unittest.mock import patch

    from butlers.connectors.owntracks import OwnTracksConnector

    binding = object()
    trace = []

    class Runtime:
        pending = False

        def reserve_processing(self, actual):
            assert actual is binding
            self.pending = True
            trace.append("reserved")

        def processing_started(self, actual):
            assert actual is binding and self.pending
            self.pending = False
            trace.append("issued")

        def processing_not_issued(self, actual):
            assert actual is binding
            self.pending = False
            trace.append("not-issued")

    runtime = Runtime()
    connector = object.__new__(OwnTracksConnector)
    connector._main_loop = SimpleNamespace(is_closed=lambda: False)
    connector._input_copies = runtime
    connector._observe_native_input_end = lambda actual, kind: trace.append("ended")

    def run_now(coroutine, loop):
        # Position the main-loop work before the submitting server can mark
        # the returned Future. Without its prior hold, server completion could
        # misclassify this actual issued child as never issued.
        coroutine.close()
        assert runtime.pending, "processing could run before submission hold"
        trace.append("published")
        future = Future()
        future.set_result(None)
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=run_now):
        connector._dispatch_event_to_main_loop({"_type": "location"}, _binding=binding)
    assert trace == ["reserved", "published", "issued"]
    assert runtime.pending is False
    trace.clear()
    captured = []

    def refuse_submission(coroutine, loop):
        assert runtime.pending
        captured.append(coroutine)
        raise RuntimeError("synthetic submission refused")

    with patch("asyncio.run_coroutine_threadsafe", side_effect=refuse_submission):
        with pytest.raises(RuntimeError, match="synthetic submission refused"):
            connector._dispatch_event_to_main_loop({"_type": "location"}, _binding=binding)
    assert trace == ["reserved", "not-issued"] and runtime.pending is False
    assert len(captured) == 1 and captured[0].cr_frame is None

    # Actual source Task cancellation may take time to unwind. Its caught
    # cancellation still settles only AFTER that Task ends, never at wrapper
    # return/finally or when a transport Future reports cancelled.
    import asyncio

    entered, releasing, unwinding = asyncio.Event(), asyncio.Event(), asyncio.Event()
    connector._native_processing_tasks = set()
    trace.clear()

    async def processing(body, *, _native_binding):
        assert _native_binding is binding
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            unwinding.set()
            await releasing.wait()

    connector._process_webhook_event = processing
    task = asyncio.create_task(connector._process_native_input({"_type": "location"}, binding))
    await entered.wait()
    assert task in connector._native_processing_tasks and trace == []
    task.cancel()
    await unwinding.wait()
    assert not task.done() and trace == []
    releasing.set()
    await task
    await asyncio.sleep(0)
    assert task not in connector._native_processing_tasks and trace == ["ended"]


async def _assert_native_input_shutdown_preserves_actual_lifetimes():
    """Actual shutdown/Task ordering; software only, no SQL disposition credit."""
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.connectors.owntracks import OwnTracksConnector
    from butlers.connectors.owntracks_input_copies import (
        OwnTracksInputCopies,
        OwnTracksInputMiddleware,
    )

    # Exercise the real allocation fence without a fake constructor identity:
    # its constructor/Pool authority is separately positioned in the PG species.
    runtime = object.__new__(OwnTracksInputCopies)
    runtime.incarnation = uuid4()
    runtime._closing, runtime._servers, runtime._bindings = False, {}, {}
    admitted = runtime.allocate_server()
    runtime.close_admission()
    with pytest.raises(ValueError, match="capacity is unavailable"):
        runtime.allocate_server()
    assert runtime._servers[id(admitted)] is admitted  # Old lifetime was not disposed.
    messages = []

    async def no_body(*args):
        raise AssertionError("closed admission received or dispatched a body")

    async def send(message):
        messages.append(message)

    closed_connector = SimpleNamespace(
        _native_server_authorized=lambda scope: True,
        _allocate_native_server=runtime.allocate_server,
    )
    await OwnTracksInputMiddleware(no_body, connector=closed_connector)(
        {"type": "http", "path": "/owntracks/webhook"}, no_body, send
    )
    assert messages[0]["status"] == 503 and messages[-1]["type"] == "http.response.body"
    assert runtime._servers[id(admitted)] is admitted

    entered, release = asyncio.Event(), asyncio.Event()
    trace = []
    connector = object.__new__(OwnTracksConnector)
    connector._native_processing_tasks = set()
    connector._health_server = SimpleNamespace(should_exit=False)
    connector._devices = {}
    connector._db_pool = None
    binding = object()

    async def processing(body, *, _native_binding):
        assert _native_binding is binding
        entered.set()
        await release.wait()
        trace.append("body-released")

    connector._process_webhook_event = processing
    connector._observe_native_input_end = lambda actual, kind: trace.append("actual-task-ended")

    class Source:
        closed = False

        def close_admission(self):
            self.closed = True
            trace.append("admission-closed")

        async def reconcile_observed_ends(self):
            assert connector._native_processing_tasks == set()
            assert "actual-task-ended" in trace
            trace.append("readback")

    source = Source()
    connector._input_copies = source
    loop = asyncio.get_running_loop()

    class Server:
        def join(self, bound):
            assert source.closed and connector._health_server.should_exit
            assert bound == 5 and "actual-task-ended" not in trace
            # Join work runs off the owning event loop. Its pool/Task remains
            # able to unwind while the actual source server is being joined.
            loop.call_soon_threadsafe(release.set)

    class Retention:
        async def stop(self):
            assert trace.index("body-released") < trace.index("actual-task-ended")
            assert trace.index("actual-task-ended") < trace.index("readback")
            trace.append("retention-stopped")

    connector._health_thread, connector._retention = Server(), Retention()
    task = asyncio.create_task(connector._process_native_input({}, binding))
    await entered.wait()
    assert task in connector._native_processing_tasks and trace == []
    await connector._shutdown()
    assert task.done() and connector._native_processing_tasks == set()
    assert trace == [
        "admission-closed",
        "body-released",
        "actual-task-ended",
        "readback",
        "retention-stopped",
    ]


def _assert_native_input_closed_diagnostics():
    """Actual closed formatter preserves classification without copying error args."""
    from unittest.mock import patch

    import asyncpg

    from butlers.connectors.owntracks_input_copies import _log_input_failure

    private = "synthetic-private-body-should-not-be-recorded"
    with patch("butlers.connectors.owntracks_input_copies._logger.warning") as warning:
        _log_input_failure("point_write", asyncpg.InsufficientPrivilegeError(private))
        _log_input_failure(private, ValueError(private))
    formatted = [call.args[0] % call.args[1:] for call in warning.call_args_list]
    assert formatted == [
        "OwnTracks input failure stage=point_write category=postgres "
        "sqlstate=42501 class=insufficient_privilege",
        "OwnTracks input failure stage=unknown category=native sqlstate=unknown class=value_error",
    ]
    assert all(private not in message for message in formatted)


async def _assert_native_ingress_census_and_actual_task_end():
    """Actual task/guard software; SQL selection and configured roles are hosted-only."""
    import asyncio
    import json

    # Execute the same helper used by the registered HTTP owning species.
    # Only policy loading is replaced; real FastMCP decorators retain names,
    # parser schemas and tool registration. This is software, not SQL proof.
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from fastmcp import FastMCP

    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import (
        IngressServerLifetime,
        SwitchboardInputCopies,
        _Header,
        require_ingress_closed,
    )
    from butlers.core_tools._base import ToolContext
    from butlers.location_retention import content_digest, logical_digest
    from tests.chronicler.owntracks_input_transport_helpers import register_native_switchboard_tools

    actual_mcp = FastMCP("native registration software control")
    context = ToolContext(
        daemon=SimpleNamespace(_pipeline=None, _buffer=None),
        pool=object(),
        spawner=None,
        butler_name="switchboard",
        butler_type=None,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )
    with patch("butlers.ingestion_policy.IngestionPolicyEvaluator.ensure_loaded", new=AsyncMock()):
        register_native_switchboard_tools(context, actual_mcp)
        await asyncio.sleep(0)
    actual_names = {tool.name for tool in await actual_mcp.list_tools()}
    assert {"ingest", "connector.heartbeat", "backfill.poll", "backfill.progress"} <= actual_names
    assert "connector_heartbeat" not in actual_names
    await _assert_registered_direct_ingress_diagnostics(actual_mcp)

    request, generation, server = uuid4(), uuid4(), uuid4()
    stored = {"raw_payload": {"synthetic": "source"}, "normalized_text": "synthetic text"}
    complete = {
        "copy_generation": generation,
        "handler_generation": uuid4(),
        "copy_kind": 1,
        "parent_generation": None,
        "original_parent": None,
        "parent_request": None,
        "parent_digest": None,
        "server_generation": server,
        "request_id": request,
        "stored_digest": content_digest(stored),
        "ended": generation,
        "server_ended": server,
    }

    class Conn:
        pending = False
        rows = [complete]

        async def execute(self, sql, *args):
            assert "pg_advisory_xact_lock" in sql

        async def fetchval(self, sql, *args):
            if "FROM location_ingress_structured_inputs" in sql:
                assert "LEFT JOIN location_ingress_input_births" in sql
                return getattr(self, "structured_pending", False)
            assert "location_ingress_server_births" in sql
            return self.pending

        async def fetch(self, sql, *args):
            assert "LEFT JOIN location_ingress_accepted_inputs" in sql
            return self.rows

        async def fetchrow(self, sql, *args):
            assert "FROM location_ingress_inbox_sources" in sql
            return getattr(
                self,
                "original",
                {
                    "dedupe_digest": logical_digest("canonical-key"),
                    "stored_digest": content_digest(stored),
                    "copy_kind": 1,
                },
            )

    conn = Conn()
    await require_ingress_closed(conn, request, "canonical-key", stored)
    for original in (
        None,
        {"dedupe_digest": b"wrong", "stored_digest": content_digest(stored), "copy_kind": 1},
        {
            "dedupe_digest": logical_digest("canonical-key"),
            "stored_digest": b"changed",
            "copy_kind": 1,
        },
        {
            "dedupe_digest": logical_digest("canonical-key"),
            "stored_digest": content_digest(stored),
            "copy_kind": None,
        },
    ):
        conn.original = original
        with pytest.raises(CopyFloorUnavailable, match="ingress_canonical_source_unknown"):
            await require_ingress_closed(conn, request, "canonical-key", stored)
    del conn.original
    await require_ingress_closed(conn, request, "canonical-key", stored)
    for rows in (
        [],
        [complete | {"request_id": None}],
        [complete | {"handler_generation": None}],
        [complete | {"copy_kind": 2}],
        [complete | {"copy_kind": 1, "parent_generation": uuid4()}],
        [complete | {"stored_digest": b"changed"}],
        [complete | {"ended": None}],
        [complete | {"server_ended": None}],
        [complete, complete | {"copy_generation": uuid4(), "ended": None}],
    ):
        conn.rows = rows
        with pytest.raises(CopyFloorUnavailable, match="ingress_input_cohort_pending"):
            await require_ingress_closed(conn, request, "canonical-key", stored)
    conn.rows = [complete]
    conn.pending = True
    with pytest.raises(CopyFloorUnavailable, match="ingress_server_cohort_pending"):
        await require_ingress_closed(conn, request, "canonical-key", stored)
    conn.pending = False
    await require_ingress_closed(conn, request, "canonical-key", stored)
    original_parent = complete["copy_generation"]
    child = complete | {
        "copy_generation": uuid4(),
        "copy_kind": 2,
        "parent_generation": original_parent,
        "original_parent": original_parent,
        "parent_request": request,
        "parent_digest": content_digest(stored),
    }
    child["ended"] = child["copy_generation"]
    conn.rows = [complete, child]
    await require_ingress_closed(conn, request, "canonical-key", stored)
    for damaged in (
        child | {"original_parent": None},
        child | {"parent_digest": b"different"},
        child | {"parent_request": uuid4()},
        child | {"ended": None},
    ):
        conn.rows = [complete, damaged]
        with pytest.raises(CopyFloorUnavailable, match="ingress_input_cohort_pending"):
            await require_ingress_closed(conn, request, "canonical-key", stored)
    conn.rows = [complete, child]
    await require_ingress_closed(conn, request, "canonical-key", stored)

    # A processing Task end does not proxy disposal of its configured
    # classifier runtime/session. Plant the complete unchanged terminal
    # companion, then independently break each original binding/readback.
    import hashlib

    runtime_copy = child | {
        "copy_kind": 3,
        "runtime_generation": uuid4(),
        "runtime_request": request,
        "runtime_source_digest": content_digest(stored),
        "processing_digest": b"e" * 32,
        "runtime_input_digest": b"e" * 32,
        "runtime_session": request,
        "bound_runtime_session": request,
        "runtime_prompt_digest": b"p" * 32,
        "bound_runtime_prompt": b"p" * 32,
        "runtime_disposition": uuid4(),
        "runtime_current_prompt": "[Location input forgotten]",
        "runtime_current_result": "[Location output forgotten]",
        "runtime_current_calls": [],
        "runtime_current_error": None,
        "runtime_current_system": "Synthetic preserved independent instructions",
        "runtime_current_provenance": {"synthetic": "independent provenance"},
        "runtime_reduced_system": hashlib.sha256(
            b"Synthetic preserved independent instructions"
        ).digest(),
        "runtime_reduced_provenance": content_digest({"synthetic": "independent provenance"}),
    }
    conn.rows = [complete, runtime_copy]
    await require_ingress_closed(conn, request, "canonical-key", stored)
    for field, altered in (
        ("copy_kind", 2),
        ("runtime_request", uuid4()),
        ("runtime_source_digest", b"x" * 32),
        ("runtime_input_digest", b"x" * 32),
        ("bound_runtime_session", None),
        ("bound_runtime_prompt", None),
        ("runtime_disposition", None),
        ("runtime_current_prompt", "Synthetic retained precise input"),
        ("runtime_current_result", "Synthetic retained precise output"),
        ("runtime_current_calls", [{"synthetic": "retained call"}]),
        ("runtime_current_error", "Synthetic retained diagnostic"),
        ("runtime_current_system", "Synthetic refilled composed prompt"),
        ("runtime_current_provenance", {"synthetic": "changed"}),
        ("runtime_reduced_system", None),
        ("runtime_reduced_provenance", None),
    ):
        conn.rows = [complete, runtime_copy | {field: altered}]
        with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_cohort_pending"):
            await require_ingress_closed(conn, request, "canonical-key", stored)
    conn.rows = [complete, runtime_copy]
    await require_ingress_closed(conn, request, "canonical-key", stored)

    conn.structured_pending = True
    with pytest.raises(CopyFloorUnavailable, match="ingress_structured_cohort_pending"):
        await require_ingress_closed(conn, request, "canonical-key", stored)
    conn.structured_pending = False
    await require_ingress_closed(conn, request, "canonical-key", stored)

    # No fake pool is claimed as a native constructor. Exercise the actual
    # observer method with a source Task whose cancellation unwind is held.
    runtime = object.__new__(SwitchboardInputCopies)
    runtime._ended_headers, runtime._ended_inputs, runtime._settlers = set(), set(), set()
    runtime.reconcile_observed_ends = AsyncMock()
    entered, unwind, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def owner():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            unwind.set()
            await release.wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    binding = _Header(uuid4(), task)
    runtime._observe(binding, header=True)
    task.cancel()
    await unwind.wait()
    assert runtime._ended_headers == set() and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert runtime._ended_headers == {id(binding)}
    await asyncio.gather(*runtime._settlers)
    assert runtime.reconcile_observed_ends.await_count == 1

    trace = []

    class Source:
        failed = False

        async def reserve_header(self):
            trace.append("birth-readback")
            if self.failed:
                raise CopyFloorUnavailable("unavailable")
            return binding

    source = Source()

    async def receive():
        trace.append("body")
        return {"type": "http.request", "body": b"synthetic", "more_body": False}

    async def send(message):
        trace.append(message["type"])

    async def sdk(scope, receive, send):
        cell = scope["butlers.location.ingress_input"]
        assert cell.runtime is source and cell.header is binding
        assert trace == ["birth-readback"]
        assert (await receive())["body"] == b"synthetic"

    app = IngressServerLifetime(sdk, source)
    await app({"type": "http", "method": "POST", "path": "/mcp"}, receive, send)
    assert trace == ["birth-readback", "body"]
    trace.clear()
    source.failed = True
    await app({"type": "http", "method": "POST", "path": "/mcp"}, receive, send)
    assert trace == ["birth-readback", "http.response.start", "http.response.body"]

    # Actual ASGI failure keeps its own body/closure in the original traceback.
    # A healthy sibling completes; the failed scope cannot be settled by done.
    runtime._headers, runtime._unresolved_headers = {}, set()
    actual_headers = []

    async def captured_header():
        captured = _Header(uuid4(), asyncio.current_task())
        runtime._headers[id(captured)] = captured
        actual_headers.append(captured)
        runtime._observe(captured, header=True)
        return captured

    runtime.reserve_header = captured_header
    server_primary = RuntimeError("synthetic server failure")

    async def server(scope, receive, send):
        independent_copy = {"body": (await receive())["body"]}
        assert independent_copy["body"] == b"synthetic"
        if scope.get("synthetic_failure"):
            raise server_primary

    for failed_scope in (False, True):
        native_server = IngressServerLifetime(server, runtime)
        actual_server = asyncio.create_task(
            native_server(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "synthetic_failure": failed_scope,
                },
                receive,
                send,
            )
        )
        if failed_scope:
            with pytest.raises(RuntimeError) as caught:
                await actual_server
            assert caught.value is server_primary
        else:
            await actual_server
        await asyncio.sleep(0)
        captured = actual_headers[-1]
        assert captured.task is actual_server and actual_server.done()
        assert (id(captured) in runtime._ended_headers) is not failed_scope
        assert (id(captured) in runtime._unresolved_headers) is failed_scope
    tb, retained_server_copy = server_primary.__traceback__, False
    while tb:
        copied = tb.tb_frame.f_locals.get("independent_copy")
        if isinstance(copied, dict) and copied.get("body") == b"synthetic":
            retained_server_copy = True
        tb = tb.tb_next
    assert retained_server_copy

    # Queue before final SDK delivery; actual HTTP Task may end before the
    # receiving SDK Task starts. No POST/202 end stands in for that holder.
    from unittest.mock import patch

    from fastmcp import FastMCP
    from starlette.requests import Request

    from butlers.core.location_ingress_copies import (
        _Input,
        _RequestInput,
        install_ingress_middleware,
    )

    envelope = {
        "schema_version": "ingest.v1",
        "source": {"provider": "owntracks"},
        "event": {},
        "sender": {},
        "payload": {"raw": {"synthetic": "location"}},
    }
    wire = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ingest", "arguments": envelope},
        }
    ).encode()
    pending = object.__new__(SwitchboardInputCopies)
    pending._inputs, pending._ended_inputs, pending._settlers = {}, set(), set()
    pending._cleared_inputs = set()
    pending.reconcile_observed_ends = AsyncMock()
    from contextlib import asynccontextmanager

    pending.incarnation = uuid4()
    claim_trace = []

    class Claims:
        row = None

        async def execute(self, sql, generation, handler, incarnation):
            assert sql.startswith("INSERT INTO location_ingress_input_claims ")
            self.row = {"handler_generation": handler, "incarnation": incarnation}
            claim_trace.append("claim-commit")

        async def fetchrow(self, sql, generation):
            assert "FROM location_ingress_input_claims " in sql
            claim_trace.append("claim-readback")
            if getattr(self, "unavailable", False):
                return None
            return self.row

    claims = Claims()

    @asynccontextmanager
    async def writer():
        yield claims

    @asynccontextmanager
    async def acquired():
        yield claims

    pending.writer = writer
    pending.pool = type("Readback", (), {"acquire": staticmethod(acquired)})()
    cells, events = [], []

    async def birth_header():
        return _Header(uuid4(), asyncio.current_task())

    async def queued(header, actual):
        assert actual == envelope
        captured = _Input(uuid4(), header.generation, b"d" * 32, content_digest(actual), None)
        pending._inputs[id(captured)] = captured
        events.append("queue-commit-readback")
        return captured

    pending.reserve_header, pending.reserve_queued_input = birth_header, queued

    async def queue_sdk(scope, receive, send):
        assert events == []
        cells.append(scope["butlers.location.ingress_input"])
        assert await receive() == {"type": "http.request", "body": wire, "more_body": False}
        assert events == ["queue-commit-readback"]
        await send({"type": "http.response.start", "status": 202})
        events.append("sdk-queued")

    async def body():
        return {"type": "http.request", "body": wire, "more_body": False}

    async def sent(message):
        assert message["status"] == 202

    http = asyncio.create_task(
        IngressServerLifetime(queue_sdk, pending)(
            {"type": "http", "method": "POST", "path": "/messages/"}, body, sent
        )
    )
    await http
    cell = cells[0]
    assert cell.header.task.done()
    assert cell.queued.task is None and pending._ended_inputs == set()
    assert events == ["queue-commit-readback", "sdk-queued"]
    assert isinstance(cell, _RequestInput)

    sdk = FastMCP("synthetic ingress SDK claim")
    install_ingress_middleware(sdk, pending)
    received = []

    @sdk.tool
    async def ingest(
        schema_version: str,
        source: dict,
        event: dict,
        sender: dict,
        payload: dict,
        control: dict | None = None,
    ):
        received.append(source["provider"])
        if source["provider"] == "owntracks":
            assert cell.queued.task is asyncio.current_task()
            assert claim_trace == ["claim-commit", "claim-readback"]
        return {"accepted": True}

    sdk_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/messages/",
            "headers": [],
            "butlers.location.ingress_input": cell,
        }
    )

    async def invoke():
        with patch("fastmcp.server.dependencies.get_http_request", return_value=sdk_request):
            await sdk.call_tool("ingest", envelope)

    handler = asyncio.create_task(invoke())
    await handler
    await asyncio.sleep(0)
    assert cell.queued.task is handler and id(cell.queued) in pending._ended_inputs
    await asyncio.gather(*pending._settlers)
    assert received == ["owntracks"]
    with patch("fastmcp.server.dependencies.get_http_request", return_value=sdk_request):
        await sdk.call_tool("ingest", envelope | {"source": {"provider": "ordinary"}})
    assert received == ["owntracks", "ordinary"]

    altered = _Input(uuid4(), cell.header.generation, b"d" * 32, content_digest(envelope), None)
    pending._inputs[id(altered)] = altered
    changed_cell = _RequestInput(pending, cell.header, altered)
    changed_request = Request({**sdk_request.scope, "butlers.location.ingress_input": changed_cell})
    changed = envelope | {"payload": {"raw": {"synthetic": "changed"}}}
    with patch("fastmcp.server.dependencies.get_http_request", return_value=changed_request):
        with pytest.raises(CopyFloorUnavailable, match="ingress_queued_input_differs"):
            await sdk.call_tool("ingest", changed)
    assert altered.task is None
    assert received == ["owntracks", "ordinary"]

    # A valid native argument alone never supplies a birth or a claim.
    missing = _RequestInput(pending, cell.header)
    missing_request = Request({**sdk_request.scope, "butlers.location.ingress_input": missing})
    with patch("fastmcp.server.dependencies.get_http_request", return_value=missing_request):
        with pytest.raises(CopyFloorUnavailable, match="ingress_queued_input_differs"):
            await sdk.call_tool("ingest", envelope)
    assert received == ["owntracks", "ordinary"]

    # The real SDK can catch a failed tool outside our middleware and return
    # normally. That Task(None) must not certify its retained input/error copy.
    failing_sdk = FastMCP("synthetic ingress error holder")
    install_ingress_middleware(failing_sdk, pending)
    retained_sdk_errors = []
    primary_sdk_error = RuntimeError("synthetic SDK primary")

    @failing_sdk.tool(name="ingest")
    async def failed_ingest(
        schema_version: str, source: dict, event: dict, sender: dict, payload: dict
    ):
        independent_copy = dict(payload)
        assert independent_copy["raw"]["synthetic"] == "location"
        raise primary_sdk_error

    failed_input = _Input(
        uuid4(), cell.header.generation, b"f" * 32, content_digest(envelope), None
    )
    pending._inputs[id(failed_input)] = failed_input
    failed_cell = _RequestInput(pending, cell.header, failed_input)
    failed_request = Request({**sdk_request.scope, "butlers.location.ingress_input": failed_cell})

    async def failed_handler():
        from fastmcp.exceptions import ToolError

        with patch("fastmcp.server.dependencies.get_http_request", return_value=failed_request):
            try:
                await failing_sdk.call_tool("ingest", envelope)
            except ToolError as error:
                retained_sdk_errors.append(error)

    failed_task = asyncio.create_task(failed_handler())
    await failed_task
    await asyncio.sleep(0)
    assert failed_task.result() is None and failed_input.task is failed_task
    assert id(failed_input) not in pending._ended_inputs
    assert failed_input.unresolved_failure and retained_sdk_errors
    tb, retained_sdk_copy = primary_sdk_error.__traceback__, False
    while tb:
        copied = tb.tb_frame.f_locals.get("independent_copy")
        if isinstance(copied, dict) and copied.get("raw", {}).get("synthetic") == "location":
            retained_sdk_copy = True
        tb = tb.tb_next
    assert retained_sdk_copy

    # Cancellation caught outside the SDK has the same unresolved-copy rule,
    # without changing the actual cancellation's identity or ordinary behavior.
    cancelled_sdk = FastMCP("synthetic cancelled ingress holder")
    install_ingress_middleware(cancelled_sdk, pending)
    primary_cancel = asyncio.CancelledError()
    caught_cancellations = []

    @cancelled_sdk.tool(name="ingest")
    async def cancelled_ingest(
        schema_version: str, source: dict, event: dict, sender: dict, payload: dict
    ):
        raise primary_cancel

    cancelled_input = _Input(
        uuid4(), cell.header.generation, b"c" * 32, content_digest(envelope), None
    )
    pending._inputs[id(cancelled_input)] = cancelled_input
    cancelled_cell = _RequestInput(pending, cell.header, cancelled_input)
    cancelled_request = Request(
        {**sdk_request.scope, "butlers.location.ingress_input": cancelled_cell}
    )

    async def caught_cancel_handler():
        with patch("fastmcp.server.dependencies.get_http_request", return_value=cancelled_request):
            try:
                await cancelled_sdk.call_tool("ingest", envelope)
            except asyncio.CancelledError as error:
                caught_cancellations.append(error)

    cancelled_task = asyncio.create_task(caught_cancel_handler())
    await cancelled_task
    await asyncio.sleep(0)
    assert cancelled_task.result() is None and cancelled_input.task is cancelled_task
    assert caught_cancellations == [primary_cancel]
    assert id(cancelled_input) not in pending._ended_inputs
    assert cancelled_input.unresolved_failure

    # Claim readback failure happens before FunctionTool invocation but can
    # also be caught by the SDK. Hold that same actual input, not a remint.
    unknown_input = _Input(
        uuid4(), cell.header.generation, b"u" * 32, content_digest(envelope), None
    )
    pending._inputs[id(unknown_input)] = unknown_input
    unknown_cell = _RequestInput(pending, cell.header, unknown_input)
    unknown_request = Request({**sdk_request.scope, "butlers.location.ingress_input": unknown_cell})
    caught_unknowns = []

    async def caught_unknown_handler():
        with patch("fastmcp.server.dependencies.get_http_request", return_value=unknown_request):
            try:
                await sdk.call_tool("ingest", envelope)
            except CopyFloorUnavailable as error:
                caught_unknowns.append(error)

    claims.unavailable = True
    try:
        unknown_task = asyncio.create_task(caught_unknown_handler())
        await unknown_task
        await asyncio.sleep(0)
    finally:
        claims.unavailable = False
    assert unknown_task.result() is None and unknown_input.task is unknown_task
    assert id(unknown_input) not in pending._ended_inputs
    assert unknown_input.unresolved_failure and len(caught_unknowns) == 1
    assert received == ["owntracks", "ordinary"]

    retry = _Input(
        uuid4(),
        cell.header.generation,
        b"r" * 32,
        content_digest(envelope),
        asyncio.current_task(),
        handler=uuid4(),
    )
    pending._inputs[id(retry)] = retry
    retry_cell = _RequestInput(pending, cell.header, retry)
    claims.unavailable = True
    with pytest.raises(CopyFloorUnavailable, match="ingress_handler_commit_unknown"):
        await pending.claim_queued_input(retry_cell, envelope)
    claims.unavailable = False
    assert await pending.claim_queued_input(retry_cell, envelope) is retry
    assert retry.handler == claims.row["handler_generation"]

    from butlers.core.location_ingress_copies import spawn_ingest_processing

    business = []
    copied = {"message_text": "synthetic location input"}
    parent = _Input(
        uuid4(), cell.header.generation, b"p" * 32, content_digest(envelope), asyncio.current_task()
    )

    async def reserve_child(actual_parent, body):
        assert actual_parent is parent
        child_input = _Input(
            uuid4(),
            actual_parent.server,
            actual_parent.dedupe_digest,
            content_digest(body),
            None,
            kind=3,
        )
        pending._inputs[id(child_input)] = child_input
        claim_trace.append("child-commit-readback")
        return child_input

    pending.reserve_child = reserve_child

    async def process():
        assert claim_trace == ["child-commit-readback", "claim-commit", "claim-readback"]
        business.append(copied["message_text"])
        return "processed"

    claim_trace.clear()
    child_task = await spawn_ingest_processing((pending, parent), copied, process)
    assert claim_trace == ["child-commit-readback"] and business == []
    assert await child_task == "processed"
    assert business == ["synthetic location input"]
    await asyncio.sleep(0)
    await asyncio.gather(*pending._settlers)
    claim_trace.clear()
    business.clear()
    tampered = await spawn_ingest_processing((pending, parent), copied, process)
    copied["message_text"] = "changed after reservation"
    with pytest.raises(CopyFloorUnavailable, match="ingress_queued_input_differs"):
        await tampered
    assert business == [] and claim_trace == ["child-commit-readback"]
    # Explicit unconfigured generic fallback remains a real ordinary Task,
    # with no source birth, private claim or retention authority.
    generic = await spawn_ingest_processing(None, {}, lambda: asyncio.sleep(0, result="ordinary"))
    assert await generic == "ordinary"

    # The actual queued object is cleared only after its separate processing
    # Task ends. Secondary witness failure cannot replace its primary error.
    from datetime import UTC, datetime

    from butlers.core.buffer import _MessageRef
    from butlers.core.location_ingress_copies import _buffer_body, process_buffer_input

    for outcome, metadata_fails in (
        ("success", False),
        ("success", True),
        ("error", False),
        ("error", True),
        ("cancel", False),
        ("cancel", True),
    ):
        ref = _MessageRef(
            request_id="synthetic-buffer-id",
            message_inbox_id=uuid4(),
            message_text="synthetic buffer location",
            source={"provider": "owntracks"},
            event={"id": "synthetic-event"},
            sender={"id": "synthetic-sender"},
            enqueued_at=datetime.now(UTC),
            attachments=[{"name": "synthetic"}],
            payload_type="text",
            triage_decision="route",
            triage_target="chronicler",
        )
        body = _buffer_body(ref)
        queued = _Input(
            uuid4(), cell.header.generation, b"q" * 32, content_digest(body), None, kind=2
        )
        pending._inputs[id(queued)] = queued
        parent = queued
        ref._native_ingress = (pending, queued, body)
        pending.reconcile_observed_ends = AsyncMock(
            side_effect=RuntimeError("synthetic witness") if metadata_fails else None,
            return_value=None,
        )
        entered, release = asyncio.Event(), asyncio.Event()
        primary = RuntimeError("synthetic primary")

        async def handle(actual):
            assert actual is ref and actual.message_text == "synthetic buffer location"
            assert id(queued) not in pending._ended_inputs
            entered.set()
            await release.wait()
            if outcome == "error":
                raise primary

        processing = asyncio.create_task(process_buffer_input(ref, handle))
        await entered.wait()
        assert ref.message_text == "synthetic buffer location"
        assert id(queued) not in pending._cleared_inputs
        if outcome == "cancel":
            processing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await processing
        else:
            release.set()
            if outcome == "error":
                with pytest.raises(RuntimeError) as failure:
                    await processing
                assert failure.value is primary
            elif metadata_fails:
                with pytest.raises(RuntimeError, match="synthetic witness"):
                    await processing
            else:
                await processing
        assert ref.message_text == "" and ref.source == {} and ref.sender == {}
        assert ref.event == {} and ref.attachments is None and ref.payload_type is None
        assert ref.triage_decision is None and ref.triage_target is None
        assert ref._native_ingress is None and body == {}
        assert id(queued) in pending._cleared_inputs and id(queued) in pending._ended_inputs
        # Even a finished worker Task cannot settle an untouched queued
        # object. The actual reconciler rejects without entering its writer.
        if outcome == "success" and not metadata_fails:
            saved_ends = pending._ended_inputs
            pending._ended_inputs = {id(queued)}
            pending._cleared_inputs.remove(id(queued))
            queued.task = processing
            pending._reconcile_lock = asyncio.Lock()
            pending._headers, pending._ended_headers = {}, set()
            assert processing.done()
            with pytest.raises(CopyFloorUnavailable, match="ingress_observed_lifetime_differs"):
                await SwitchboardInputCopies.reconcile_observed_ends(pending)
            pending._cleared_inputs.add(id(queued))
            pending._ended_inputs = saved_ends
        await asyncio.sleep(0)
        settlements = await asyncio.gather(*pending._settlers, return_exceptions=True)
        assert (
            all(isinstance(value, RuntimeError) for value in settlements)
            if metadata_fails
            else all(value is None for value in settlements)
        )

    # Actual buffer enqueue rejects and stop removes its own untouched objects;
    # neither a generic absence nor worker completion invents their disposal.
    from butlers.config import BufferConfig
    from butlers.core.buffer import DurableBuffer
    from butlers.core.location_ingress_copies import discard_buffer_input

    await asyncio.sleep(0)
    while pending._settlers:
        older = await asyncio.gather(*tuple(pending._settlers), return_exceptions=True)
        assert all(value is None or isinstance(value, RuntimeError) for value in older)
        await asyncio.sleep(0)
    pending.reconcile_observed_ends = AsyncMock(return_value=None)
    rejected_queue = DurableBuffer(BufferConfig(queue_capacity=1), None, AsyncMock())

    def queued_ref():
        original = _MessageRef(
            request_id="synthetic-discard",
            message_inbox_id=uuid4(),
            message_text="synthetic retained queue payload",
            source={"provider": "owntracks"},
            event={"id": "synthetic-event"},
            sender={"id": "synthetic-sender"},
            enqueued_at=datetime.now(UTC),
        )
        frozen = _buffer_body(original)
        allocation = _Input(
            uuid4(), cell.header.generation, b"q" * 32, content_digest(frozen), None, kind=2
        )
        pending._inputs[id(allocation)] = allocation
        original._native_ingress = (pending, allocation, frozen)
        return original, allocation, frozen

    offered, first, first_body = queued_ref()
    assert rejected_queue.enqueue(
        **_buffer_body(offered),
        message_inbox_id=offered.message_inbox_id,
        _native_ingress=offered._native_ingress,
    )
    actual_first = rejected_queue._tier_queues["default"]._queue[0]
    offered, refused, refused_body = queued_ref()
    assert not rejected_queue.enqueue(
        **_buffer_body(offered),
        message_inbox_id=offered.message_inbox_id,
        _native_ingress=offered._native_ingress,
    )
    assert actual_first.message_text == "synthetic retained queue payload"
    assert id(first) not in pending._cleared_inputs
    assert id(refused) in pending._cleared_inputs and refused_body == {}
    await asyncio.gather(*pending._settlers)
    assert refused.handler is not None and id(refused) in pending._ended_inputs
    rejected_queue._running = True
    await rejected_queue.stop(drain_timeout_s=0)
    await asyncio.gather(*pending._settlers)
    assert rejected_queue.queue_depth == 0
    assert actual_first.message_text == "" and actual_first._native_ingress is None
    assert id(first) in pending._ended_inputs and first_body == {}
    assert first.handler is not None

    # A claimed processing object or changed body is retained unchanged.
    offered, claimed, frozen = queued_ref()
    claimed.task = asyncio.current_task()
    with pytest.raises(CopyFloorUnavailable, match="ingress_discard_claim_already_present"):
        discard_buffer_input(offered)
    assert offered.message_text == "synthetic retained queue payload" and frozen
    offered, changed, frozen = queued_ref()
    offered.message_text = "synthetic changed queue payload"
    with pytest.raises(CopyFloorUnavailable, match="ingress_buffer_copy_differs"):
        discard_buffer_input(offered)
    assert offered.message_text == "synthetic changed queue payload" and frozen
    assert changed.task is None and id(changed) not in pending._cleared_inputs
    await _assert_native_cold_ingress_values()
    await _assert_native_ingress_completed_task_holders()


async def _assert_native_cold_ingress_values():
    """Actual scanner scope and current captured-parent engine; software only."""
    import asyncio
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from butlers.config import BufferConfig
    from butlers.core.buffer import DurableBuffer, _MessageRef
    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import (
        SwitchboardInputCopies,
        _Header,
        _scanner_scope,
        _writers,
        reserve_scanned_buffer_input,
    )

    request_id, original_generation = uuid4(), uuid4()
    canonical = {
        "request_context": {"request_id": str(request_id), "dedupe_key": "synthetic cold key"},
        "raw_payload": {
            "source": {"provider": "owntracks", "channel": "owntracks", "endpoint_identity": "e"},
            "sender": {"identity": "s"},
            "event": {},
        },
        "normalized_text": "synthetic frozen input",
    }
    original = {
        "copy_generation": original_generation,
        "dedupe_digest": logical_digest("synthetic cold key"),
        "stored_digest": content_digest(
            {
                "raw_payload": canonical["raw_payload"],
                "normalized_text": canonical["normalized_text"],
            }
        ),
        "copy_kind": 1,
    }

    class Connection:
        parent = original
        forgotten = False
        admission = None
        committed = True
        trace = []
        births = {}
        parent_generation = None
        accepted = None

        async def fetchrow(self, sql, *args):
            if "FROM message_inbox " in sql:
                assert "FOR UPDATE" in sql and args == (request_id,)
                self.trace.append("locked canonical read")
                return canonical
            if "FROM location_ingress_inbox_sources " in sql:
                assert "LEFT JOIN location_ingress_input_births" in sql
                return self.parent
            if "FROM public.ingestion_events " in sql:
                return self.admission
            assert "FROM location_ingress_input_births " in sql
            assert "LEFT JOIN location_ingress_input_parents" in sql
            assert "LEFT JOIN location_ingress_accepted_inputs" in sql
            self.trace.append("independent committed readback")
            if not self.committed:
                return None
            birth = self.births[args[0]]
            return {
                "server_generation": birth[1],
                "dedupe_digest": birth[2],
                "envelope_digest": birth[3],
                "copy_kind": 2,
                "parent_generation": self.parent_generation,
                "request_id": self.accepted[1],
                "stored_digest": self.accepted[2],
            }

        async def fetchval(self, sql, *args):
            assert "location_retention_source_floors" in sql
            return self.forgotten

        async def execute(self, sql, *args):
            self.trace.append("committing lineage")
            if "INSERT INTO location_ingress_input_births" in sql:
                self.births[args[0]] = args
            elif "INSERT INTO location_ingress_input_parents" in sql:
                self.parent_generation = args[1]
            else:
                assert "INSERT INTO location_ingress_accepted_inputs" in sql
                self.accepted = args

    conn = Connection()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()
    runtime = object.__new__(SwitchboardInputCopies)
    runtime.pool, runtime.active = pool, True
    runtime._inputs, runtime._headers = {}, {}

    @asynccontextmanager
    async def writer():
        yield conn

    runtime.writer = writer

    def make_ref():
        return _MessageRef(
            request_id=str(request_id),
            message_inbox_id=request_id,
            message_text=canonical["normalized_text"],
            source=canonical["raw_payload"]["source"],
            event={},
            sender=canonical["raw_payload"]["sender"],
            enqueued_at=datetime.now(UTC),
        )

    async def reserve_header():
        header = _Header(uuid4(), asyncio.current_task())
        runtime._headers[id(header)] = header
        conn.trace.append("header commit readback before query")
        return header

    runtime.reserve_header = reserve_header
    previous = _writers.get(pool)
    _writers[pool] = runtime
    try:
        ref = make_ref()
        with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_scope_unknown"):
            await reserve_scanned_buffer_input(pool, ref)
        assert ref._native_ingress is None

        buffer = DurableBuffer(BufferConfig(), pool, AsyncMock())
        caller = asyncio.current_task()

        async def scan():
            assert asyncio.current_task() is not caller
            assert conn.trace == ["header commit readback before query"]
            assert _scanner_scope.get()[0] is runtime
            await reserve_scanned_buffer_input(pool, ref)
            return 1

        buffer._scan_canonical_rows = scan
        assert await buffer._run_scanner_sweep() == 1
        assert _scanner_scope.get() is None
        assert ref._native_ingress[0] is runtime
        assert ref._native_ingress[1].kind == 2
        assert conn.parent_generation == original_generation
        assert conn.trace.index("locked canonical read") > 0
        assert conn.trace[-1] == "independent committed readback"

        # No smaller/guessed original, refilled canonical body or policy floor.
        header = await reserve_header()
        body = ref._native_ingress[2]
        for parent in (
            None,
            {**original, "copy_kind": None},
            {**original, "stored_digest": b"changed"},
            {**original, "dedupe_digest": b"changed"},
        ):
            conn.parent = parent
            with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_original_unknown"):
                await runtime.reserve_recovered_queue(header, request_id, body)
        conn.parent = original
        conn.forgotten = True
        with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_original_unknown"):
            await runtime.reserve_recovered_queue(header, request_id, body)
        conn.forgotten = False
        with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_body_changed"):
            await runtime.reserve_recovered_queue(
                header, request_id, {**body, "message_text": "new"}
            )
        conn.committed = False
        with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_commit_unknown"):
            await runtime.reserve_recovered_queue(header, request_id, body)
        conn.committed = True
        assert (await runtime.reserve_recovered_queue(header, request_id, body)).kind == 2

        # Ordinary compatibility requires the exact original shared admission,
        # not an arbitrary current non-location label or absent private stamp.
        canonical["raw_payload"]["source"] = {
            "provider": "telegram",
            "channel": "telegram_bot",
            "endpoint_identity": "ordinary",
        }
        ordinary = make_ref()
        from butlers.core.location_ingress_copies import _buffer_body

        conn.parent = None
        ordinary_body = _buffer_body(ordinary)
        admission = {
            "source_provider": "telegram",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "ordinary",
            "source_sender_identity": "s",
            "dedupe_key": "synthetic cold key",
        }
        for bad in (
            None,
            {**admission, "source_provider": "owntracks"},
            {**admission, "dedupe_key": "another admission"},
        ):
            conn.admission = bad
            with pytest.raises(
                CopyFloorUnavailable, match="ingress_scanner_ordinary_admission_unknown"
            ):
                await runtime.reserve_recovered_queue(header, request_id, ordinary_body)
        conn.admission = admission
        assert await runtime.reserve_recovered_queue(header, request_id, ordinary_body) is None
        canonical["raw_payload"]["source"]["provider"] = "owntracks"
        with pytest.raises(CopyFloorUnavailable, match="ingress_scanner_original_unknown"):
            await runtime.reserve_recovered_queue(header, request_id, _buffer_body(make_ref()))
    finally:
        if previous is None:
            _writers.pop(pool)
        else:
            _writers[pool] = previous


async def _assert_native_ingress_completed_task_holders():
    """Actual Task observer/reconciler; modeled metadata I/O, never SQL proof."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    from butlers.core.buffer import _MessageRef
    from butlers.core.location_ingress_copies import (
        SwitchboardInputCopies,
        _buffer_body,
        _Input,
        process_buffer_input,
    )

    marker = "synthetic independent processing copy"

    class Connection:
        def __init__(self, runtime):
            self.runtime, self.ends = runtime, set()
            self.at_insert = []

        async def execute(self, sql, generation, *args):
            if "INSERT INTO location_ingress_input_claims" in sql:
                return
            assert "INSERT INTO location_ingress_input_ends" in sql
            binding = next(b for b in self.runtime._inputs.values() if b.generation == generation)
            retained = False
            if binding.task.done() and not binding.task.cancelled():
                error = binding.task.exception()
                tb = error.__traceback__ if error else None
                while tb:
                    local = tb.tb_frame.f_locals.get("independent_copy")
                    if isinstance(local, dict) and local.get("marker") == marker:
                        retained = True
                    tb = tb.tb_next
            self.at_insert.append((binding.kind, retained))
            self.ends.add(generation)

        async def fetchrow(self, sql, generation):
            binding = next(b for b in self.runtime._inputs.values() if b.generation == generation)
            if "location_ingress_input_claims" in sql:
                return {
                    "handler_generation": binding.handler,
                    "incarnation": self.runtime.incarnation,
                }
            assert "location_ingress_input_births" in sql
            return {
                "server_generation": binding.server,
                "dedupe_digest": binding.dedupe_digest,
                "envelope_digest": binding.envelope_digest,
                "copy_kind": binding.kind,
            }

        async def fetchval(self, sql, generation):
            assert "location_ingress_input_ends" in sql
            return generation in self.ends

    class Runtime(SwitchboardInputCopies):
        def __init__(self):
            self.incarnation = uuid4()
            self._inputs, self._headers = {}, {}
            self._ended_inputs, self._ended_headers = set(), set()
            self._cleared_inputs, self._settlers = set(), set()
            self._reconcile_lock = asyncio.Lock()
            self.conn = Connection(self)
            self.pool = self

        @asynccontextmanager
        async def writer(self):
            yield self.conn

        @asynccontextmanager
        async def acquire(self):
            yield self.conn

        async def reserve_child(self, parent, body):
            assert self._inputs.get(id(parent)) is parent and parent.kind == 2
            child = _Input(
                uuid4(), parent.server, parent.dedupe_digest, content_digest(body), None, kind=3
            )
            self._inputs[id(child)] = child
            self.processing = child
            return child

    for outcome in (
        "healthy",
        "error",
        "result",
        "cancel",
        "configured_healthy",
        "configured_error",
        "configured_result",
        "configured_reconcile_error",
    ):
        runtime = Runtime()
        ref = _MessageRef(
            request_id="synthetic holder",
            message_inbox_id=uuid4(),
            message_text="synthetic source",
            source={"provider": "owntracks", "marker": marker, "endpoint_identity": marker},
            event={"external_event_id": marker},
            sender={},
            enqueued_at=datetime.now(UTC),
        )
        body = _buffer_body(ref)
        queued = _Input(uuid4(), uuid4(), b"q" * 32, content_digest(body), None, kind=2)
        runtime._inputs[id(queued)] = queued
        ref._native_ingress = (runtime, queued, body)
        primary = RuntimeError("synthetic primary")

        from butlers.core import fact_authority
        from butlers.switchboard_wiring import wire_pipelines

        retained_errors = []

        async def pipeline_process(**kwargs):
            independent_copy = {"marker": marker, "message": kwargs["message_text"]}
            assert independent_copy["message"] == "synthetic source"
            if outcome == "configured_error":
                try:
                    raise primary
                except RuntimeError as error:
                    retained_errors.append(error)
                    raise
            return SimpleNamespace(
                classification_error=marker if outcome == "configured_result" else None,
                routing_error=None,
                failed_targets=[],
            )

        daemon = SimpleNamespace(
            config=SimpleNamespace(name="switchboard", buffer=None),
            spawner=SimpleNamespace(trigger=AsyncMock()),
            _active_modules=[],
            _credential_store=None,
            mcp=None,
        )
        previous_registry = fact_authority.source_registry()
        with (
            patch(
                "butlers.modules.pipeline.MessagePipeline",
                return_value=SimpleNamespace(process=pipeline_process),
            ),
            patch(
                "butlers.core.buffer.DurableBuffer",
                side_effect=lambda **kwargs: SimpleNamespace(_process_fn=kwargs["process_fn"]),
            ),
        ):
            try:
                wire_pipelines(daemon, runtime)
            finally:
                fact_authority._source_registry = previous_registry

        async def invoke(actual):
            if outcome.startswith("configured_"):
                with (
                    patch(
                        "butlers.core.ingestion_events.ingestion_event_reconcile_after_processing",
                        new=AsyncMock(
                            side_effect=primary if outcome == "configured_reconcile_error" else None
                        ),
                    ),
                    patch("butlers.switchboard_wiring.logger.warning") as warning,
                ):
                    result = await daemon._buffer._process_fn(actual)
                    assert result is None
                    assert all("exc_info" not in call.kwargs for call in warning.call_args_list)
                return
            independent_copy = dict(actual.source)
            assert independent_copy["marker"] == marker
            if outcome == "error":
                raise primary
            if outcome == "cancel":
                raise asyncio.CancelledError()
            if outcome == "result":
                return independent_copy

        if outcome in ("error", "configured_reconcile_error"):
            with pytest.raises(RuntimeError) as caught:
                await process_buffer_input(ref, invoke)
            assert caught.value is primary
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await process_buffer_input(ref, invoke)
        else:
            await process_buffer_input(ref, invoke)
        await asyncio.sleep(0)
        while runtime._settlers:
            await asyncio.gather(*tuple(runtime._settlers))
            await asyncio.sleep(0)
        assert ref.message_text == "" and ref.source == {} and body == {}
        assert queued.generation in runtime.conn.ends
        processing = runtime.processing
        assert processing.task.done()
        if outcome in ("healthy", "configured_healthy"):
            assert processing.generation in runtime.conn.ends
            assert {kind for kind, _ in runtime.conn.at_insert} == {2, 3}
        else:
            assert processing.generation not in runtime.conn.ends
            assert {kind for kind, _ in runtime.conn.at_insert} == {2}
            assert id(processing) not in runtime._ended_inputs
        assert not any(retained for _, retained in runtime.conn.at_insert)
        if outcome == "error":
            tb, retained = primary.__traceback__, False
            while tb:
                copied = tb.tb_frame.f_locals.get("independent_copy")
                if isinstance(copied, dict) and copied.get("marker") == marker:
                    retained = True
                tb = tb.tb_next
            assert retained  # The primary traceback survives; its copy remains held.
        if outcome == "result":
            assert processing.task.result()["marker"] == marker
        if outcome in ("configured_error", "configured_result"):
            assert processing.task.result() is None
            assert processing.unresolved_failure
        if outcome == "configured_error":
            assert retained_errors == [primary]
            tb, retained = primary.__traceback__, False
            while tb:
                copied = tb.tb_frame.f_locals.get("independent_copy")
                if isinstance(copied, dict) and copied.get("marker") == marker:
                    retained = True
                tb = tb.tb_next
            assert retained
        if outcome == "configured_reconcile_error":
            assert processing.task.exception() is primary
            tb, retained = primary.__traceback__, False
            while tb:
                copied = tb.tb_frame.f_locals.get("_buf_tool_args")
                if isinstance(copied, dict) and copied.get("source_identity") == marker:
                    retained = True
                tb = tb.tb_next
            assert retained

    # Execute the actual native replay reader with an empty owning source
    # query. Its content-free birth is separate from the webhook allocation;
    # an empty replay query does not make that real lifetime disappear.
    from butlers.connectors.owntracks import OwnTracksConnector
    from butlers.connectors.owntracks_input_copies import OwnTracksInputCopies

    native_inputs = object.__new__(OwnTracksInputCopies)
    native_inputs.incarnation = uuid4()
    native_inputs._closing = False
    native_inputs._servers, native_inputs._bindings = {}, {}
    original_webhook = native_inputs.allocate_server()
    committed_headers, ended_headers = [], []

    async def commit_header(binding):
        assert native_inputs._servers[id(binding)] is binding
        committed_headers.append(binding)

    class EmptyReplay:
        async def fetch(self, sql, endpoint):
            assert "FROM connectors.filtered_events" in sql
            assert "status='replay_pending'" in sql and endpoint == "synthetic native endpoint"
            return []

        @asynccontextmanager
        async def acquire(self):
            yield self

    native_inputs.pool = EmptyReplay()
    native_inputs.commit_server = commit_header
    native_connector = SimpleNamespace(
        _input_copies=native_inputs,
        _observe_native_server_end=ended_headers.append,
    )
    reader = asyncio.create_task(
        OwnTracksConnector._drain_native_replay(
            native_connector,
            "synthetic native endpoint",
        )
    )
    await reader
    await asyncio.sleep(0)
    assert len(committed_headers) == 1 and len(native_inputs._servers) == 2
    assert committed_headers[0].generation != original_webhook.generation
    assert ended_headers == committed_headers


async def _assert_native_no_dispatch_source_copy():
    """Actual own reducer; source/DB metadata doubles are not SQL evidence."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from uuid import uuid4

    from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest
    from butlers.core import location_copy_retention as copies
    from butlers.core import location_ingress_copies as ingress

    decision = uuid4()
    request = uuid4()
    key = "owntracks:synthetic-terminal:1:location"
    raw = {"lat": 1.314159, "lon": 103.812345}
    frozen = FrozenRaw(
        raw_id=uuid4(),
        source_revision=1,
        logical_source_digest=logical_digest(key).hex(),
        content_digest=content_digest(raw).hex(),
        retention_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_request_id=request,
        accepted_payload_digest=content_digest({"raw": raw}).hex(),
        accepted_normalized_digest=content_digest({"text": "synthetic precise input"}).hex(),
    )
    cutoff = datetime(2026, 1, 5, tzinfo=UTC)
    manifest = frozen_manifest(decision, 1, cutoff, (frozen,))
    plan = {
        "decision_id": str(decision),
        "policy_version": 1,
        "cutoff": cutoff.isoformat(),
        "rows": [frozen.model_dump(mode="json")],
        "manifest_digest": manifest.hex(),
        "state": "holder_pending",
    }

    class Pool:
        def __init__(self, terminal):
            self.row = {
                "id": request,
                "request_context": {"dedupe_key": key},
                "raw_payload": {"source": {"provider": "owntracks"}, "payload": {"raw": raw}},
                "normalized_text": "synthetic precise input",
                "lifecycle_state": terminal,
                "final_state_at": cutoff,
                "response_summary": "Policy bypass: "
                + ("skip" if terminal == "skipped" else terminal),
                "decomposition_output": {
                    "policy_bypass": True,
                    "triage_decision": "skip" if terminal == "skipped" else terminal,
                },
                "dispatch_outcomes": {"request_id": str(request)},
                "attachments": [],
                "processing_metadata": {},
            }
            self.receipt, self.floors, self.session = None, [], False
            self.unknown_census, self.receipt_fault = False, False
            self.census, self.readbacks = 0, 0

        @asynccontextmanager
        async def acquire(self):
            self.readbacks += 1
            yield self

        @asynccontextmanager
        async def transaction(self):
            before = deepcopy((self.row, self.receipt, self.floors))
            try:
                yield
            except BaseException:
                self.row, self.receipt, self.floors = before
                raise

        async def fetchrow(self, query, *args):
            if "FROM location_retention_copy_receipts" in query:
                return self.receipt
            assert "FROM switchboard.message_inbox" in query and args == (request,)
            return deepcopy(self.row)

        async def fetch(self, query, *args):
            if "location_retention_source_floors" in query:
                return deepcopy(self.floors)
            assert "FROM switchboard.message_inbox" in query
            if "SELECT id" in query:
                assert args == ([key],)
                return [{"id": request}]
            assert "FOR UPDATE" in query and args == ([request],)
            return [deepcopy(self.row)]

        async def fetchval(self, query, *args):
            assert "FROM sessions WHERE request_id=$1" in query and args == (str(request),)
            assert type(args[0]) is str
            return self.session

        async def execute(self, query, *args):
            if "pg_advisory_xact_lock" in query or query.startswith("SET LOCAL"):
                return
            if "INSERT INTO location_retention_copy_receipts" in query:
                if self.receipt_fault:
                    raise RuntimeError("synthetic receipt failure")
                self.receipt = {
                    "decision_id": decision,
                    "manifest_digest": manifest,
                    "receipt_id": args[2],
                    "source_kind": "switchboard_skipped",
                    "forgotten_count": 1,
                }
                return
            if "INSERT INTO location_retention_source_floors" in query:
                self.floors.append({"request_id": request, "logical_source_digest": args[3]})
                return
            assert "UPDATE switchboard.message_inbox" in query and args == (request,)
            self.row.update(
                raw_payload={"retention": "forgotten"},
                normalized_text="[OwnTracks exact evidence forgotten]",
                attachments=[],
                processing_metadata={},
            )

    async def source(pool, actual):
        assert actual == decision
        return plan

    async def census(conn, actual, actual_key, row):
        assert actual == request and actual_key == key and row == conn.row
        conn.census += 1
        if conn.unknown_census:
            raise copies.CopyFloorUnavailable("ingress_input_cohort_pending")

    from unittest.mock import patch

    with (
        patch.object(copies, "_registered_plan", source),
        patch.object(ingress, "lock_ingress_census", AsyncMock()),
        patch.object(ingress, "require_ingress_closed", census),
    ):
        for terminal in ("skipped", "metadata_only"):
            pool = Pool(terminal)
            original = deepcopy(pool.row)
            for invalid in ("unknown_census", "session", "changed_decision", "route", "unfinished"):
                pool.row = deepcopy(original)
                pool.unknown_census, pool.session = False, False
                if invalid == "unknown_census":
                    pool.unknown_census = True
                elif invalid == "session":
                    pool.session = True
                elif invalid == "changed_decision":
                    pool.row["decomposition_output"]["triage_decision"] = (
                        "skip" if terminal == "metadata_only" else "metadata_only"
                    )
                elif invalid == "route":
                    pool.row["dispatch_outcomes"]["routed"] = ["chronicler"]
                else:
                    pool.row["final_state_at"] = None
                planted = deepcopy(pool.row)
                with pytest.raises(copies.CopyFloorUnavailable):
                    await copies.forget_skipped_source(pool, decision)
                assert pool.row == planted and pool.receipt is None and not pool.floors
            pool.row, pool.unknown_census, pool.session = deepcopy(original), False, False
            receipt = await copies.forget_skipped_source(pool, decision)
            assert receipt["manifest_digest"] == manifest.hex() and receipt["forgotten_count"] == 1
            assert pool.row["raw_payload"] == {"retention": "forgotten"}
            assert pool.row["normalized_text"] == "[OwnTracks exact evidence forgotten]"
            assert pool.floors == [
                {
                    "request_id": request,
                    "logical_source_digest": bytes.fromhex(frozen.logical_source_digest),
                }
            ]
            assert pool.census > 0 and pool.readbacks > 1
            assert await copies.forget_skipped_source(pool, decision) == receipt
            pool = Pool(terminal)
            original = deepcopy(pool.row)
            pool.receipt_fault = True
            with pytest.raises(RuntimeError, match="synthetic receipt failure"):
                await copies.forget_skipped_source(pool, decision)
            assert pool.row == original and pool.receipt is None and not pool.floors


async def _assert_registered_direct_ingress_diagnostics(mcp):
    """Actual registered closure, modeled pipeline/event I/O; no SQL or erasure."""
    import inspect
    from types import SimpleNamespace
    from unittest.mock import patch
    from uuid import uuid4

    actual_ingest = (await mcp.get_tool("ingest")).fn
    while "func" in inspect.getclosurevars(actual_ingest).nonlocals:
        actual_ingest = inspect.getclosurevars(actual_ingest).nonlocals["func"]
    process = inspect.getclosurevars(actual_ingest).nonlocals["_process_ingested_message"]
    precise = "synthetic GPS 1.31415926,103.81234567 private-SSID"
    for channel in ("owntracks", "ordinary"):
        for outcome in ("error", "result", "healthy"):
            error = RuntimeError(precise)
            pipeline = SimpleNamespace(
                process=AsyncMock(
                    side_effect=error if outcome == "error" else None,
                    return_value=SimpleNamespace(
                        classification_error=precise if outcome == "result" else None,
                        routing_error=precise if outcome == "result" else None,
                        failed_targets=[precise] if outcome == "result" else [],
                    ),
                )
            )
            with (
                patch(
                    "butlers.core.ingestion_events.ingestion_event_reconcile_after_processing",
                    new=AsyncMock(),
                ) as event,
                patch("butlers.core_tools._switchboard.logger.exception") as exceptional,
                patch("butlers.core_tools._switchboard.logger.error") as warning,
            ):
                result = await process(
                    pipeline,
                    str(uuid4()),
                    precise,
                    {
                        "channel": channel,
                        "provider": channel,
                        "endpoint_identity": "synthetic phone",
                    },
                    {},
                    {},
                    uuid4(),
                )
            assert result is None
            assert pipeline.process.await_args.kwargs["message_text"] == precise
            actual = event.await_args.kwargs
            assert actual["routing_failed"] is (outcome != "healthy")
            if outcome == "healthy":
                assert (
                    actual["error_detail"] is None and not exceptional.called and not warning.called
                )
            elif channel == "owntracks":
                assert not exceptional.called
                assert precise not in repr(warning.call_args_list)
                assert precise not in actual["error_detail"]
                if outcome == "error":
                    assert actual["error_detail"] == "pipeline_exception:RuntimeError"
                    assert warning.called
                else:
                    assert (
                        actual["error_detail"]
                        == "classification_error; routing_error; failed_targets:1"
                    )
            else:
                assert precise in actual["error_detail"]
                assert exceptional.called is (outcome == "error")


async def _assert_native_ingress_runtime_values():
    """Actual pre-context writer and active producer; metadata I/O doubles only."""
    import asyncio
    import copy
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_input_binding import _dispatchers, register_dispatch_runtime
    from butlers.chronicler.location_memory_context import (
        _context_writers,
        begin_runtime_context,
        bind_context_session,
        capture_context_prompt,
        current_runtime_context,
        end_runtime_context,
        register_context_writer,
        verify_context_session,
    )
    from butlers.chronicler.location_policy import PolicyUnavailableError
    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import _Input, _processing_scope, _writers
    from butlers.core.location_ingress_runtime import current_ingress_runtime_input

    canonical = {"raw_payload": {"synthetic": "native raw input"}, "normalized_text": "input"}
    prompt = "Synthetic full classifier prompt with independent instructions"
    request, parent = uuid4(), uuid4()

    class Pool:
        source = None
        reserved = None
        structured = None
        structured_output = None
        output_unknown = False
        output_fault = False
        sdk_birth = None
        sdk_end = None
        sdk_unknown = False
        sdk_end_unknown = False
        intent = None
        binding = None
        session = None
        floor = False
        unknown = False
        fault = False
        trace = []
        transaction_active = False

        def is_closing(self):
            return False

        @asynccontextmanager
        async def acquire(self):
            self.trace.append("own acquisition")
            yield self

        @asynccontextmanager
        async def transaction(self):
            original = copy.deepcopy(
                (
                    self.reserved,
                    self.intent,
                    self.binding,
                    self.structured,
                    self.structured_output,
                    self.sdk_birth,
                    self.sdk_end,
                )
            )
            self.transaction_active = True
            try:
                yield
            except BaseException:
                (
                    self.reserved,
                    self.intent,
                    self.binding,
                    self.structured,
                    self.structured_output,
                    self.sdk_birth,
                    self.sdk_end,
                ) = original
                raise
            finally:
                self.transaction_active = False
                self.trace.append("transaction ended")

        async def fetchrow(self, sql, *args):
            if sql == "SELECT current_schema(),current_user":
                return dict(schema="switchboard", role="butler_switchboard_rw")
            if "FROM location_ingress_input_births " in sql:
                assert "LEFT JOIN location_ingress_input_claims" in sql
                assert "LEFT JOIN location_ingress_input_parents" in sql
                assert args == (child.generation,)
                return self.source
            if "FROM message_inbox " in sql:
                assert "FOR SHARE" in sql and args == (request,)
                return canonical
            if "FROM location_ingress_runtime_inputs r " in sql:
                assert not self.transaction_active and args == (self.intent[0],)
                self.trace.append("independent committed ancestry readback")
                if self.unknown:
                    return None
                fields = (
                    "input_generation",
                    "copy_generation",
                    "receiving_session",
                    "request_id",
                    "stored_digest",
                    "envelope_digest",
                    "prompt_digest",
                )
                row = dict(zip(fields, self.reserved))
                return row | {
                    "original_request": request,
                    "original_digest": content_digest(canonical),
                    "reserved_session": self.intent[1],
                }
            if "FROM location_ingress_structured_sdk_births WHERE" in sql:
                assert args == (self.sdk_birth[0],)
                if self.transaction_active:
                    assert "FOR SHARE" in sql
                else:
                    self.trace.append("independent structured SDK claim")
                    if self.sdk_unknown:
                        return None
                return dict(zip(("task", "handler", "incarnation"), self.sdk_birth[1:]))
            if "FROM location_ingress_structured_sdk_ends " in sql and "FOR SHARE" in sql:
                assert self.transaction_active
                if self.sdk_end is None or args != (self.sdk_end[0],):
                    return None
                return dict(zip(("task_generation", "receipt_id"), self.sdk_end[1:]))
            if "FROM location_ingress_structured_sdk_ends " in sql:
                assert not self.transaction_active and args == (self.sdk_end[0],)
                self.trace.append("independent structured SDK end")
                if self.sdk_end_unknown:
                    return None
                return dict(zip(("task", "receipt"), self.sdk_end[1:]))
            if "FROM location_ingress_structured_inputs WHERE" in sql:
                assert self.transaction_active and args == (self.structured[0],)
                assert "FOR SHARE" in sql
                return dict(zip(("copy", "request", "stored", "envelope"), self.structured[1:5]))
            if "FROM location_ingress_structured_outputs o " in sql:
                assert not self.transaction_active and args == (self.structured_output[0],)
                assert "LEFT JOIN location_ingress_structured_inputs" in sql
                assert "LEFT JOIN location_ingress_accepted_inputs" in sql
                self.trace.append("independent structured output readback")
                if self.output_unknown:
                    return None
                fields = (
                    "output",
                    "copy",
                    "request",
                    "stored",
                    "envelope",
                    "original_request",
                    "original_digest",
                )
                return dict(
                    zip(
                        fields,
                        (
                            self.structured_output[1],
                            *self.structured[1:5],
                            request,
                            content_digest(canonical),
                        ),
                    )
                )
            if "FROM location_ingress_structured_inputs s " in sql:
                assert not self.transaction_active and args == (self.structured[0],)
                self.trace.append("independent structured input readback")
                if self.unknown:
                    return None
                fields = (
                    "copy_generation",
                    "request_id",
                    "stored_digest",
                    "envelope_digest",
                    "prompt_digest",
                    "system_digest",
                    "tools_digest",
                    "original_request",
                    "original_digest",
                )
                return dict(zip(fields, (*self.structured[1:], request, content_digest(canonical))))
            assert "SELECT prompt,effective_system_prompt FROM sessions" in sql
            return self.session

        async def fetchval(self, sql, *args):
            if "FROM location_retention_source_floors" in sql:
                return self.floor
            if "FROM location_runtime_context_intents" in sql:
                assert not self.transaction_active
                return self.intent[1]
            if "FROM location_runtime_context_ended" in sql:
                return self.ended[1]
            if "FROM location_ingress_runtime_inputs " in sql:
                return (
                    self.reserved[0] == args[0]
                    and self.reserved[2] == args[1]
                    and self.reserved[6] == args[2]
                )
            assert "FROM location_runtime_context_bindings" in sql
            return self.binding is not None

        async def execute(self, sql, *args):
            assert self.transaction_active
            if "pg_advisory_xact_lock" in sql:
                self.trace.append("ingress control first")
            elif "INSERT INTO location_runtime_context_intents" in sql:
                self.intent = args
                self.trace.append("runtime intent")
            elif "INSERT INTO location_ingress_runtime_inputs" in sql:
                self.reserved = args
                self.trace.append("raw descendant")
                if self.fault:
                    raise RuntimeError("synthetic same-writer descendant failure")
            elif "INSERT INTO location_ingress_structured_inputs" in sql:
                self.structured = args
                self.trace.append("structured full input")
                if self.fault:
                    raise RuntimeError("synthetic same-writer structured failure")
            elif "INSERT INTO location_ingress_structured_sdk_births" in sql:
                self.sdk_birth = args
                self.trace.append("structured SDK claim")
            elif "INSERT INTO location_ingress_structured_sdk_ends" in sql:
                if self.sdk_end is not None and self.sdk_end[0] == args[0]:
                    import asyncpg

                    raise asyncpg.UniqueViolationError("Synthetic duplicate SDK receipt")
                self.sdk_end = args
                self.trace.append("structured SDK end")
            elif "INSERT INTO location_ingress_structured_outputs" in sql:
                self.structured_output = args
                self.trace.append("structured output lineage")
                if self.output_fault:
                    raise RuntimeError("synthetic same-writer output failure")
            elif "INSERT INTO location_runtime_context_bindings" in sql:
                self.binding = args
            else:
                assert "INSERT INTO location_runtime_context_ended" in sql
                self.ended = args

    pool = Pool()
    child = _Input(uuid4(), uuid4(), b"s" * 32, b"e" * 32, asyncio.current_task(), uuid4(), 3)
    owner = SimpleNamespace(
        pool=pool, active=True, incarnation=uuid4(), _inputs={id(child): child}, _structured_sdk={}
    )
    source = dict(
        request_id=request,
        stored_digest=content_digest(canonical),
        envelope_digest=child.envelope_digest,
        handler_generation=child.handler,
        incarnation=owner.incarnation,
        parent_generation=parent,
        parent_request=request,
        parent_digest=content_digest(canonical),
    )
    pool.source = source

    async def lock_domain(conn):
        assert conn is pool and conn.transaction_active
        pool.trace.append("catalog lock")

    runtime = SimpleNamespace(domain=pool, active=True, lock_domain=lock_domain)
    spawner = SimpleNamespace(_pool=pool)
    register_dispatch_runtime(spawner, object)
    register_context_writer(runtime)
    _writers[pool] = owner
    token = _processing_scope.set((owner, child))
    try:
        assert current_ingress_runtime_input(pool) == (owner, child)
        for bad in (
            None,
            source | {"parent_generation": None},
            source | {"parent_digest": b"x" * 32},
        ):
            pool.source = bad
            with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_ancestry_unknown"):
                await begin_runtime_context(pool, spawner, prompt=prompt)
            assert pool.reserved is None and pool.intent is None
        pool.source = source
        pool.floor = True
        with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_source_disposed"):
            await begin_runtime_context(pool, spawner, prompt=prompt)
        assert pool.reserved is None and pool.intent is None
        pool.floor = False
        pool.fault = True
        with pytest.raises(RuntimeError, match="synthetic same-writer descendant failure"):
            await begin_runtime_context(pool, spawner, prompt=prompt)
        assert pool.reserved is None and pool.intent is None
        pool.fault = False
        pool.unknown = True
        with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_commit_unknown"):
            await begin_runtime_context(pool, spawner, prompt=prompt)
        assert pool.reserved is not None and current_runtime_context() is None
        pool.reserved = pool.intent = None  # Model a separate healthy attempt, no SQL credit.
        pool.unknown = False
        pool.trace.clear()
        handle = await begin_runtime_context(pool, spawner, prompt=prompt)
        context = handle[0]
        try:
            assert context.ingress_input == (owner, child)
            assert context.generated_prompt and not context.known_context
            assert pool.reserved[6] == hashlib.sha256(prompt.encode()).digest()
            assert pool.trace.index("ingress control first") < pool.trace.index("catalog lock")
            assert pool.trace.index("runtime intent") < pool.trace.index("raw descendant")
            assert pool.trace.index("transaction ended") < pool.trace.index(
                "independent committed ancestry readback"
            )
            capture_context_prompt(None, "Synthetic independent system")
            pool.session = dict(
                prompt=prompt, effective_system_prompt="Synthetic independent system"
            )
            async with pool.transaction():
                await bind_context_session(pool, pool, context.session, prompt)
            await verify_context_session(pool, context.session)
            assert (
                pool.binding[6] is False
            )  # Captured ancestry does not assert exclusive ownership.
            pool.session["prompt"] = prompt + " changed"
            with pytest.raises(
                PolicyUnavailableError, match="Native ingress composed input differs"
            ):
                async with pool.transaction():
                    await bind_context_session(pool, pool, context.session, pool.session["prompt"])
            assert pool.binding[6] is False
        finally:
            await end_runtime_context(handle)
        assert current_runtime_context() is None
        from unittest.mock import AsyncMock, MagicMock, patch

        from butlers.core.location_ingress_runtime import (
            capture_structured_ingress_output,
            finish_structured_ingress_sdk,
            prepare_structured_ingress_sdk,
            reserve_structured_ingress_input,
            start_structured_ingress_sdk,
        )
        from butlers.tools.switchboard.routing import structured_classify as sc

        tools = [sc.ROUTE_TO_BUTLER_TOOL]
        for bad in (None, source | {"parent_digest": b"x" * 32}):
            pool.source = bad
            with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_ancestry_unknown"):
                await reserve_structured_ingress_input(
                    pool, prompt=prompt, system_prompt="Independent fixed system", tools=tools
                )
            assert pool.structured is None
        pool.source = source
        pool.fault = True
        with pytest.raises(RuntimeError, match="synthetic same-writer structured failure"):
            await reserve_structured_ingress_input(
                pool, prompt=prompt, system_prompt="Independent fixed system", tools=tools
            )
        assert pool.structured is None
        pool.fault = False
        adapter = MagicMock()
        adapter.invoke_structured = AsyncMock()

        async def actual_route(**kwargs):
            assert pool.sdk_end is not None
            assert "independent structured SDK end" in pool.trace
            assert pool.structured_output is not None
            assert "independent structured output readback" in pool.trace
            assert pool.trace.index("structured output lineage") < pool.trace.index(
                "independent structured output readback"
            )
            return {}

        route = AsyncMock(side_effect=actual_route)
        server = SimpleNamespace(get_tool=lambda name: SimpleNamespace(fn=route))
        catalog = ("api", "synthetic-model", [], uuid4(), 30, "cheap")

        async def actual_adapter(**kwargs):
            assert asyncio.current_task() is not child.task
            assert pool.sdk_birth is not None
            assert "independent structured SDK claim" in pool.trace
            assert pool.structured is not None
            assert pool.trace.index("transaction ended") < pool.trace.index(
                "independent structured input readback"
            )
            assert pool.structured[5] == hashlib.sha256(kwargs["prompt"].encode()).digest()
            assert pool.structured[7] == content_digest({"tools": kwargs["tools"]})
            return (
                [dict(name="route_to_butler", input=dict(butler="chronicler", prompt="synthetic"))],
                None,
                None,
            )

        adapter.invoke_structured.side_effect = actual_adapter
        module = "butlers.tools.switchboard.routing.structured_classify"
        with (
            patch(module + ".resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
            patch(
                module + ".check_token_quota", AsyncMock(return_value=SimpleNamespace(allowed=True))
            ),
            patch(module + ".create_adapter", return_value=adapter),
        ):
            pool.trace.clear()
            pool.unknown = True
            with pytest.raises(CopyFloorUnavailable, match="ingress_structured_commit_unknown"):
                await sc.try_structured_classification(
                    pool, mcp_server=server, prompt=prompt, include_bug_report=False
                )
            adapter.invoke_structured.assert_not_awaited()
            pool.unknown = False
            refused_starts = []

            async def refuse_actual_start(actual_pool, binding, invoke):
                original = owner._structured_sdk.pop(id(binding))
                try:
                    try:
                        await start_structured_ingress_sdk(actual_pool, binding, invoke)
                    except CopyFloorUnavailable as refusal:
                        refused_starts.append(refusal)
                        raise
                finally:
                    owner._structured_sdk[id(binding)] = original
                    # The test owns the still-gated Task cleanup. Cancellation
                    # cannot publish an SDK end or attest original-copy disposal.
                    binding.task.cancel()
                    await asyncio.gather(binding.task, return_exceptions=True)

            with patch(
                "butlers.core.location_ingress_runtime.start_structured_ingress_sdk",
                side_effect=refuse_actual_start,
            ):
                with pytest.raises(CopyFloorUnavailable) as refused:
                    await sc.try_structured_classification(
                        pool, mcp_server=server, prompt=prompt, include_bug_report=False
                    )
                assert refused.value is refused_starts[0]
                assert "ingress_structured_sdk_producer_differs" == str(refused.value)
            adapter.invoke_structured.assert_not_awaited()
            route.assert_not_awaited()
            assert pool.sdk_end is None
            pool.sdk_unknown = True
            with pytest.raises(CopyFloorUnavailable, match="ingress_structured_sdk_commit_unknown"):
                await sc.try_structured_classification(
                    pool, mcp_server=server, prompt=prompt, include_bug_report=False
                )
            adapter.invoke_structured.assert_not_awaited()
            assert pool.sdk_birth is not None and pool.sdk_end is None
            assert all(entry.task.done() for entry in owner._structured_sdk.values())
            pool.sdk_unknown = False
            pool.sdk_end_unknown = True
            with pytest.raises(CopyFloorUnavailable, match="ingress_structured_sdk_end_unknown"):
                await sc.try_structured_classification(
                    pool, mcp_server=server, prompt=prompt, include_bug_report=False
                )
            route.assert_not_awaited()
            assert pool.sdk_end is not None  # Committed ACK unknown, not false rollback.
            assert any(entry.reply is not None for entry in owner._structured_sdk.values())
            pool.sdk_end_unknown = False
            same_sdk = next(
                entry
                for entry in owner._structured_sdk.values()
                if entry.generation == pool.sdk_end[0]
            )
            committed_end = pool.sdk_end
            body = same_sdk.reply
            writes = pool.trace.count("structured SDK end")
            original_birth = pool.sdk_birth
            for field in (1, 2, 3):  # Original Task, handler and incarnation.
                changed_birth = list(original_birth)
                changed_birth[field] = uuid4()
                pool.sdk_birth = tuple(changed_birth)
                with pytest.raises(
                    CopyFloorUnavailable, match="ingress_structured_sdk_claim_differs"
                ):
                    await finish_structured_ingress_sdk(pool, same_sdk)
                assert same_sdk.reply is body and pool.trace.count("structured SDK end") == writes
            pool.sdk_birth = original_birth
            # A planted differing claim must not reuse another Task's receipt.
            pool.sdk_end = (committed_end[0], uuid4(), committed_end[2])
            with pytest.raises(
                CopyFloorUnavailable, match="ingress_structured_sdk_receipt_differs"
            ):
                await finish_structured_ingress_sdk(pool, same_sdk)
            assert same_sdk.reply is body and pool.trace.count("structured SDK end") == writes
            pool.sdk_end = committed_end
            assert await finish_structured_ingress_sdk(pool, same_sdk) is body
            assert (
                pool.sdk_end == committed_end and pool.trace.count("structured SDK end") == writes
            )
            assert same_sdk.reply is None and same_sdk.invoke is None
            assert id(same_sdk) not in owner._structured_sdk
            adapter.invoke_structured.reset_mock()
            pool.sdk_birth = pool.sdk_end = None
            pool.output_unknown = True
            with pytest.raises(
                CopyFloorUnavailable, match="ingress_structured_output_commit_unknown"
            ):
                await sc.try_structured_classification(
                    pool, mcp_server=server, prompt=prompt, include_bug_report=False
                )
            route.assert_not_awaited()
            assert pool.structured_output is not None  # Unknown ACK does not undo COMMIT.
            pool.output_unknown = False
            preserved = pool.structured_output
            pool.output_fault = True
            with pytest.raises(RuntimeError, match="synthetic same-writer output failure"):
                await capture_structured_ingress_output(
                    pool, pool.structured[0], tool_calls=[], text="synthetic output"
                )
            assert pool.structured_output == preserved
            pool.output_fault = False
            pool.source = source | {"parent_generation": None}
            with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_ancestry_unknown"):
                await capture_structured_ingress_output(
                    pool, pool.structured[0], tool_calls=[], text=None
                )
            assert pool.structured_output == preserved
            pool.source = source
            adapter.invoke_structured.reset_mock()
            pool.structured_output = None
            # Model a separate healthy attempt, never SQL history repair credit.
            pool.unknown = False
            pool.structured = None
            pool.trace.clear()
            decision = await sc.try_structured_classification(
                pool, mcp_server=server, prompt=prompt, include_bug_report=False
            )
            assert decision is not None and adapter.invoke_structured.await_count == 1
            assert pool.structured[1] == child.generation
            assert pool.structured[6] == hashlib.sha256(b"").digest()
            assert pool.structured[7] == content_digest({"tools": tools})
            assert route.await_count == 1
            assert pool.structured_output[0] == pool.structured[0]
            assert pool.structured_output[1] == content_digest(
                {
                    "tool_calls": [
                        dict(
                            name="route_to_butler",
                            input=dict(butler="chronicler", prompt="synthetic"),
                        )
                    ],
                    "text": None,
                }
            )
        # The SDK retains its returned mutable object. After actual output
        # capture/readback, mutation of that alias must not change the body
        # subsequently consumed by the actual local tool handler.
        returned_calls = [
            dict(name="route_to_butler", input=dict(butler="chronicler", prompt="synthetic"))
        ]
        original_returned = copy.deepcopy(returned_calls)

        async def aliased_reply(**kwargs):
            await actual_adapter(**kwargs)
            return returned_calls, None, None

        async def change_returned_alias(actual_pool, generation, *, tool_calls, text):
            await capture_structured_ingress_output(
                actual_pool, generation, tool_calls=tool_calls, text=text
            )
            assert pool.structured_output[1] == content_digest(
                {"tool_calls": original_returned, "text": None}
            )
            returned_calls[0]["input"]["prompt"] = "Synthetic changed after output readback"

        with (
            patch(module + ".resolve_model_with_effective_tier", AsyncMock(return_value=catalog)),
            patch(
                module + ".check_token_quota", AsyncMock(return_value=SimpleNamespace(allowed=True))
            ),
            patch(module + ".create_adapter", return_value=adapter),
            patch(
                "butlers.core.location_ingress_runtime.capture_structured_ingress_output",
                side_effect=change_returned_alias,
            ),
        ):
            adapter.invoke_structured.side_effect = aliased_reply
            route.reset_mock()
            decision = await sc.try_structured_classification(
                pool, mcp_server=server, prompt=prompt, include_bug_report=False
            )
            assert decision is not None and route.await_count == 1
            assert route.call_args.kwargs["prompt"] == original_returned[0]["input"]["prompt"]
            assert returned_calls[0]["input"]["prompt"] != route.call_args.kwargs["prompt"]
            assert pool.structured_output[1] == content_digest(
                {"tool_calls": original_returned, "text": None}
            )
        adapter.invoke_structured.side_effect = actual_adapter
        # Mutate the actual shared nested schema only after the actual input
        # has committed and its SDK Task birth/readback have completed. The
        # SDK must receive the original privately frozen body, not that alias.
        original_tool = copy.deepcopy(sc.ROUTE_TO_BUTLER_TOOL)
        snapshot_digest = content_digest({"tools": [original_tool]})

        async def change_shared_schema(actual_pool, generation):
            prepared = await prepare_structured_ingress_sdk(actual_pool, generation)
            sc.ROUTE_TO_BUTLER_TOOL["input_schema"]["properties"]["prompt"]["description"] = (
                "Synthetic schema mutation after admitted body"
            )
            assert content_digest({"tools": [sc.ROUTE_TO_BUTLER_TOOL]}) != snapshot_digest
            assert pool.structured[7] == snapshot_digest
            return prepared

        try:
            with (
                patch(
                    module + ".resolve_model_with_effective_tier", AsyncMock(return_value=catalog)
                ),
                patch(
                    module + ".check_token_quota",
                    AsyncMock(return_value=SimpleNamespace(allowed=True)),
                ),
                patch(module + ".create_adapter", return_value=adapter),
                patch(
                    "butlers.core.location_ingress_runtime.prepare_structured_ingress_sdk",
                    side_effect=change_shared_schema,
                ),
            ):
                adapter.invoke_structured.reset_mock()
                decision = await sc.try_structured_classification(
                    pool, mcp_server=server, prompt=prompt, include_bug_report=False
                )
                assert decision is not None and adapter.invoke_structured.await_count == 1
                assert pool.structured[7] == snapshot_digest
                received = adapter.invoke_structured.call_args.kwargs["tools"]
                assert content_digest({"tools": received}) == snapshot_digest
                assert received[0] is not sc.ROUTE_TO_BUTLER_TOOL
        finally:
            sc.ROUTE_TO_BUTLER_TOOL.clear()
            sc.ROUTE_TO_BUTLER_TOOL.update(original_tool)
        assert sc.ROUTE_TO_BUTLER_TOOL == original_tool
        # A completed failed SDK Task retains its actual exception/frames.
        # The real helper preserves primary identity and grants no SDK end.
        sdk_input = await reserve_structured_ingress_input(
            pool, prompt=prompt, system_prompt="Independent fixed system", tools=tools
        )
        primary = RuntimeError("synthetic SDK primary")
        sdk = await prepare_structured_ingress_sdk(pool, sdk_input)
        pool.sdk_end = None

        async def failed_sdk():
            retained_copy = {"synthetic": prompt}
            assert retained_copy["synthetic"] == prompt
            raise primary

        with pytest.raises(RuntimeError) as caught:
            await start_structured_ingress_sdk(pool, sdk, failed_sdk)
        assert caught.value is primary and sdk.task.exception() is primary
        assert sdk.reply is None and pool.sdk_end is None
        assert sdk.task.done() and primary.__traceback__ is not None
        with pytest.raises(CopyFloorUnavailable, match="ingress_structured_sdk_lifetime_differs"):
            await finish_structured_ingress_sdk(pool, sdk)
        assert pool.sdk_end is None and owner._structured_sdk[id(sdk)] is sdk
        cancelled_input = await reserve_structured_ingress_input(
            pool, prompt=prompt, system_prompt="Independent fixed system", tools=tools
        )
        cancelled = await prepare_structured_ingress_sdk(pool, cancelled_input)

        async def cancel_sdk():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await start_structured_ingress_sdk(pool, cancelled, cancel_sdk)
        assert cancelled.task.cancelled() and pool.sdk_end is None
        with pytest.raises(CopyFloorUnavailable, match="ingress_structured_sdk_lifetime_differs"):
            await finish_structured_ingress_sdk(pool, cancelled)
        assert pool.sdk_end is None

        for change in ("registry", "task", "kind", "pool"):
            original_task, original_kind = child.task, child.kind
            if change == "registry":
                _writers.pop(pool)
            elif change == "task":
                child.task = None
            elif change == "kind":
                child.kind = 2
            try:
                with pytest.raises(CopyFloorUnavailable, match="ingress_runtime_producer_differs"):
                    current_ingress_runtime_input(object() if change == "pool" else pool)
            finally:
                _writers[pool] = owner
                child.task, child.kind = original_task, original_kind
    finally:
        _processing_scope.reset(token)
        _writers.pop(pool, None)
        _context_writers.pop(pool, None)
        _dispatchers.pop(pool, None)
    assert current_ingress_runtime_input(pool) is None
    assert (
        await reserve_structured_ingress_input(pool, prompt=prompt, system_prompt="", tools=tools)
        is None
    )


async def _assert_structured_local_processing_disposition_values():
    """Actual original Task/control/census engine; modeled metadata, not SQL."""
    import asyncio
    import copy
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.core.location_copy_retention import CopyFloorUnavailable
    from butlers.core.location_ingress_copies import SwitchboardInputCopies, _Input, _writers
    from butlers.core.location_ingress_runtime import (
        close_structured_processing_copies,
        verify_structured_processing_copies,
    )

    gate = asyncio.Event()

    async def process():
        owned_local = {"synthetic": "original classifier result"}
        await gate.wait()
        owned_local.clear()
        return None

    task = asyncio.create_task(process())
    child = _Input(uuid4(), uuid4(), b"d" * 32, b"e" * 32, task, uuid4(), 3)
    request, incarnation = uuid4(), uuid4()
    healthy = {
        "input_generation": uuid4(),
        "copy_generation": child.generation,
        "request_id": request,
        "stored_digest": b"s" * 32,
        "envelope_digest": child.envelope_digest,
        "original_request": request,
        "original_digest": b"s" * 32,
        "task_generation": uuid4(),
        "handler_generation": child.handler,
        "incarnation": incarnation,
        "sdk_receipt": uuid4(),
        "output_digest": b"o" * 32,
        "local_task": None,
        "local_handler": None,
        "local_incarnation": None,
        "local_output": None,
        "receipt_id": None,
    }
    healthy["ended_task"] = healthy["task_generation"]

    class Pool:
        rows = [healthy]
        transaction_active = False
        unknown = False
        fault = False
        commit_unknown = False
        input_end = False
        writes = 0

        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetch(self, sql, *args):
            assert "FROM location_ingress_structured_inputs s " in sql
            assert "LEFT JOIN location_ingress_structured_sdk_births" in sql
            assert "LEFT JOIN location_ingress_structured_sdk_ends" in sql
            assert "LEFT JOIN location_ingress_structured_outputs" in sql
            assert args == (child.generation,)
            return copy.deepcopy([] if self.unknown and not self.transaction_active else self.rows)

        async def fetchrow(self, sql, *args):
            assert self.transaction_active and args == (child.generation,)
            if "location_ingress_input_births" in sql:
                return dict(
                    server=child.server,
                    dedupe=child.dedupe_digest,
                    envelope=child.envelope_digest,
                    kind=3,
                )
            assert "location_ingress_input_claims" in sql
            return dict(handler=child.handler, incarnation=incarnation)

        async def fetchval(self, sql, *args):
            assert not self.transaction_active
            assert "location_ingress_input_ends" in sql and args == (child.generation,)
            return self.input_end

        async def execute(self, sql, *args):
            assert self.transaction_active
            if "INSERT INTO location_ingress_input_ends" in sql:
                assert args == (child.generation,)
                self.input_end = True
                return
            assert "INSERT INTO location_ingress_structured_local_ends" in sql
            selected = next(row for row in self.rows if row["input_generation"] == args[0])
            selected.update(
                zip(
                    (
                        "local_task",
                        "local_handler",
                        "local_incarnation",
                        "local_output",
                        "receipt_id",
                    ),
                    args[1:],
                )
            )
            self.writes += 1
            if self.fault:
                raise RuntimeError("synthetic actual local receipt fault")

    pool = Pool()
    owner = SimpleNamespace(
        pool=pool,
        incarnation=incarnation,
        _inputs={id(child): child},
        _headers={},
        _ended_inputs={id(child)},
        _ended_headers=set(),
        _cleared_inputs=set(),
        _structured_sdk={},
        _structured_local_closures={},
        _reconcile_lock=asyncio.Lock(),
    )

    @asynccontextmanager
    async def writer():
        before = copy.deepcopy(pool.rows), pool.input_end, pool.writes
        pool.transaction_active = True
        try:
            yield pool
            if pool.commit_unknown:
                raise RuntimeError("synthetic actual commit ACK unknown")
        except BaseException:
            pool.rows, pool.input_end, pool.writes = before
            raise
        finally:
            pool.transaction_active = False

    owner.writer = writer
    previous = _writers.get(pool)
    _writers[pool] = owner
    try:
        async with writer():
            with pytest.raises(CopyFloorUnavailable, match="processing_still_held"):
                await close_structured_processing_copies(owner, child, pool)
        assert pool.writes == 0
        gate.set()
        await task
        assert task.done() and task.result() is None
        retained_result = {"synthetic": "cached copied result"}

        async def result_holder():
            return retained_result

        primary = RuntimeError("synthetic original processing error")

        async def error_holder():
            retained_input = {"synthetic": "traceback copied input"}
            assert retained_input
            raise primary

        result_task = asyncio.create_task(result_holder())
        error_task = asyncio.create_task(error_holder())
        await result_task
        with pytest.raises(RuntimeError) as original_error:
            await error_task
        assert original_error.value is primary and error_task.exception() is primary
        for held_task in (result_task, error_task):
            child.task = held_task
            async with writer():
                with pytest.raises(CopyFloorUnavailable, match="processing_still_held"):
                    await close_structured_processing_copies(owner, child, pool)
            assert pool.writes == 0 and child.task is held_task
        assert result_task.result() is retained_result and primary.__traceback__ is not None
        child.task = task
        child.unresolved_failure = True
        async with writer():
            with pytest.raises(CopyFloorUnavailable, match="processing_still_held"):
                await close_structured_processing_copies(owner, child, pool)
        child.unresolved_failure = False
        owner._structured_sdk[1] = SimpleNamespace(child=child, reply={"synthetic": "held"})
        async with writer():
            with pytest.raises(CopyFloorUnavailable, match="processing_still_held"):
                await close_structured_processing_copies(owner, child, pool)
        assert pool.writes == 0
        owner._structured_sdk.clear()
        original = copy.deepcopy(healthy)
        for change in (
            {"ended_task": None},
            {"sdk_receipt": None},
            {"output_digest": None},
            {"handler_generation": uuid4()},
            {"incarnation": uuid4()},
            {"original_digest": b"x" * 32},
            {"copy_generation": uuid4()},
        ):
            pool.rows = [original | change]
            async with writer():
                with pytest.raises(CopyFloorUnavailable, match="cohort_differs"):
                    await close_structured_processing_copies(owner, child, pool)
            assert pool.writes == 0 and not pool.input_end
        sibling = original | {
            "input_generation": uuid4(),
            "task_generation": uuid4(),
            "sdk_receipt": uuid4(),
            "output_digest": b"b" * 32,
        }
        sibling["ended_task"] = sibling["task_generation"]
        pool.rows = [copy.deepcopy(original), sibling | {"ended_task": None}]
        async with writer():
            with pytest.raises(CopyFloorUnavailable, match="cohort_differs"):
                await close_structured_processing_copies(owner, child, pool)
        assert pool.writes == 0 and not pool.input_end
        full_original = [copy.deepcopy(original), copy.deepcopy(sibling)]
        pool.rows = copy.deepcopy(full_original)
        pool.fault = True
        with pytest.raises(RuntimeError, match="synthetic actual local receipt fault"):
            await SwitchboardInputCopies.reconcile_observed_ends(owner)
        assert pool.writes == 0 and pool.rows == full_original and not pool.input_end
        assert owner._inputs[id(child)] is child
        pool.fault = False
        pool.commit_unknown = True
        with pytest.raises(RuntimeError, match="synthetic actual commit ACK unknown"):
            await SwitchboardInputCopies.reconcile_observed_ends(owner)
        assert pool.writes == 0 and pool.rows == full_original and not pool.input_end
        planned = owner._structured_local_closures[id(child)]
        assert len(planned) == 2 and all(entry[-1] is not None for entry in planned)
        pool.commit_unknown = False
        pool.unknown = True
        with pytest.raises(CopyFloorUnavailable, match="processing_end_unknown"):
            await SwitchboardInputCopies.reconcile_observed_ends(owner)
        assert pool.input_end and pool.writes == 2 and owner._inputs[id(child)] is child
        receipt = pool.rows[0]["receipt_id"]
        assert {row["receipt_id"] for row in pool.rows} == {entry[-1] for entry in planned}
        pool.unknown = False
        full_committed = copy.deepcopy(pool.rows)
        pool.rows = pool.rows[:1]
        with pytest.raises(CopyFloorUnavailable, match="processing_end_unknown"):
            await verify_structured_processing_copies(owner, child)
        pool.rows = copy.deepcopy(full_committed)
        pool.rows[0].update(
            dict(
                local_task=None,
                local_handler=None,
                local_incarnation=None,
                local_output=None,
                receipt_id=None,
            )
        )
        differing_receipt = uuid4()
        pool.rows[1]["receipt_id"] = differing_receipt
        writes_before_mismatch = pool.writes
        await SwitchboardInputCopies.reconcile_observed_ends(owner)
        assert pool.writes == writes_before_mismatch
        assert pool.rows[0]["receipt_id"] is None
        assert pool.rows[1]["receipt_id"] == differing_receipt
        assert owner._inputs[id(child)] is child
        pool.rows = full_committed
        pool.rows[0]["local_output"] = b"x" * 32
        with pytest.raises(CopyFloorUnavailable, match="receipt_differs"):
            await verify_structured_processing_copies(owner, child)
        pool.rows[0]["local_output"] = original["output_digest"]
        await SwitchboardInputCopies.reconcile_observed_ends(owner)
        assert pool.writes == 2 and pool.rows[0]["receipt_id"] == receipt
        assert id(child) not in owner._inputs and id(child) not in owner._ended_inputs
        assert id(child) not in owner._structured_local_closures
        # This receipt settles local frame copies only. It does not create a
        # routed receiver, remote provider or SDK error-holder disposition.
    finally:
        if not task.done():
            gate.set()
            await task
        if previous is None:
            _writers.pop(pool, None)
        else:
            _writers[pool] = previous
