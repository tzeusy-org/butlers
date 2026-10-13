"""Integration tests for switchboard message_inbox partition migration."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import (
    create_migration_db,
    index_exists,
    migration_db_name,
    table_exists,
)

# Skip all tests if Docker is not available.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _table_relkind(db_url: str, table_name: str) -> str | None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT c.relkind "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = :t"
            ),
            {"t": table_name},
        )
        row = result.fetchone()
    engine.dispose()
    return str(row[0]) if row else None


def _function_exists(db_url: str, function_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 "
                "  FROM pg_proc p "
                "  JOIN pg_namespace n ON n.oid = p.pronamespace "
                "  WHERE n.nspname = 'public' AND p.proname = :name"
                ")"
            ),
            {"name": function_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _column_exists(db_url: str, table_name: str, column_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.columns"
                "  WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
                ")"
            ),
            {"t": table_name, "c": column_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def test_partition_migration_builds_partitioned_table_and_indexes(postgres_container):
    """Switchboard migration creates a partitioned message_inbox lifecycle table."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "message_inbox")
    assert _table_relkind(db_url, "message_inbox") == "p"

    assert index_exists(db_url, "ix_message_inbox_recent_received_at")
    assert index_exists(db_url, "ix_message_inbox_ctx_source_channel_received_at")
    assert index_exists(db_url, "ix_message_inbox_ctx_source_sender_received_at")

    assert _function_exists(db_url, "switchboard_message_inbox_ensure_partition")
    assert _function_exists(db_url, "switchboard_message_inbox_drop_expired_partitions")


