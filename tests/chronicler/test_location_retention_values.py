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
    finally:
        _receivers.pop(domain)


@pytest.mark.asyncio
async def test_runtime_context_disposes_closed_exact_bundle_and_preserves_mixed_or_active_context():
    """REQ-location-retention-005; planted software state/control, SQL remains separate."""
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from butlers.chronicler.location_memory_context import dispose_runtime_context

    generation, session_id, loan_id, source_generation, incarnation, decision = (
        uuid4() for _ in range(6)
    )
    context = "# Memory Context\n- native source sentinel"
    base = "Independent configured instructions stay byte exact"
    prompt = "server generated source input"
    system = base + "\n\n" + context
    body_digest = b"b" * 32
    frozen = {
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

        @asynccontextmanager
        async def acquire(self):
            yield self

        @asynccontextmanager
        async def transaction(self):
            yield

        def is_closed(self):
            return False

        async def fetchrow(self, sql, *args):
            return frozen if "context_bindings" in sql else session

        async def fetch(self, sql, *args):
            if "context_episodes" in sql:
                return []
            return [loan]

        async def fetchval(self, sql, *args):
            if sql == "SELECT current_schema()":
                return "chronicler_mem"
            if sql == "SELECT current_user":
                return "own-role-double"
            if "count(*) FROM location_catalog_copy_finished" in sql:
                return 1
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
    assert await dispose_runtime_context(runtime, generation, plan) is True
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
    sentinel = "synthetic raw private location must not be emitted"
    for exc, expected in (
        (asyncpg.UndefinedColumnError(sentinel), ("postgres", "undefined_column", "42703")),
        (PolicyUnavailableError(sentinel), ("native", "policy_unavailable", "unknown")),
        (TypeError(sentinel), ("native", "type_error", "unknown")),
    ):
        assert closed_failure(exc) == expected
        assert sentinel not in repr(closed_failure(exc))
