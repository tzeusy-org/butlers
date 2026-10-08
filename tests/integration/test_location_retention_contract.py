"""Migrated native projection/policy contract; copy-holder authority is separate.

One species groups the shared lifecycle, actual role and rollback invariants.
No hand-built schema or caller-generated grant is treated as source admission.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
from butlers.chronicler.adapters.owntracks_ssid import OwnTracksSsidPresenceAdapter
from butlers.chronicler.contracts import seed_source_registry
from butlers.chronicler.location_retention import (
    PolicyConflictError,
    PolicyUnavailableError,
    prepare_batch,
    read_local_receipt,
    read_policy,
    ready_batches,
    retention_status,
    set_policy,
    start_attempt,
)
from butlers.db import register_jsonb_codec
from butlers.location_retention import content_digest, logical_digest
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
    )


async def test_native_projection_policy_rollback_and_real_role_fences(migrated_db_url):
    """REQ-location-retention-001/002/003/006; genuine SQL, not full source authority."""
    # Bounded own migration replay before planting permanent history; this
    # never crosses adopted196/198 downgrade fences or stamps a revision.
    from alembic import command
    from butlers.migrations import _build_alembic_config, run_migrations

    core = _build_alembic_config(migrated_db_url, chains=["core"], target_schema="chronicler")
    for _ in range(2):
        command.downgrade(core, "core@core_261")
        command.upgrade(core, "core@head")
    await run_migrations(migrated_db_url, chain="core", schema="retention_second")
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "chronicler,public"},
    )
    try:
        # Separate real acquisitions observe both retained installations.
        for schema in ("chronicler", "retention_second"):
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema=$1 AND table_name IN "
                    "('location_retention_copy_receipts','location_retention_source_floors')",
                    schema,
                )
                == 2
            )
        await seed_source_registry(pool)
        policy = await read_policy(pool)
        assert policy["days"] == 30 and policy["version"] == 1
        now = datetime.now(UTC)
        born = now - timedelta(days=31)
        raw_ids = []
        for minutes in (0, 15, 90):
            raw_id = uuid4()
            raw_ids.append(raw_id)
            source = f"retention-fixture:{minutes}:{raw_id}"
            # This fixture plants a native-source row. Its accepted locator is
            # test data, not proof that an online source accepted this report.
            await pool.execute(
                """INSERT INTO connectors.owntracks_points
                   (id,idempotency_key,ts,lat,lon,endpoint_identity,recorded_at,
                    logical_source_digest,content_digest,accepted_request_id,
                    accepted_payload_digest,accepted_normalized_digest)
                   VALUES($1,$2,$3,1.31415926,103.81234567,'retention-fixture',$3,
                     $4,$5,$6,$5,$5)""",
                raw_id,
                source,
                born + timedelta(minutes=minutes),
                logical_digest(source),
                content_digest({"fixture": minutes}),
                uuid4(),
            )
        assert await pool.fetchval(
            "SELECT retention_at=ts FROM connectors.owntracks_points WHERE id=$1",
            raw_ids[0],
        )
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.RaiseError, match="immutable"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE connectors.owntracks_points SET retention_at=$2 WHERE id=$1",
                        raw_ids[0],
                        now,
                    )
        # Native writer/output/coverage/checkpoint same transaction, including
        # no-output SSID coverage and an actually open final movement carry.
        adapters = (
            OwnTracksPointAdapter(),
            OwnTracksPlaceClusterAdapter(),
            OwnTracksSsidPresenceAdapter(ssid_places={}),
        )
        for adapter in adapters:
            result = await adapter.run(pool=pool, chronicler_pool=pool)
            assert result.error is None and not result.skipped
        assert await pool.fetchval("SELECT count(*) FROM location_projection_coverage") == 9
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM location_projection_coverage WHERE disposition='pending'",
            )
            > 0
        )
        run = await start_attempt(pool)
        decision = await prepare_batch(pool, run)
        assert decision is not None  # First actually closed segment qualifies.
        local = await read_local_receipt(pool, decision)
        assert local is not None and local["removed_event_count"] == 0
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM location_evidence_tombstones WHERE decision_id=$1",
                decision,
            )
            == local["removed_event_count"]
        )
        # Unknown holder frontier preserves BOTH raw source and point events.
        # A local coarsening receipt must not masquerade as deletion/closure.
        assert await pool.fetchval("SELECT count(*) FROM point_events") == 3
        assert await ready_batches(pool) == []  # No fabricated copy-holder completion.
        assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
        status = await retention_status(pool)
        assert status["status"] == "pending" and status["deleted_count"] == 0
        assert status["blocked_count"] > 0
        # Owning policy CAS survives readback and widening cannot mutate a
        # previously frozen decision/cutoff/manifest.
        before = dict(
            await pool.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1",
                decision,
            )
        )
        updated = await set_policy(pool, days=1, expected_version=1, server_actor="test-owner")
        assert updated["version"] == 2
        with pytest.raises(PolicyConflictError):
            await set_policy(pool, days=30, expected_version=1, server_actor="test-owner")
        await set_policy(pool, days=30, expected_version=2, server_actor="test-owner")
        assert (
            dict(
                await pool.fetchrow(
                    "SELECT * FROM location_retention_plans WHERE decision_id=$1",
                    decision,
                )
            )
            == before
        )
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.RaiseError, match="immutable"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE location_retention_plans SET cutoff=$2 WHERE decision_id=$1",
                        decision,
                        now,
                    )
        # SELECT positives use actual existing roles; unexecuted/missing rows
        # cannot supply the negative. Planting above establishes row reachability.
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE butler_chronicler_rw")
                assert await conn.fetchval("SELECT current_user") == "butler_chronicler_rw"
                assert await conn.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM connectors.owntracks_points WHERE id=$1", raw_ids[0]
                        )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.fetch("SELECT * FROM connectors.owntracks_retention_tombstones")
                assert await conn.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
        # Separately acquired durable readback, including failed mutations.
        assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
        assert (await read_policy(pool))["days"] == 30
        # More than one preparation batch shares the same genuine closed
        # movement/place output. A legitimate reduction must update every
        # contributor, while arbitrary output mutation remains ineligible.
        more_ids = []
        for minute in range(301):
            raw_id = uuid4()
            more_ids.append(raw_id)
            source = f"large-native:{raw_id}"
            moment = born - timedelta(days=1) + timedelta(minutes=minute if minute < 300 else 400)
            await pool.execute(
                """INSERT INTO connectors.owntracks_points
                   (id,idempotency_key,ts,lat,lon,endpoint_identity,recorded_at,
                    logical_source_digest,content_digest,accepted_request_id,
                    accepted_payload_digest,accepted_normalized_digest)
                   VALUES($1,$2,$3,1.31415926,103.81234567,'large-native',$3,$4,$5,$6,$5,$5)""",
                raw_id,
                source,
                moment,
                logical_digest(source),
                content_digest({"native_fixture": minute}),
                uuid4(),
            )
        # These synthetic accepted locators do not prove online admission.
        for adapter in adapters:
            actual = await adapter.run(pool=pool, chronicler_pool=pool)
            assert actual.error is None and not actual.skipped
        assert (
            await pool.fetchval(
                """SELECT max(n) FROM (SELECT count(DISTINCT raw_id) n
               FROM location_projection_outputs WHERE output_kind='episode'
                 AND raw_id=ANY($1::uuid[]) GROUP BY output_id) counts""",
                more_ids,
            )
            > 256
        )
        first = await prepare_batch(pool, await start_attempt(pool))
        assert first is not None
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM location_retention_plan_rows WHERE decision_id=$1",
                first,
            )
            == 256
        )
        assert (
            await pool.fetchval(
                """SELECT count(*) FROM location_projection_privacy_transitions
               WHERE decision_id=$1 AND raw_id=ANY($2::uuid[])""",
                first,
                more_ids,
            )
            > 256
        )
        second = await prepare_batch(pool, await start_attempt(pool))
        assert second is not None and second != first
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM location_retention_plan_rows WHERE decision_id=$1",
                second,
            )
            > 0
        )
        assert await read_local_receipt(pool, second) is not None
        # Current generation integrity remains a real predicate. Plant a
        # semantic tamper before preparing a new genuinely closed source group.
        target = await pool.fetchrow(
            "SELECT id,payload FROM episodes WHERE source_name='owntracks.points' "
            "AND EXISTS(SELECT 1 FROM location_projection_outputs o "
            "WHERE o.output_id=episodes.id AND o.raw_id=ANY($1::uuid[])) LIMIT 1",
            more_ids,
        )
        original_payload = target["payload"]
        await pool.execute(
            "UPDATE episodes SET payload=payload || $2::jsonb WHERE id=$1",
            target["id"],
            {"unrelated_tamper": True},
        )
        from butlers.chronicler.location_retention import _output_generation_cohort

        async with pool.acquire() as connection:
            async with connection.transaction():
                with pytest.raises(PolicyUnavailableError, match="generation changed"):
                    await _output_generation_cohort(connection, [target["id"]], [])
        # Restore only the deliberate test mutation, not missing coverage or
        # authority, then verify current reduced generation and original lineage.
        await pool.execute(
            "UPDATE episodes SET payload=$2 WHERE id=$1", target["id"], original_payload
        )
        async with pool.acquire() as connection:
            async with connection.transaction():
                cohort = await _output_generation_cohort(connection, [target["id"]], [])
                assert cohort and all(row["original_output_revision"] is not None for row in cohort)
        assert await ready_batches(pool) == []  # Unknown foreign frontier cannot earn READY.
    finally:
        await pool.close()