@pytest.mark.pg_clock
def test_partition_maintenance_and_downgrade_round_trip(postgres_container):
    """Maintenance functions and downgrade path preserve operability."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    engine = create_engine(db_url)
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT switchboard_message_inbox_ensure_partition(:ts)"),
            {"ts": "2024-01-15T00:00:00+00:00"},
        )
        partition_name = result.scalar()
        assert partition_name == "message_inbox_p202401"

        relation_result = conn.execute(text("SELECT to_regclass('public.message_inbox_p202401')"))
        assert relation_result.scalar() == "message_inbox_p202401"

        drop_result = conn.execute(
            text(
                "SELECT switchboard_message_inbox_drop_expired_partitions("
                "CAST(:retention AS interval), CAST(:ref AS timestamptz)"
                ")"
            ),
            {
                "retention": "1 month",
                "ref": "2024-03-20T00:00:00+00:00",
            },
        )
        assert int(drop_result.scalar() or 0) >= 1

        conn.execute(
            text(
                """
                INSERT INTO message_inbox (
                    request_context,
                    raw_payload,
                    normalized_text,
                    received_at,
                    lifecycle_state,
                    schema_version,
                    processing_metadata,
                    response_summary
                )
                VALUES (
                    CAST(:request_context AS jsonb),
                    CAST(:raw_payload AS jsonb),
                    :normalized_text,
                    :received_at,
                    :lifecycle_state,
                    :schema_version,
                    CAST(:processing_metadata AS jsonb),
                    :response_summary
                )
                """
            ),
            {
                "request_context": json.dumps(
                    {
                        "request_id": "migration-test-request",
                        "received_at": "2026-02-14T00:00:00+00:00",
                        "source_channel": "telegram_bot",
                        "source_endpoint_identity": "telegram:bot",
                        "source_sender_identity": "user-123",
                    }
                ),
                "raw_payload": json.dumps({"content": "hello", "metadata": {"test": True}}),
                "normalized_text": "hello",
                "received_at": datetime.now(UTC),
                "lifecycle_state": "completed",
                "schema_version": "message_inbox.v2",
                "processing_metadata": json.dumps({"classification_duration_ms": 5}),
                "response_summary": "ok",
            },
        )
    engine.dispose()

    assert not table_exists(db_url, "message_inbox_p202401")

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@base")

    assert not table_exists(db_url, "message_inbox")
    assert not _function_exists(db_url, "switchboard_message_inbox_ensure_partition")
    assert not _function_exists(db_url, "switchboard_message_inbox_drop_expired_partitions")


@pytest.fixture(scope="module")
def delivery_databases(postgres_container):
    """Actual named target/Switchboard schemas and replayed production bootstrap GRANTs."""
    from urllib.parse import urlparse

    from butlers.migrations import run_migrations
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url,
    )

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))
    asyncio.run(run_migrations(db_url, chain="switchboard", schema="switchboard"))
    admin_url = migration_bootstrap_db_url(postgres_container, db_name)
    # Prove the ordinary runtime boundary after bootstrap re-widens table grants.
    _bootstrap_migration_prerequisites(admin_url, urlparse(db_url).username)
    return db_url, admin_url


def _delivery_envelope(segment="a", *, request_id=None):
    from butlers.core.ingestion_delivery import DeliveryBinding
    from butlers.core.utils import generate_uuid7_string

    envelope = {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": request_id or generate_uuid7_string(),
            "received_at": "2026-10-14T00:00:00Z",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "private-sender-canary",
            "subrequest_id": f"part-{segment}",
            "segment_id": segment,
        },
        "target": {"butler": "health"},
        "subrequest": {
            "subrequest_id": f"part-{segment}",
            "segment_id": segment,
            "fanout_mode": "parallel",
        },
        "input": {"prompt": "private-message-canary"},
    }
    envelope["delivery"] = DeliveryBinding.for_envelope(envelope).model_dump(mode="json")
    return envelope


async def _delivery_pool(db_url, *, role="butler_health_rw", schema="health"):
    import asyncpg

    from butlers.db import register_jsonb_codec

    async def setup(conn):
        await conn.execute(f'SET ROLE "{role}"')
        await conn.execute(f'SET search_path TO "{schema}", public')

    return await asyncpg.create_pool(
        db_url, init=register_jsonb_codec, setup=setup, min_size=1, max_size=3
    )


def test_ingestion_delivery_concurrent_acceptance_and_segment_sessions(delivery_databases):
    """REQ-ingestion-target-delivery-recovery-002: real handler, real uniqueness/lease, one session."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from butlers.core_tools._routing import register_routing_tools

    async def exercise():
        pool = await _delivery_pool(delivery_databases[0])
        tasks = set()
        triggers = []

        async def trigger(**kwargs):
            session_id = uuid.uuid4()
            triggers.append(session_id)
            await pool.execute(
                "INSERT INTO sessions (id, prompt, trigger_source, request_id, success) "
                "VALUES ($1, 'delivery test', 'route', $2, true)",
                session_id,
                kwargs["request_id"],
            )
            return SimpleNamespace(success=True, session_id=session_id)

        daemon = SimpleNamespace(
            config=SimpleNamespace(name="health", trusted_route_callers=["switchboard"]),
            db=SimpleNamespace(pool=pool),
            _route_inbox_tasks=tasks,
        )
        ctx = SimpleNamespace(
            daemon=daemon,
            pool=pool,
            spawner=SimpleNamespace(trigger=trigger),
            butler_name="health",
            route_metrics=MagicMock(),
        )
        handlers = {}
        mcp = SimpleNamespace(tool=lambda **kw: lambda fn: handlers.setdefault(kw["name"], fn))
        register_routing_tools(ctx, mcp, lambda fn: fn)
        route = handlers["route.execute"]
        first = _delivery_envelope()
        try:
            responses = await asyncio.gather(route(**first), route(**first))
            assert {r["status"] for r in responses} == {"accepted"}
            assert len({r["receipt_id"] for r in responses}) == 1
            assert sum(r.get("dedup", False) for r in responses) == 1
            if tasks:
                await asyncio.gather(*list(tasks))
            assert len(triggers) == 1
            second = _delivery_envelope("b", request_id=first["request_context"]["request_id"])
            response = await route(**second)
            assert response["receipt_id"] != responses[0]["receipt_id"]
            if tasks:
                await asyncio.gather(*list(tasks))
            assert len(triggers) == 2
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM route_inbox WHERE id = ANY($1::uuid[])",
                    [uuid.UUID(responses[0]["receipt_id"]), uuid.UUID(response["receipt_id"])],
                )
                == 2
            )
            from copy import deepcopy

            from butlers.core.ingestion_delivery import DeliveryBinding

            changed_work = deepcopy(first)
            changed_work["input"]["prompt"] = "different private message"
            changed_work["delivery"] = DeliveryBinding.for_envelope(changed_work).model_dump(
                mode="json"
            )
            conflict = await route(**changed_work)
            assert conflict["status"] == "error" and "receipt_id" not in conflict
            assert len(triggers) == 2
            # Read-only receipt lookup does not use retained message content.
            first["delivery"]["operation"] = "lookup"
            first["input"]["prompt"] = "receipt lookup"
            receipt = await route(**first)
            assert receipt["receipt_id"] == responses[0]["receipt_id"]
            assert len(triggers) == 2
            assert "private" not in json.dumps(receipt)
            first["delivery"]["payload_digest"] = "0" * 64
            conflict = await route(**first)
            assert conflict["status"] == "error" and "receipt_id" not in conflict
            first["delivery"]["target_butler"] = "finance"
            conflict = await route(**first)
            assert conflict["status"] == "error" and "receipt_id" not in conflict
        finally:
            if tasks:
                await asyncio.gather(*list(tasks))
            await pool.close()

    asyncio.run(exercise())


