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


async def test_native_mcp_input_birth_precedes_emission_and_unknown_commit_refuses(monkeypatch):
    """REQ-location-retention-005/006; actual producer/guard software, not SQL/MCP transport."""
    from contextlib import asynccontextmanager
    from dataclasses import dataclass
    from types import SimpleNamespace
    from uuid import uuid4

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
            self.changed = False
            self.catalog_unknown = False
            self.catalog_pending = False
            self.artifact_pending = False
            self.catalog = []
            self.unknown_commit = False
            self.frontier = None
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
            if "location_retention_policy" in query:
                self.trace.append("policy")
                return {"version": 1}
            if "location_retention_plans" in query:
                return {
                    "decision_id": decision,
                    "state": "holder_pending",
                    "manifest_digest": manifest,
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
            if "FROM public.memory_catalog" in query:
                return self.catalog_unknown
            if "FROM location_native_catalog_generations" in query:
                return self.catalog_pending
            if "FROM location_native_memory_artifacts" in query:
                return self.artifact_pending
            if "FROM location_native_memory_parents" in query:
                return False
            if "FROM sessions" in query or "FROM tier2_cache" in query:
                return self.legacy
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
        # This frontier producer itself has no deletion side effect. The real
        # role/point/raw action and separate readbacks are distinct controls.
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
                return [
                    {
                        **self.artifact,
                        "output_kind": "point_event",
                        "output_id": self.row["id"],
                        "lineage_known": self.artifact_exclusive,
                        "exclusive_input": self.artifact_exclusive,
                    }
                ]
            if "FROM chronicler.location_native_memory_commits" in sql:
                return (
                    [
                        {
                            "output_kind": "point_event",
                            "output_id": self.row["id"],
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
async def test_catalog_native_admission_precedes_delegate_and_server_lifetime_is_bounded():
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

        async def fetchrow(self, sql, *args):
            table = sql.split(" FROM ")[1].split()[0]
            assert sql.endswith("FOR UPDATE") and args[0] == self.rows[table]["id"]
            return self.rows[table]

        async def fetch(self, sql, *args):
            table = "episodes" if "location_native_memory_commits" in sql else args[0]
            return [] if table == self.missing else [self.witnesses[table]]

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
        artifacts = []
        tool_witnesses = []
        tool_loans = []
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
            if "public.memory_catalog" in sql:
                return None
            if "SELECT * FROM rules" in sql:
                return self.artifact_row
            if "location_native_memory_bundles" in sql:
                return None
            return frozen if "context_bindings" in sql else session

        async def fetch(self, sql, *args):
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
                self.receipt = args[-1]

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

    async def source_fetch(sql, *args):
        if "location_catalog_copy_loans l" in sql:
            return native_pool.tool_loans
        if "location_native_catalog_generations" in sql:
            return (
                [
                    {
                        "copy_generation": late_parent,
                        "input_digest": b"l" * 32,
                        "lineage_known": True,
                        "exclusive_input": True,
                    }
                ]
                if source_parent_known
                else []
            )
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
            saved = deepcopy((self.row, self.transitions))
            schema = self.schema
            self.trace.append("begin")
            try:
                yield
            except BaseException:
                self.row, self.transitions = saved
                self.trace.append("rollback")
                raise
            else:
                self.trace.append("commit")
            finally:
                self.schema = schema  # SET LOCAL never changes the pool view.

        async def fetchrow(self, sql, *args):
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
            assert "location_native_memory_mutations" in sql
            return deepcopy(self.transitions)

        async def fetchval(self, sql, *args):
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