def test_ingestion_receipt_survives_pruning_rerun_and_rollback(delivery_databases):
    """REQ-ingestion-target-delivery-recovery-002: ledger survives every payload/clock fence loss."""
    import uuid
    from copy import deepcopy

    import asyncpg

    from butlers.core.ingestion_delivery import DeliveryBinding, validate_delivery
    from butlers.core.route_inbox import (
        RouteAcceptanceConflict,
        route_inbox_accept_delivery,
        route_inbox_lookup_acceptance,
    )
    from butlers.migrations import _build_alembic_config

    envelope = _delivery_envelope()
    binding = validate_delivery(
        envelope, receiving_butler="health", trusted_callers=["switchboard"]
    )
    db_url, admin_url = delivery_databases

    async def accept():
        pool = await _delivery_pool(db_url)
        try:
            receipt = await route_inbox_accept_delivery(
                pool, binding=binding, route_envelope=envelope
            )
            # Server controls are exercised from a separate acquisition.
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM route_inbox WHERE id=$1", receipt.receipt_id
                )
                == 1
            )
            return receipt
        finally:
            await pool.close()

    original = asyncio.run(accept())
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.ingestion_events "
                    "(id,source_channel,source_provider,source_endpoint_identity,source_sender_identity,"
                    "external_event_id,dedupe_key,dedupe_strategy,ingestion_tier,policy_tier,status) "
                    "VALUES (:id,'telegram_bot','telegram','switchboard','private-sender-canary',"
                    "CAST(:id AS text),CAST(:id AS text),'external_event_id','full','full','ingested')"
                ),
                {"id": binding.ingestion_event_id},
            )
            conn.execute(
                text(
                    "UPDATE public.ingestion_events SET received_at=now()-interval '50 years' WHERE id=:id"
                ),
                {"id": binding.ingestion_event_id},
            )
            conn.execute(
                text("DELETE FROM public.ingestion_events WHERE id=:id"),
                {"id": binding.ingestion_event_id},
            )
            conn.execute(
                text("DELETE FROM health.route_inbox WHERE id=:id"), {"id": original.receipt_id}
            )
            before = conn.execute(
                text("SELECT * FROM health.route_acceptance_ledger WHERE receipt_id=:id"),
                {"id": original.receipt_id},
            ).one()
            for mutation in (
                "UPDATE health.route_acceptance_ledger SET accepted_at=now() WHERE receipt_id=:id",
                "DELETE FROM health.route_acceptance_ledger WHERE receipt_id=:id",
                "TRUNCATE health.route_acceptance_ledger",
            ):
                with pytest.raises(Exception, match="append-only"):
                    conn.execute(text(mutation), {"id": original.receipt_id})
        config = _build_alembic_config(db_url, ["core"], target_schema="health")
        command.downgrade(config, "core@core_265")

        # Old route.v1 contract lacks delivery and refuses the envelope; even an
        # old raw insertion cannot recreate an inbox under a fresh UUID.
        async def old_insertion():
            pool = await _delivery_pool(db_url)
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.execute(
                        "INSERT INTO route_inbox (id,route_envelope) VALUES ($1,$2)",
                        uuid.uuid4(),
                        envelope,
                    )
            finally:
                await pool.close()

        asyncio.run(old_insertion())
        command.upgrade(config, "core@core_268")
        # Replay the installer itself rather than a no-op Alembic head upgrade.
        with engine.begin() as conn:
            import importlib.util
            from pathlib import Path

            from alembic.operations import Operations
            from alembic.runtime.migration import MigrationContext

            path = (
                Path(__file__).resolve().parents[2]
                / "alembic/versions/core/core_268_ingestion_route_acceptance.py"
            )
            module_spec = importlib.util.spec_from_file_location("acceptance_install", path)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            conn.execute(text("SET search_path TO health,public"))
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            after = conn.execute(
                text("SELECT * FROM health.route_acceptance_ledger WHERE receipt_id=:id"),
                {"id": original.receipt_id},
            ).one()
            assert before == after
    finally:
        engine.dispose()

    async def verify():
        pool = await _delivery_pool(db_url)
        try:
            repeated = await route_inbox_accept_delivery(
                pool, binding=binding, route_envelope=envelope
            )
            assert repeated.receipt_id == original.receipt_id and not repeated.created
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM route_inbox WHERE id=$1", original.receipt_id
                )
                == 0
            )
            async with pool.acquire() as conn:
                receipt = await route_inbox_lookup_acceptance(conn, binding)
                assert receipt.receipt_id == original.receipt_id
                changed = DeliveryBinding.model_validate(
                    {**binding.model_dump(), "payload_digest": "f" * 64}
                )
                with pytest.raises(RouteAcceptanceConflict):
                    await route_inbox_lookup_acceptance(conn, changed)
                # First ledger insert rolls back if its inbox insert fails.
                bad = deepcopy(envelope)
                bad["delivery"]["segment_id"] = "wrong"
                new = binding.model_copy(update={"segment_id": "rollback"})
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await route_inbox_accept_delivery(pool, binding=new, route_envelope=bad)
                assert await route_inbox_lookup_acceptance(conn, new) is None
        finally:
            await pool.close()

    asyncio.run(verify())


def test_ingestion_intent_closed_state_and_runtime_authority(delivery_databases):
    """REQ-ingestion-target-delivery-recovery-003/-008: closed states and narrow real roles."""
    import asyncpg

    from butlers.core.route_inbox import route_inbox_acceptance_capacity
    from butlers.migrations import _build_alembic_config

    envelope = _delivery_envelope()
    event_id = envelope["delivery"]["ingestion_event_id"]
    db_url, admin_url = delivery_databases

    async def exercise():
        from butlers.core.ingestion_delivery import validate_delivery
        from butlers.core.route_inbox import route_inbox_accept_delivery

        target_pool = await _delivery_pool(db_url)
        try:
            binding = validate_delivery(
                envelope, receiving_butler="health", trusted_callers=["switchboard"]
            )
            acceptance = await route_inbox_accept_delivery(
                target_pool, binding=binding, route_envelope=envelope
            )
        finally:
            await target_pool.close()
        pool = await _delivery_pool(db_url, role="butler_switchboard_rw", schema="switchboard")
        try:
            await pool.execute(
                "INSERT INTO ingestion_target_plans (ingestion_event_id,plan_digest,classified_plan) "
                "VALUES ($1::text::uuid,$2,$3)",
                event_id,
                "a" * 64,
                {"targets": ["health"]},
            )
            await pool.execute(
                "INSERT INTO ingestion_target_intents "
                "(ingestion_event_id,target_butler,segment_id,subrequest_id,payload_digest,route_payload) "
                "VALUES ($1::text::uuid,'health','a','part-a',$2,$3)",
                event_id,
                envelope["delivery"]["payload_digest"],
                envelope,
            )
            for update in (
                "state='invented'",
                "reason='private-exception-canary'",
                "transport_outcome='made_up'",
                "state='accepted'",
                "state='attempting'",
                "payload_digest=repeat('f',64)",
                "segment_id='changed'",
                "route_payload='{}'::jsonb",
                "max_attempts=9",
            ):
                with pytest.raises(
                    (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)
                ):
                    await pool.execute(
                        "UPDATE ingestion_target_intents SET "
                        + update
                        + " WHERE ingestion_event_id=$1::text::uuid",
                        event_id,
                    )
            await pool.execute(
                "UPDATE ingestion_target_intents SET state='accepted',receipt_id=$2 "
                "WHERE ingestion_event_id=$1::text::uuid",
                event_id,
                acceptance.receipt_id,
            )
            for update in ("state='pending',receipt_id=NULL", "receipt_id=gen_random_uuid()"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.execute(
                        "UPDATE ingestion_target_intents SET "
                        + update
                        + " WHERE ingestion_event_id=$1::text::uuid",
                        event_id,
                    )
            for table in ("ingestion_target_plans", "ingestion_target_intents"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await pool.execute(
                        f"DELETE FROM {table} WHERE ingestion_event_id=$1::text::uuid", event_id
                    )
            await pool.execute(
                "UPDATE ingestion_target_intents SET route_payload=NULL WHERE ingestion_event_id=$1::text::uuid",
                event_id,
            )
            await pool.execute(
                "UPDATE ingestion_target_plans SET classified_plan=NULL WHERE ingestion_event_id=$1::text::uuid",
                event_id,
            )
        finally:
            await pool.close()
        own = await _delivery_pool(db_url)
        try:
            capacity = await route_inbox_acceptance_capacity(own)
            assert set(capacity) == {"bytes", "estimated_rows"} and capacity["bytes"] > 0
            before_count = await own.fetchval("SELECT count(*) FROM route_acceptance_ledger")
            for target, digest in (("finance", "a" * 64), ("health", "private-reason-canary")):
                with pytest.raises(
                    (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError)
                ):
                    await own.execute(
                        "INSERT INTO route_acceptance_ledger "
                        "(ingestion_event_id,target_butler,segment_id,payload_digest,receipt_id) "
                        "VALUES ($1::text::uuid,$2,'runtime-role-proof',$3,gen_random_uuid())",
                        event_id,
                        target,
                        digest,
                    )
            assert (
                await own.fetchval("SELECT count(*) FROM route_acceptance_ledger") == before_count
            )
            # Bootstrap grants TRIGGER/UPDATE/DELETE; runtime RLS and immutable
            # triggers still prevent runtime mutation of target evidence.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await own.execute(
                    "UPDATE route_acceptance_ledger SET payload_digest=repeat('f',64)"
                )
        finally:
            await own.close()
        foreign = await _delivery_pool(admin_url, role="butler_finance_rw")
        try:
            for sql in (
                "SELECT * FROM health.route_acceptance_ledger",
                "INSERT INTO health.route_acceptance_ledger SELECT * FROM health.route_acceptance_ledger",
                "UPDATE switchboard.ingestion_target_intents SET reason='worker_lost'",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await foreign.execute(sql)
        finally:
            await foreign.close()
        return acceptance.receipt_id

    original_receipt = asyncio.run(exercise())
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_044")
    command.upgrade(config, "switchboard@sw_045")
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT state,receipt_id,payload_digest,route_payload FROM switchboard.ingestion_target_intents WHERE ingestion_event_id=:id"
                ),
                {"id": event_id},
            ).one()
            assert (
                row.state == "accepted"
                and row.receipt_id == original_receipt
                and row.payload_digest == envelope["delivery"]["payload_digest"]
            )
            assert row.route_payload is None
    finally:
        engine.dispose()
