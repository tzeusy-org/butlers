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


@pytest.fixture(scope="module")
def completion_db_url(postgres_container):
    """Provision the separate healthy DB before entering the async test loop."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
    )


async def test_native_projection_policy_rollback_and_real_role_fences(
    migrated_db_url, postgres_container, completion_db_url
):
    """REQ-location-retention-001/002/003/006; genuine SQL, not full source authority."""
    # Bounded own migration replay before planting permanent history; this
    # never crosses adopted196/198 downgrade fences or stamps a revision.
    from alembic import command
    from butlers.migrations import _build_alembic_config, run_migrations

    core = _build_alembic_config(migrated_db_url, chains=["core"], target_schema="chronicler")
    for _ in range(2):
        command.downgrade(core, "core@core_265")
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
        # Actual retained identity reads under the existing owning runtime
        # role, distinct from the migration creator. Catalog names/IDs never
        # enter diagnostics. Deliberate hostile mutations roll back; this is
        # not a fixture patch to conceal a failed migration.
        import importlib.util
        from pathlib import Path
        from types import SimpleNamespace

        import sqlalchemy as sa

        source = Path(__file__).resolve().parents[2] / (
            "alembic/versions/core/core_264_owntracks_retention_lineage.py"
        )
        spec = importlib.util.spec_from_file_location("_retention_identity_control", source)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = sa.create_engine(migrated_db_url)
        try:
            with engine.connect() as catalog:
                catalog.execute(sa.text("SET search_path TO chronicler,public"))
                catalog.commit()
                from alembic.migration import MigrationContext
                from alembic.operations import Operations

                migration.op = Operations(MigrationContext.configure(catalog))
                with catalog.begin():
                    catalog.execute(sa.text("SET LOCAL ROLE butler_chronicler_rw"))
                    migration._validate_local_tables("chronicler")
                    facts = catalog.execute(
                        sa.text("""
                        SELECT jsonb_build_object(
                          'regular_table',c.relkind='r',
                          'canonical_owner',c.relowner=s.relowner,
                          'current_owner',pg_get_userbyid(c.relowner)=current_user,
                          'session_is_current',session_user=current_user,
                          'can_set_canonical_owner',pg_has_role(current_user,s.relowner,'SET'))
                        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        JOIN pg_class s ON s.relnamespace=n.oid AND s.relname='state'
                        WHERE n.nspname='chronicler'
                          AND c.relname='location_retention_copy_receipts'
                    """)
                    ).scalar_one()
                    assert facts["regular_table"] and facts["canonical_owner"]
                    assert facts["current_owner"] is False
                    print(
                        "RETENTION_MANAGED_OWNER " + __import__("json").dumps(facts, sort_keys=True)
                    )
                # Exact newly added artifact table is existing replay history,
                # not a new relation whose owner can be silently transferred.
                transaction = catalog.begin()
                try:
                    catalog.execute(
                        sa.text(
                            "ALTER TABLE location_runtime_context_artifacts "
                            "OWNER TO butler_chronicler_rw"
                        )
                    )
                    migration._create_local_tables("chronicler", "SELECT 1")
                    with pytest.raises(RuntimeError, match="identity differs"):
                        migration._validate_local_tables("chronicler")
                finally:
                    transaction.rollback()
                with catalog.begin():
                    migration._validate_local_tables("chronicler")
                for statements, reason in (
                    (
                        [
                            "ALTER TABLE location_retention_copy_receipts OWNER TO butler_chronicler_rw"
                        ],
                        "identity differs",
                    ),
                    (
                        [
                            "ALTER TABLE location_retention_copy_receipts RENAME TO retained_real_copy",
                            "CREATE VIEW location_retention_copy_receipts AS SELECT * FROM retained_real_copy",
                        ],
                        "identity differs",
                    ),
                    (
                        ["ALTER TABLE location_retention_copy_receipts ADD COLUMN unrelated TEXT"],
                        "shape differs",
                    ),
                    (
                        [
                            "ALTER TABLE location_retention_copy_receipts DROP CONSTRAINT "
                            "location_retention_copy_receipts_forgotten_count_check"
                        ],
                        "shape differs",
                    ),
                ):
                    transaction = catalog.begin()
                    try:
                        for statement in statements:
                            catalog.execute(sa.text(statement))
                        with pytest.raises(RuntimeError, match=reason):
                            migration._validate_local_tables("chronicler")
                    finally:
                        transaction.rollback()
                    with catalog.begin():
                        migration._validate_local_tables("chronicler")
        finally:
            engine.dispose()
        # The native separate pool observes the committed original relations.
        assert await pool.fetchval(
            "SELECT c.relkind='r' AND c.relowner=s.relowner "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_class s ON s.relnamespace=n.oid AND s.relname='state' "
            "WHERE n.nspname='chronicler' AND c.relname='location_retention_copy_receipts'"
        )
        async with pool.acquire() as artifact_conn:
            context_id, native_session, artifact_id = uuid4(), uuid4(), uuid4()
            async with artifact_conn.transaction():
                await artifact_conn.execute(
                    "INSERT INTO location_runtime_context_intents "
                    "(input_generation,receiving_session) VALUES($1,$2)",
                    context_id,
                    native_session,
                )
                await artifact_conn.execute(
                    "INSERT INTO location_runtime_context_artifacts "
                    "(artifact_generation,input_generation,memory_table,artifact_id,body_digest) "
                    "VALUES($1,$2,'rules',$3,$4)",
                    uuid4(),
                    context_id,
                    artifact_id,
                    b"a" * 32,
                )
            for mutation in (
                "UPDATE location_runtime_context_artifacts SET body_digest=$2 WHERE artifact_id=$1",
                "DELETE FROM location_runtime_context_artifacts WHERE artifact_id=$1 "
                "AND body_digest<>$2",
            ):
                with pytest.raises(asyncpg.RaiseError, match="permanent"):
                    async with artifact_conn.transaction():
                        await artifact_conn.execute(mutation, artifact_id, b"b" * 32)
        async with pool.acquire() as artifact_readback:
            assert (
                await artifact_readback.fetchval(
                    "SELECT body_digest=$2 FROM location_runtime_context_artifacts WHERE artifact_id=$1",
                    artifact_id,
                    b"a" * 32,
                )
                is True
            )
        async with pool.acquire() as content_catalog:
            assert (
                await content_catalog.fetchval(
                    "SELECT is_nullable='YES' FROM information_schema.columns "
                    "WHERE table_schema='chronicler' AND table_name='location_runtime_context_artifacts' "
                    "AND column_name='content_digest'"
                )
                is True
            )
            # No default/backfill fabricates a new witness for the old planted history.
            assert (
                await content_catalog.fetchval(
                    "SELECT content_digest IS NULL FROM location_runtime_context_artifacts "
                    "WHERE artifact_id=$1",
                    artifact_id,
                )
                is True
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
            moment = born + timedelta(minutes=minutes)
            native_payload = {
                "_type": "location",
                "tst": int(moment.timestamp()),
                "lat": 1.31415926,
                "lon": 103.81234567,
            }
            source = f"owntracks:retention-fixture:{native_payload['tst']}:location"
            input_generation = await _plant_closed_input_engine_history(
                pool, logical_digest(source), content_digest(native_payload)
            )
            # This fixture plants a native-source row. Its accepted locator is
            # test data, not proof that an online source accepted this report.
            await pool.execute(
                """INSERT INTO connectors.owntracks_points
                   (id,idempotency_key,ts,lat,lon,endpoint_identity,recorded_at,
                    logical_source_digest,content_digest,accepted_request_id,
                    accepted_payload_digest,accepted_normalized_digest,raw_payload,source_input_generation)
                   VALUES($1,$2,$3,1.31415926,103.81234567,'retention-fixture',$3,
                     $4,$5,$6,$5,$5,$7,$8)""",
                raw_id,
                source,
                born + timedelta(minutes=minutes),
                logical_digest(source),
                content_digest(native_payload),
                uuid4(),
                native_payload,
                input_generation,
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
            assert result.error is None and not result.skipped, tuple(
                warning
                for warning in result.warnings
                if __import__("re").fullmatch(
                    r"location_projection_diagnostic:[a-z_]+:(?:postgres|native):(?:[A-Z0-9]{5}|unknown)"
                    r"|location_projection_class:[a-z_]+",
                    warning,
                )
            )
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
        # Separate acquisition witnesses the actual committed counters. The
        # planted native source has overdue rows and both closed/open coverage.
        async with pool.acquire() as committed_counts:
            counts = await committed_counts.fetchrow(
                "SELECT overdue_count,blocked_count,holder_pending_count "
                "FROM location_retention_runs WHERE run_id=$1",
                run,
            )
        assert counts["overdue_count"] == 3
        assert 0 < counts["blocked_count"] < 3
        assert counts["holder_pending_count"] == 3 - counts["blocked_count"]
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
        for replay_bootstrap in (False, True):
            if replay_bootstrap:
                from urllib.parse import urlparse

                from butlers.testing.migration import (
                    init_db_sql_for_dbapi,
                    migration_bootstrap_db_url,
                )

                parsed = urlparse(migrated_db_url)
                assert parsed.username is not None
                # Established disposable bootstrap identity, never the
                # ordinary runtime role or a fixture patch to a catalog ACL.
                bootstrap = sa.create_engine(
                    migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
                    isolation_level="AUTOCOMMIT",
                )
                raw_connection = bootstrap.raw_connection()
                try:
                    raw_connection.autocommit = True
                    with raw_connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config('butlers.connecting_user', %s, false)",
                            (parsed.username,),
                        )
                        cursor.execute(init_db_sql_for_dbapi())
                finally:
                    raw_connection.close()
                    bootstrap.dispose()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL ROLE butler_chronicler_rw")
                    assert await conn.fetchval("SELECT current_user") == "butler_chronicler_rw"
                    assert (
                        await conn.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
                    )
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with conn.transaction():
                            await conn.execute(
                                "DELETE FROM connectors.owntracks_points WHERE id=$1", raw_ids[0]
                            )
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        async with conn.transaction():
                            await conn.fetch(
                                "SELECT * FROM connectors.owntracks_retention_tombstones"
                            )
                    assert (
                        await conn.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 3
                    )
                    assert (
                        await conn.fetchval(
                            "SELECT has_table_privilege('connectors.owntracks_retention_batches','SELECT')"
                        )
                        is True
                    )
                    assert (
                        await conn.fetchval(
                            "SELECT has_table_privilege('connectors.owntracks_retention_batch_rows','SELECT')"
                        )
                        is True
                    )
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
            moment = born - timedelta(days=1) + timedelta(minutes=minute if minute < 300 else 400)
            native_payload = {
                "_type": "location",
                "tst": int(moment.timestamp()),
                "lat": 1.31415926,
                "lon": 103.81234567,
            }
            source = f"owntracks:large-native:{native_payload['tst']}:location"
            input_generation = await _plant_closed_input_engine_history(
                pool, logical_digest(source), content_digest(native_payload)
            )
            await pool.execute(
                """INSERT INTO connectors.owntracks_points
                   (id,idempotency_key,ts,lat,lon,endpoint_identity,recorded_at,
                    logical_source_digest,content_digest,accepted_request_id,
                    accepted_payload_digest,accepted_normalized_digest,raw_payload,source_input_generation)
                   VALUES($1,$2,$3,1.31415926,103.81234567,'large-native',$3,$4,$5,$6,$5,$5,$7,$8)""",
                raw_id,
                source,
                moment,
                logical_digest(source),
                content_digest(native_payload),
                uuid4(),
                native_payload,
                input_generation,
            )
        # These synthetic accepted locators do not prove online admission.
        for adapter in adapters:
            actual = await adapter.run(pool=pool, chronicler_pool=pool)
            assert actual.error is None and not actual.skipped, tuple(
                warning
                for warning in actual.warnings
                if __import__("re").fullmatch(
                    r"location_projection_diagnostic:[a-z_]+:(?:postgres|native):(?:[A-Z0-9]{5}|unknown)"
                    r"|location_projection_class:[a-z_]+",
                    warning,
                )
            )
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
            "SELECT e.id,e.payload FROM episodes e WHERE e.source_name='owntracks.points' "
            "AND EXISTS(SELECT 1 FROM location_projection_outputs o "
            "JOIN location_projection_coverage c "
            "USING(raw_id,source_revision,adapter_name,mapping_revision) "
            "JOIN location_projection_privacy_transitions t "
            "USING(raw_id,source_revision,adapter_name,mapping_revision) "
            "WHERE o.output_kind='episode' AND o.output_id=e.id "
            "AND o.raw_id=ANY($1::uuid[]) AND c.disposition='complete' "
            "AND t.phase='coarsen' AND t.decision_id=ANY($2::uuid[])) "
            "ORDER BY e.id LIMIT 1",
            more_ids,
            [first, second],
        )
        assert target is not None
        from butlers.chronicler.location_retention import _output_generation_cohort

        # The healthy complete source must actually reach the generation
        # predicate before corruption; an open/no-contributor selection is
        # not a tamper control, even if a later generic refusal were added.
        async with pool.acquire() as connection:
            async with connection.transaction():
                healthy_cohort = await _output_generation_cohort(connection, [target["id"]], [])
                assert healthy_cohort
        original_payload = target["payload"]
        await pool.execute(
            "UPDATE episodes SET payload=payload || $2::jsonb WHERE id=$1",
            target["id"],
            {"unrelated_tamper": True},
        )
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
                assert cohort == healthy_cohort
        assert await ready_batches(pool) == []  # Unknown foreign frontier cannot earn READY.

        # Genuine migrated engine/role positive, distinct from online source
        # authentication: the existing fixture's accepted IDs and the planted
        # remote observation below are explicitly synthetic. They do not prove
        # a registered source accepted/disposed this report, nor full delivery.
        from butlers.chronicler.location_retention import (
            dispose_ready_point_evidence,
            issue_ready_grant,
            seal_native_frontier,
        )
        from roster.chronicler.modules import ChroniclerModule

        async def owning_connection(connection):
            await register_jsonb_codec(connection)
            await connection.execute("SET ROLE butler_chronicler_rw")

        owning = await asyncpg.create_pool(
            migrated_db_url,
            min_size=1,
            max_size=2,
            init=owning_connection,
            server_settings={"search_path": "chronicler,public"},
        )
        module = ChroniclerModule()
        await module.on_startup(None, SimpleNamespace(schema="chronicler", pool=owning))
        try:
            assert await owning.fetchval("SELECT current_user") == "butler_chronicler_rw"
            # Missing remote closure actually preserves planted source and
            # point bodies, beside the closed-cohort engine positive below.
            before_points = await pool.fetchval("SELECT count(*) FROM point_events")
            assert await seal_native_frontier(owning, decision) is None
            assert await dispose_ready_point_evidence(owning, decision) is None
            assert await pool.fetchval("SELECT count(*) FROM point_events") == before_points
            assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
            selected = await pool.fetch(
                "SELECT DISTINCT output_id FROM location_retention_plan_outputs "
                "WHERE decision_id=$1 AND output_kind='point_event'",
                decision,
            )
            assert selected  # Positive target is genuinely present before DELETE.
            receipt = uuid4()
            await pool.execute(
                "INSERT INTO location_retention_holder_receipts "
                "(decision_id,owning_butler,holder_kind,holder_generation,source_digest,receipt_id) "
                "SELECT decision_id,'switchboard','switchboard_skipped',decision_id,"
                "manifest_digest,$2 FROM location_retention_plans WHERE decision_id=$1",
                decision,
                receipt,
            )
            from butlers.chronicler.location_retention import filtered_copy_plans
            from butlers.connectors.owntracks_copy_retention import (
                FilteredCopyPlan,
                prepare_filtered_copies,
            )

            async def copy_connection(connection):
                await register_jsonb_codec(connection)
                await connection.execute("SET ROLE connector_writer")

            copy_writer = await asyncpg.create_pool(
                migrated_db_url, min_size=1, max_size=2, init=copy_connection
            )
            try:
                copies = [
                    FilteredCopyPlan.model_validate(w) for w in await filtered_copy_plans(owning)
                ]
                copy_plan = next(w for w in copies if w.decision_id == decision)
                assert await seal_native_frontier(owning, decision) is None
                await prepare_filtered_copies(copy_writer, copy_plan)
            finally:
                await copy_writer.close()
            frontier = await seal_native_frontier(owning, decision)
            assert frontier is not None
            assert (
                await pool.fetchval(
                    "SELECT frontier_generation FROM location_retention_frontiers WHERE decision_id=$1",
                    decision,
                )
                == frontier
            )
            await _assert_late_opaque_holder_refusal(
                pool, owning, decision, frontier, before_points
            )
            disposed = await dispose_ready_point_evidence(owning, decision)
            assert disposed is not None
            assert (
                await pool.fetchval(
                    "SELECT receipt_id FROM location_retention_disposal_receipts WHERE decision_id=$1",
                    decision,
                )
                == disposed
            )
            assert await pool.fetchval("SELECT count(*) FROM point_events") == before_points - len(
                selected
            )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM point_events WHERE id=ANY($1::uuid[])",
                    [row["output_id"] for row in selected],
                )
                == 0
            )
            assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
            assert await pool.fetchval(
                "SELECT count(*) FROM location_evidence_tombstones WHERE decision_id=$1",
                decision,
            ) == len(selected)
            grant = await issue_ready_grant(owning, decision, disposed)
            assert (
                await pool.fetchval(
                    "SELECT grant_id FROM location_retention_grants WHERE decision_id=$1",
                    decision,
                )
                == grant
            )
            assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
            # Continue through the actual connector SQL engine under its
            # existing role, not through the migration creator. The planted
            # remote observation above remains synthetic: this proves SQL
            # disposal/rollback/readback, not registered online admission.
            from butlers.chronicler.location_retention import reconcile_raw_batches
            from butlers.connectors.owntracks_forgetting import (
                ForgettingRefusedError,
                ReadyGrant,
                forget_ready_batch,
                read_batch,
            )

            wire = await ready_batches(owning)
            frozen = ReadyGrant.model_validate(
                next(item for item in wire if item["decision_id"] == str(decision))
            )
            frozen.check_manifest()
            frozen_ids = [row.raw_id for row in frozen.rows]
            assert 1 <= len(frozen_ids) <= 256
            with pytest.raises(ForgettingRefusedError, match="writer_identity_mismatch"):
                await forget_ready_batch(owning, frozen)
            assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304

            async def connector_connection(connection):
                await register_jsonb_codec(connection)
                await connection.execute("SET ROLE connector_writer")

            connector = await asyncpg.create_pool(
                migrated_db_url, min_size=1, max_size=2, init=connector_connection
            )
            try:
                assert await connector.fetchval("SELECT current_user") == "connector_writer"
                expired = frozen.model_copy(
                    update={"lease_until": datetime.now(UTC) - timedelta(seconds=1)}
                )
                with pytest.raises(ForgettingRefusedError, match="claim_lease_expired"):
                    await forget_ready_batch(connector, expired)
                # The lease is checked after candidate tombstone inserts:
                # refusal must roll those back, not merely preserve raw rows.
                async with pool.acquire() as committed:
                    assert await committed.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_points WHERE id=ANY($1::uuid[])",
                        frozen_ids,
                    ) == len(frozen_ids)
                    assert (
                        await committed.fetchval(
                            "SELECT count(*) FROM connectors.owntracks_retention_tombstones "
                            "WHERE batch_id=$1",
                            frozen.batch_id,
                        )
                        == 0
                    )
                    assert (
                        await committed.fetchval(
                            "SELECT count(*) FROM connectors.owntracks_retention_batches "
                            "WHERE batch_id=$1",
                            frozen.batch_id,
                        )
                        == 0
                    )
                renewed = ReadyGrant.model_validate(
                    next(
                        item
                        for item in await ready_batches(owning)
                        if item["decision_id"] == str(decision)
                    )
                )
                assert renewed.batch_id == frozen.batch_id and renewed.rows == frozen.rows
                assert renewed.lease_version > frozen.lease_version
                result = await forget_ready_batch(connector, renewed)
                assert result["deleted_count"] == len(frozen_ids)
                assert result["already_forgotten_count"] == 0
                assert {row["raw_id"] for row in result["rows"]} == set(frozen_ids)
                async with pool.acquire() as committed:
                    assert (
                        await committed.fetchval(
                            "SELECT count(*) FROM connectors.owntracks_points "
                            "WHERE id=ANY($1::uuid[])",
                            frozen_ids,
                        )
                        == 0
                    )
                    assert await committed.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_points"
                    ) == 304 - len(frozen_ids)
                    assert await committed.fetchval(
                        "SELECT count(*) FROM connectors.owntracks_retention_tombstones "
                        "WHERE batch_id=$1",
                        frozen.batch_id,
                    ) == len(frozen_ids)
                assert await read_batch(connector, renewed.batch_id) == result
                # ACK loss/restart resumes the exact immutable batch even
                # after its old lease expires; it cannot choose a new set.
                assert await forget_ready_batch(connector, expired) == result
                with pytest.raises(ForgettingRefusedError, match="receipt_identity_mismatch"):
                    await forget_ready_batch(
                        connector, renewed.model_copy(update={"grant_id": uuid4()})
                    )
                await reconcile_raw_batches(owning)
                await reconcile_raw_batches(owning)
                async with pool.acquire() as committed:
                    assert (
                        await committed.fetchval(
                            "SELECT state FROM location_retention_plans WHERE decision_id=$1",
                            decision,
                        )
                        == "complete"
                    )
                    assert await committed.fetchval(
                        "SELECT deleted_count FROM location_retention_runs WHERE run_id=$1", run
                    ) == len(frozen_ids)
                from butlers.chronicler.location_retention import complete_native_attempt

                incomplete_run = await start_attempt(owning)
                await complete_native_attempt(owning, incomplete_run)
                async with pool.acquire() as committed:
                    incomplete = await committed.fetchrow(
                        "SELECT * FROM location_retention_runs WHERE run_id=$1", incomplete_run
                    )
                assert incomplete["status"] == "unknown" and incomplete["completion_at"] is not None
                assert incomplete["overdue_count"] == 304 - len(frozen_ids)
                assert incomplete["holder_pending_count"] == (
                    incomplete["overdue_count"] - incomplete["blocked_count"]
                )
                assert (await retention_status(owning))["unknown_count"] is None
            finally:
                await connector.close()
        finally:
            await module.on_shutdown()
            await owning.close()
    finally:
        await pool.close()
    await _assert_native_attempt_completion(completion_db_url)


async def _assert_native_attempt_completion(url):
    """Actual migrated producer/role/COMMIT; planted remote receipt is not online proof."""
    from types import SimpleNamespace

    from butlers.chronicler.location_retention import (
        complete_native_attempt,
        dispose_ready_point_evidence,
        issue_ready_grant,
        reconcile_raw_batches,
        seal_native_frontier,
    )
    from butlers.connectors.owntracks_forgetting import ReadyGrant, forget_ready_batch
    from roster.chronicler.modules import ChroniclerModule

    # A separate genuinely migrated healthy database: never delete the other
    # species' incomplete permanent history to manufacture a zero inventory.
    # The synchronous fixture provisions this actual fresh chain before the
    # async node starts; no nested asyncio.run occurs inside this helper.
    creator = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": "chronicler,public"},
    )

    async def own_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    async def connector_connection(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE connector_writer")

    own = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=2,
        init=own_connection,
        server_settings={"search_path": "chronicler,public"},
    )
    connector = await asyncpg.create_pool(url, min_size=1, max_size=2, init=connector_connection)
    module = ChroniclerModule()
    await module.on_startup(None, SimpleNamespace(schema="chronicler", pool=own))
    try:
        empty = await start_attempt(own)
        await complete_native_attempt(own, empty)
        assert (await retention_status(own))["status"] == "unknown"
        assert (await retention_status(own))["unknown_count"] is None
        assert await creator.fetchval("SELECT count(*) FROM location_retention_plans") == 0
        await _assert_native_input_producer(creator, own, connector)
        await seed_source_registry(creator)
        now = datetime.now(UTC)
        # Last fresh boundary closes the old native movement/place carry;
        # its still-unexpired point is preserved, not hidden/deleted by a test.
        for minutes in (0, 15, 90, 31 * 1440):
            raw, moment = uuid4(), now - timedelta(days=31) + timedelta(minutes=minutes)
            native_payload = {
                "_type": "location",
                "tst": int(moment.timestamp()),
                "lat": 1.31415926,
                "lon": 103.81234567,
            }
            source = f"owntracks:completion-fixture:{native_payload['tst']}:location"
            input_generation = await _plant_closed_input_engine_history(
                creator, logical_digest(source), content_digest(native_payload)
            )
            digest = content_digest(native_payload)
            await creator.execute(
                "INSERT INTO connectors.owntracks_points "
                "(id,idempotency_key,ts,lat,lon,endpoint_identity,recorded_at,"
                "logical_source_digest,content_digest,accepted_request_id,"
                "accepted_payload_digest,accepted_normalized_digest,raw_payload,source_input_generation) "
                "VALUES($1,$2,$3,1.31415926,103.81234567,'completion-fixture',$3,$4,$5,$6,$5,$5,$7,$8)",
                raw,
                source,
                moment,
                logical_digest(source),
                digest,
                uuid4(),
                native_payload,
                input_generation,
            )
        for adapter in (
            OwnTracksPointAdapter(),
            OwnTracksPlaceClusterAdapter(),
            OwnTracksSsidPresenceAdapter(ssid_places={}),
        ):
            result = await adapter.run(pool=creator, chronicler_pool=creator)
            assert result.error is None and not result.skipped
        run = await start_attempt(own)
        decision = await prepare_batch(own, run)
        assert decision is not None
        assert (
            await creator.fetchval(
                "SELECT count(*) FROM location_retention_plan_rows WHERE decision_id=$1", decision
            )
            == 3
        )
        # Real source-owned engine reads this planted terminal remote receipt;
        # this remains synthetic engine proof, not a registered sender verdict.
        await creator.execute(
            "INSERT INTO location_retention_holder_receipts "
            "(decision_id,owning_butler,holder_kind,holder_generation,source_digest,receipt_id) "
            "SELECT decision_id,'switchboard','switchboard_skipped',decision_id,manifest_digest,$2 "
            "FROM location_retention_plans WHERE decision_id=$1",
            decision,
            uuid4(),
        )
        from butlers.chronicler.location_retention import filtered_copy_plans
        from butlers.connectors.owntracks_copy_retention import (
            FilteredCopyPlan,
            prepare_filtered_copies,
        )

        copy_plan = next(
            FilteredCopyPlan.model_validate(w)
            for w in await filtered_copy_plans(own)
            if w["decision_id"] == str(decision)
        )
        assert await seal_native_frontier(own, decision) is None
        await prepare_filtered_copies(connector, copy_plan)
        assert await seal_native_frontier(own, decision) is not None
        disposed = await dispose_ready_point_evidence(own, decision)
        assert disposed is not None
        await issue_ready_grant(own, decision, disposed)
        wire = ReadyGrant.model_validate((await ready_batches(own))[0])
        from butlers.chronicler.location_retention import _committed_raw_plan

        async with own.acquire() as witness:
            original_plan = await witness.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1", decision
            )
            original_frontier = await witness.fetchrow(
                "SELECT * FROM location_retention_frontiers WHERE decision_id=$1", decision
            )
            assert original_plan is not None and original_frontier is not None
            assert not await _committed_raw_plan(witness, original_plan, original_frontier)
        result = await forget_ready_batch(connector, wire)
        assert result["deleted_count"] == 3
        await reconcile_raw_batches(own)
        async with own.acquire() as witness:
            assert await _committed_raw_plan(witness, original_plan, original_frontier)
        assert await creator.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 1
        before = dict(
            await creator.fetchrow("SELECT * FROM location_retention_runs WHERE run_id=$1", run)
        )
        # Disposable trigger fails the ACTUAL producer's completion UPDATE.
        # Independent readback falsifies separate-commit/regression behavior.
        await creator.execute(
            "CREATE FUNCTION completion_fault() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.completion_at IS NOT NULL AND NEW.status IN ('complete','no_work') "
            "THEN RAISE EXCEPTION 'planted completion failure'; END IF; RETURN NEW; END $$; "
            "CREATE TRIGGER completion_fault AFTER UPDATE ON location_retention_runs "
            "FOR EACH ROW EXECUTE FUNCTION completion_fault()"
        )
        try:
            with pytest.raises(asyncpg.RaiseError, match="planted completion failure"):
                await complete_native_attempt(own, run)
            async with creator.acquire() as committed:
                assert (
                    dict(
                        await committed.fetchrow(
                            "SELECT * FROM location_retention_runs WHERE run_id=$1", run
                        )
                    )
                    == before
                )
        finally:
            await creator.execute(
                "DROP TRIGGER completion_fault ON location_retention_runs; DROP FUNCTION completion_fault()"
            )
        await complete_native_attempt(own, run)
        status = await retention_status(own)
        assert status["status"] == "complete" and status["unknown_count"] == 0
        assert (
            status["overdue_count"]
            == status["blocked_count"]
            == status["holder_pending_count"]
            == 0
        )
        assert status["deleted_count"] == 3 and status["completion_at"] is not None
        first = dict(
            await creator.fetchrow("SELECT * FROM location_retention_runs WHERE run_id=$1", run)
        )
        await complete_native_attempt(own, run)
        assert (
            dict(
                await creator.fetchrow("SELECT * FROM location_retention_runs WHERE run_id=$1", run)
            )
            == first
        )
        assert await creator.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 1
        idle = await start_attempt(own)
        await complete_native_attempt(own, idle)
        idle_status = await retention_status(own)
        assert idle_status["status"] == "no_work" and idle_status["unknown_count"] == 0
        assert idle_status["prepared_count"] == idle_status["deleted_count"] == 0
        assert first == dict(
            await creator.fetchrow("SELECT * FROM location_retention_runs WHERE run_id=$1", run)
        )
        # A new actual opaque owning session invalidates a later attempt's
        # census. An earlier timestamp/complete plan cannot refresh green.
        from butlers.core.sessions import session_create
        from butlers.core.utils import generate_uuid7_string

        await session_create(
            own,
            prompt="synthetic opaque input",
            trigger_source="trigger",
            request_id=generate_uuid7_string(),
        )
        later = await start_attempt(own)
        await complete_native_attempt(own, later)
        current = await retention_status(own)
        assert current["status"] == "unknown" and current["unknown_count"] is None
        assert first == dict(
            await creator.fetchrow("SELECT * FROM location_retention_runs WHERE run_id=$1", run)
        )
        await _assert_native_filtered_copy_preparation(creator, own, connector)
    finally:
        await module.on_shutdown()
        await connector.close()
        await own.close()
        await creator.close()


async def _assert_late_opaque_holder_refusal(pool, owning, decision, frontier, before_points):
    """Current-census engine proof; opaque fixture cleanup is NOT erasure authority."""
    import asyncio
    from contextlib import suppress

    from butlers.chronicler.location_retention import (
        dispose_ready_point_evidence,
        seal_native_frontier,
    )
    from butlers.chronicler.storage import _lock_location_writes, upsert_tier2_cache
    from butlers.core.sessions import session_create
    from butlers.core.utils import generate_uuid7_string

    # This actual ordinary configured producer has no native ancestry. Its
    # opaque stored input must block reuse of the already committed snapshot.
    request = generate_uuid7_string()
    task = None
    try:
        async with owning.acquire() as holder:
            async with holder.transaction():
                await _lock_location_writes(holder)
                holder_pid = await holder.fetchval("SELECT pg_backend_pid()")
                task = asyncio.create_task(
                    session_create(
                        owning,
                        prompt="synthetic opaque late fixture input",
                        trigger_source="tick",
                        request_id=request,
                    )
                )
                waiting = False
                for _ in range(100):
                    waiting = await pool.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity a "
                        "WHERE $1=ANY(pg_blocking_pids(a.pid)) "
                        "AND a.query LIKE '%FROM location_retention_policy%')",
                        holder_pid,
                    )
                    if waiting or task.done():
                        break
                    await asyncio.sleep(0.01)
                assert waiting and not task.done()
                assert not await pool.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM sessions WHERE request_id=$1)", request
                )
        session = await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    async with pool.acquire() as committed:
        assert await committed.fetchval("SELECT prompt FROM sessions WHERE id=$1", session)
        assert not await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_copy_births WHERE receiving_session=$1)",
            session,
        )
    assert await seal_native_frontier(owning, decision) is None
    assert await dispose_ready_point_evidence(owning, decision) is None
    async with pool.acquire() as committed:
        assert await committed.fetchval("SELECT count(*) FROM point_events") == before_points
        assert await committed.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
        assert (
            await committed.fetchval(
                "SELECT frontier_generation FROM location_retention_frontiers WHERE decision_id=$1",
                decision,
            )
            == frontier
        )
    # Disposable test-owner cleanup of ONLY the planted fixture. This is not a
    # product classification/disposal path and supplies no legacy-holder receipt.
    await pool.execute("DELETE FROM sessions WHERE id=$1", session)
    assert await seal_native_frontier(owning, decision) == frontier

    key = f"synthetic-late-opaque:{uuid4()}"
    async with owning.acquire() as writer:
        async with writer.transaction():
            await _lock_location_writes(writer)
            await upsert_tier2_cache(
                writer,
                cache_key=key,
                start_at=datetime.now(UTC) - timedelta(hours=1),
                end_at=datetime.now(UTC),
                prose="synthetic opaque late cache fixture",
                provenance_refs=[],
            )
    async with pool.acquire() as committed:
        assert await committed.fetchval("SELECT prose FROM tier2_cache WHERE cache_key=$1", key)
        assert not await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_cache_heads WHERE cache_key=$1)", key
        )
    assert await seal_native_frontier(owning, decision) is None
    assert await dispose_ready_point_evidence(owning, decision) is None
    async with pool.acquire() as committed:
        assert await committed.fetchval("SELECT count(*) FROM point_events") == before_points
        assert await committed.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
    await pool.execute("DELETE FROM tier2_cache WHERE cache_key=$1", key)
    assert await seal_native_frontier(owning, decision) == frontier

    # Empty prompt/result must not hide an opaque composed-system or process
    # diagnostic body. Use each actual configured writer; no native receipt is
    # fabricated from a session locator or the absence of normal prompt bytes.
    import hashlib

    from butlers.core.session_process_logs import write as write_process_log

    system_body = "synthetic opaque composed system fixture"
    system_session = await session_create(
        owning,
        prompt="",
        trigger_source="tick",
        request_id=generate_uuid7_string(),
        effective_system_prompt=system_body,
        prompt_digest=hashlib.sha256(system_body.encode()).hexdigest(),
        prompt_provenance=[],
    )
    async with pool.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT effective_system_prompt FROM sessions WHERE id=$1", system_session
            )
            == system_body
        )
    assert await seal_native_frontier(owning, decision) is None
    assert await dispose_ready_point_evidence(owning, decision) is None
    assert await pool.fetchval("SELECT count(*) FROM point_events") == before_points
    await pool.execute("DELETE FROM sessions WHERE id=$1", system_session)
    assert await seal_native_frontier(owning, decision) == frontier

    diagnostic_session = await session_create(
        owning, prompt="", trigger_source="tick", request_id=generate_uuid7_string()
    )
    assert await seal_native_frontier(owning, decision) == frontier
    for field in ("command", "stderr"):
        await write_process_log(
            owning, diagnostic_session, **{field: "synthetic opaque process fixture"}
        )
        async with pool.acquire() as committed:
            diagnostic = await committed.fetchrow(
                "SELECT command,stderr FROM session_process_logs WHERE session_id=$1",
                diagnostic_session,
            )
            assert diagnostic[field] == "synthetic opaque process fixture"
        assert await seal_native_frontier(owning, decision) is None
        assert await dispose_ready_point_evidence(owning, decision) is None
        assert await pool.fetchval("SELECT count(*) FROM point_events") == before_points
        assert await pool.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 304
        await pool.execute(
            "DELETE FROM session_process_logs WHERE session_id=$1", diagnostic_session
        )
        assert await seal_native_frontier(owning, decision) == frontier
    await pool.execute("DELETE FROM sessions WHERE id=$1", diagnostic_session)


async def _assert_native_filtered_copy_preparation(creator, own, connector):
    """Real configured producer/role engine; accepted UUIDs remain synthetic."""
    from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
    from butlers.connectors.owntracks import persist_location_point
    from butlers.connectors.owntracks_copy_retention import (
        FilteredCopyPlan,
        NativeFilteredCopyBuffer,
        prepare_filtered_copies,
        read_filtered_receipt,
    )
    from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest
    from butlers.location_retention import logical_digest

    moment = datetime.now(UTC) - timedelta(days=31)
    stamp = int(moment.timestamp())
    payload = {"_type": "location", "tst": stamp, "lat": 1.31415926, "lon": 103.81234567}
    endpoint = "owntracks:copy-fixture"
    assert await persist_location_point(
        connector,
        endpoint_identity=endpoint,
        tst=stamp,
        lat=payload["lat"],
        lon=payload["lon"],
        accuracy=None,
        trigger=None,
        raw_payload=payload,
        accepted_request_id=uuid4(),
        accepted_payload_digest=content_digest(payload),
        accepted_normalized_digest=b"n" * 32,
    )
    source = await connector.fetchrow(
        "SELECT * FROM connectors.owntracks_points WHERE endpoint_identity=$1", endpoint
    )
    frozen = FrozenRaw.model_validate(
        {
            key: source[key].hex()
            if key.endswith("digest")
            else source["id" if key == "raw_id" else key]
            for key in FrozenRaw.model_fields
        }
    )
    decision, cutoff = uuid4(), datetime.now(UTC) - timedelta(days=30)
    plan = FilteredCopyPlan(
        decision_id=decision,
        policy_version=1,
        cutoff=cutoff,
        rows=(frozen,),
        manifest_digest=frozen_manifest(decision, 1, cutoff, [frozen]).hex(),
    )
    # Actual producer receives this old device timestamp NOW. Its immutable
    # retention birth correctly uses current received time; do not mutate it
    # or pretend the old tst makes this freshly received point eligible.
    fresh_source = source
    assert source["retention_at"] >= cutoff
    with pytest.raises(ValueError, match="native filtered raw source differs"):
        await prepare_filtered_copies(connector, plan)
    async with connector.acquire() as committed:
        assert dict(
            await committed.fetchrow(
                "SELECT * FROM connectors.owntracks_points WHERE id=$1", fresh_source["id"]
            )
        ) == dict(fresh_source)
    # Separate deliberately aged ENGINE fixture at original INSERT, under the
    # existing connector role and actual immutable trigger. This is synthetic
    # historical input, not live authoritative clock/accepted-source evidence.
    endpoint += ":aged"
    input_generation = await _plant_closed_input_engine_history(
        connector, logical_digest(f"owntracks:{endpoint}:{stamp}:location"), content_digest(payload)
    )
    source = await connector.fetchrow(
        "INSERT INTO connectors.owntracks_points "
        "(idempotency_key,ts,lat,lon,accuracy,trigger,endpoint_identity,raw_payload,"
        "recorded_at,retention_at,logical_source_digest,content_digest,"
        "accepted_request_id,accepted_payload_digest,accepted_normalized_digest,"
        "source_input_generation) "
        "VALUES($1,$2,$3,$4,NULL,NULL,$5,$6,$2,$2,$7,$8,$9,$10,$11,$12) RETURNING *",
        f"owntracks:{endpoint}:{stamp}:location",
        moment,
        payload["lat"],
        payload["lon"],
        endpoint,
        payload,
        logical_digest(f"owntracks:{endpoint}:{stamp}:location"),
        content_digest(payload),
        uuid4(),
        content_digest(payload),
        b"n" * 32,
        input_generation,
    )
    assert source["retention_at"] < cutoff
    frozen = FrozenRaw.model_validate(
        {
            key: source[key].hex()
            if key.endswith("digest")
            else source["id" if key == "raw_id" else key]
            for key in FrozenRaw.model_fields
        }
    )
    decision = uuid4()
    plan = FilteredCopyPlan(
        decision_id=decision,
        policy_version=1,
        cutoff=cutoff,
        rows=(frozen,),
        manifest_digest=frozen_manifest(decision, 1, cutoff, [frozen]).hex(),
    )
    buffer = NativeFilteredCopyBuffer(endpoint)
    fields = {
        "external_message_id": f"{stamp}:location",
        "source_channel": "owntracks",
        "sender_identity": endpoint,
        "subject_or_preview": "synthetic location copy",
        "filter_reason": "synthetic",
        "error_detail": "synthetic copied detail",
        "full_payload": FilteredEventBuffer.full_payload(
            channel="owntracks",
            provider="owntracks",
            endpoint_identity=endpoint,
            external_event_id=f"{stamp}:location",
            external_thread_id=endpoint,
            observed_at=moment.isoformat(),
            sender_identity=endpoint,
            raw=payload,
        ),
    }
    buffer.record(**fields)
    await buffer.flush(connector)
    assert len(buffer) == 0
    birth = await connector.fetchrow(
        "SELECT * FROM connectors.owntracks_filtered_copy_births WHERE logical_source_digest=$1",
        source["logical_source_digest"],
    )
    assert birth is not None and birth["raw_digest"] == source["content_digest"]
    original = dict(
        await connector.fetchrow(
            "SELECT * FROM connectors.filtered_events WHERE id=$1 AND received_at=$2",
            birth["filtered_id"],
            birth["filtered_received_at"],
        )
    )
    # Observe actual ACLs rather than assume bootstrap widens this newly
    # purpose-restricted relation. Both denied ACL and zero-visible forced RLS
    # are valid privacy boundaries, but an unrelated SQL error is not a denial.
    selectable = await own.fetchval(
        "SELECT has_table_privilege(current_user,"
        "'connectors.owntracks_filtered_copy_births','SELECT')"
    )
    if selectable:
        assert (
            await own.fetchval("SELECT count(*) FROM connectors.owntracks_filtered_copy_births")
            == 0
        )
    else:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await own.fetchval("SELECT count(*) FROM connectors.owntracks_filtered_copy_births")
    # Disposable existing-role SELECT grant positions the RLS predicate against
    # the already planted birth. This test-only grant is not production authority.
    await creator.execute(
        "GRANT SELECT ON connectors.owntracks_filtered_copy_births TO butler_chronicler_rw"
    )
    assert await own.fetchval("SELECT count(*) FROM connectors.owntracks_filtered_copy_births") == 0
    from butlers.owntracks_copy_schema import filtered_copy_security_sql

    await creator.execute(filtered_copy_security_sql())
    async with own.acquire() as restricted:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await restricted.fetch("SELECT * FROM connectors.owntracks_filtered_copy_births")
        assert (
            await restricted.fetchval(
                "SELECT count(*) FROM connectors.owntracks_filtered_copy_batches WHERE decision_id=$1",
                decision,
            )
            == 0
        )
    # Replay the exact own installer against healthy metadata, then falsify
    # distinct complete-shape predicates in disposable savepoints. Each
    # refusal rolls back the tamper; no runtime role repairs its own catalog.
    security = filtered_copy_security_sql()
    await creator.execute(security)
    for tamper, category in (
        ("ALTER TABLE connectors.owntracks_filtered_copy_births ADD COLUMN extra TEXT", "columns"),
        (
            "ALTER TABLE connectors.owntracks_filtered_copy_members ADD CONSTRAINT extra_birth "
            "FOREIGN KEY(copy_generation) REFERENCES connectors.owntracks_filtered_copy_births",
            "constraints",
        ),
        (
            "ALTER TABLE connectors.owntracks_filtered_copy_batches "
            "DROP CONSTRAINT owntracks_copy_batches_count",
            "constraints",
        ),
        (
            "CREATE POLICY extra_read ON connectors.owntracks_filtered_copy_floors "
            "FOR SELECT USING(true)",
            "policy",
        ),
    ):
        async with creator.acquire() as installed:
            with pytest.raises(asyncpg.RaiseError, match=f"installed {category} differ"):
                async with installed.transaction():
                    await installed.execute(tamper)
                    await installed.execute(security)
        await creator.execute(security)
        assert (
            await connector.fetchval(
                "SELECT count(*) FROM connectors.owntracks_filtered_copy_births "
                "WHERE copy_generation=$1",
                birth["copy_generation"],
            )
            == 1
        )
    # Actual producer fault AFTER actual row reduction, before receipt commit.
    await creator.execute(
        "CREATE FUNCTION filtered_receipt_fault() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'planted filtered receipt failure'; END $$; "
        "CREATE TRIGGER filtered_receipt_fault BEFORE INSERT ON "
        "connectors.owntracks_filtered_copy_members FOR EACH ROW "
        "EXECUTE FUNCTION filtered_receipt_fault()"
    )
    try:
        with pytest.raises(asyncpg.RaiseError, match="planted filtered receipt failure"):
            await prepare_filtered_copies(connector, plan, (buffer,))
        async with connector.acquire() as committed:
            assert (
                dict(
                    await committed.fetchrow(
                        "SELECT * FROM connectors.filtered_events WHERE id=$1 AND received_at=$2",
                        birth["filtered_id"],
                        birth["filtered_received_at"],
                    )
                )
                == original
            )
            assert (
                await committed.fetchval(
                    "SELECT count(*) FROM connectors.owntracks_filtered_copy_floors "
                    "WHERE logical_source_digest=$1",
                    source["logical_source_digest"],
                )
                == 0
            )
            assert (
                await committed.fetchval(
                    "SELECT count(*) FROM connectors.owntracks_filtered_copy_batches WHERE decision_id=$1",
                    decision,
                )
                == 0
            )
    finally:
        await creator.execute(
            "DROP TRIGGER filtered_receipt_fault ON connectors.owntracks_filtered_copy_members; "
            "DROP FUNCTION filtered_receipt_fault()"
        )
    result = await prepare_filtered_copies(connector, plan, (buffer,))
    assert result["expected_count"] == 1 and len(result["members"]) == 1
    assert await read_filtered_receipt(connector, plan) == result
    assert await prepare_filtered_copies(connector, plan, (buffer,)) == result
    assert dict(
        await connector.fetchrow(
            "SELECT * FROM connectors.owntracks_points WHERE id=$1",
            source["id"],
        )
    ) == dict(source)  # Copy preparation is NOT a raw deletion grant.
    reduced = await connector.fetchrow(
        "SELECT full_payload,error_detail,status,endpoint_identity,sender_identity "
        "FROM connectors.filtered_events WHERE id=$1 AND received_at=$2",
        birth["filtered_id"],
        birth["filtered_received_at"],
    )
    assert reduced["full_payload"] == {} and reduced["error_detail"] is None
    assert reduced["status"] == "filtered"
    assert reduced["endpoint_identity"] == reduced["sender_identity"] == "retention"
    buffer.record(**fields)
    assert len(buffer) == 0  # Actual separate floor readback installed the private buffer fence.
    with pytest.raises(asyncpg.RaiseError, match="reductions are permanent"):
        await connector.execute(
            "UPDATE connectors.filtered_events SET full_payload=$3 WHERE id=$1 AND received_at=$2",
            birth["filtered_id"],
            birth["filtered_received_at"],
            {"synthetic_refill": True},
        )
    assert await read_filtered_receipt(connector, plan) == result
    # Deliberately neutralize this ONE disposable guard to position the
    # independent current-body detector. This is a falsification control,
    # not an installation repair or alternate product write path.
    await creator.execute(
        "DROP TRIGGER preserve_native_filtered_reduction ON connectors.filtered_events"
    )
    try:
        await connector.execute(
            "UPDATE connectors.filtered_events SET full_payload=$3 WHERE id=$1 AND received_at=$2",
            birth["filtered_id"],
            birth["filtered_received_at"],
            {"synthetic_refill": True},
        )
        with pytest.raises(ValueError, match="committed native filtered body differs"):
            await read_filtered_receipt(connector, plan)
        await connector.execute(
            "UPDATE connectors.filtered_events SET full_payload='{}'::jsonb "
            "WHERE id=$1 AND received_at=$2",
            birth["filtered_id"],
            birth["filtered_received_at"],
        )
    finally:
        from butlers.owntracks_copy_schema import filtered_copy_security_sql

        await creator.execute(filtered_copy_security_sql())
    assert await read_filtered_receipt(connector, plan) == result


async def _plant_closed_input_engine_history(pool, logical, raw_digest):
    """Explicit synthetic completed-copy engine cells, NOT runtime/end authority.

    Existing synthetic accepted/raw fixtures need complete matching ancestry to
    position the downstream roles/coarsening/delete controls. No immutable source
    is updated/backfilled. The actual owning producer/lifetime proof is separate.
    """
    generation, incarnation, bundle = uuid4(), uuid4(), uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE connector_writer")
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "owntracks:retention:source"
            )
            await conn.execute(
                "INSERT INTO connectors.owntracks_input_copy_births"
                "(copy_generation,incarnation,copy_bundle,bundle_count,logical_source_digest,"
                "raw_digest,copy_kind,producer_contract,server_generation) "
                "VALUES($1,$2,$3,1,$4,$5,3,1,NULL)",
                generation,
                incarnation,
                bundle,
                logical,
                raw_digest,
            )
            await conn.execute(
                "INSERT INTO connectors.owntracks_input_copy_ends(copy_generation,raw_digest) "
                "VALUES($1,$2)",
                generation,
                raw_digest,
            )
    async with pool.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT raw_digest FROM connectors.owntracks_input_copy_ends WHERE copy_generation=$1",
                generation,
            )
            == raw_digest
        )
    return generation


async def _assert_native_input_producer(creator, own, connector):
    """Actual configured Pool/producer transactions; no online receiver claim.

    The source-owned Task observer here is a fixed harness for the same private
    producer. The separate ASGI software node tests the production callback.
    Neither synthetic engine cells nor this harness attest a remote recipient.
    """
    import asyncio
    from dataclasses import replace

    from butlers.connectors.owntracks import persist_location_point
    from butlers.connectors.owntracks_input_copies import OwnTracksInputCopies, require_inputs_ended

    runtime = OwnTracksInputCopies(connector)
    raw = {
        "_type": "location",
        "tst": int(datetime.now(UTC).timestamp()),
        "lat": 1.25,
        "lon": 103.75,
    }
    endpoint = f"owntracks:native-input-{uuid4()}"
    server = runtime.allocate_server()
    await runtime.commit_server(server)
    async with connector.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT incarnation FROM connectors.owntracks_input_server_births WHERE copy_generation=$1",
                server.generation,
            )
            == runtime.incarnation
        )
    with pytest.raises(ValueError, match="server birth is unavailable"):
        await runtime.reserve(endpoint, raw, server=replace(server))

    # The real configured Chronicler role cannot become this input producer.
    wrong_writer = OwnTracksInputCopies(own)
    with pytest.raises(ValueError, match="writer differs"):
        await wrong_writer.commit_server(wrong_writer.allocate_server())
    from butlers.owntracks_copy_schema import INPUT_TABLES, filtered_copy_security_sql

    await creator.execute(filtered_copy_security_sql())
    for table in INPUT_TABLES:
        async with own.acquire() as restricted:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await restricted.fetch(f"SELECT * FROM connectors.{table}")
        # Disposable SELECT grants position the unchanged forced-RLS predicate
        # against the planted header; the production installer then revokes
        # only these exact new relations, not a broader runtime privilege.
        await creator.execute(f"GRANT SELECT ON connectors.{table} TO butler_chronicler_rw")
        assert await own.fetchval(f"SELECT count(*) FROM connectors.{table}") == 0
    await creator.execute(filtered_copy_security_sql())
    assert await connector.fetchval(
        "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_births WHERE copy_generation=$1)",
        server.generation,
    )

    # Fail after the actual first birth INSERT, inside the actual producer's
    # transaction. The separate acquisition must see neither bundle member.
    await creator.execute("""
        CREATE FUNCTION connectors.test_input_second_birth_fault() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.copy_kind=2 THEN RAISE EXCEPTION 'planted input birth fault'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER test_input_second_birth_fault BEFORE INSERT
          ON connectors.owntracks_input_copy_births FOR EACH ROW
          EXECUTE FUNCTION connectors.test_input_second_birth_fault()
    """)
    try:
        with pytest.raises(asyncpg.RaiseError, match="planted input birth fault"):
            await runtime.reserve(endpoint, raw, server=server)
        async with connector.acquire() as committed:
            assert (
                await committed.fetchval(
                    "SELECT count(*) FROM connectors.owntracks_input_copy_births WHERE copy_bundle=$1",
                    server.generation,
                )
                == 0
            )
            assert not await committed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_ends WHERE copy_generation=$1)",
                server.generation,
            )
    finally:
        await creator.execute(
            "DROP TRIGGER test_input_second_birth_fault ON connectors.owntracks_input_copy_births; DROP FUNCTION connectors.test_input_second_birth_fault()"
        )

    # Settle the SAME never-issued failed allocation at an actual native Task
    # end; the positive is a genuinely separate server admission, not a retry
    # that replaces an unknown original child capability.
    async def failed_server_finished():
        return None

    failed_end = asyncio.Event()

    def failed_server_ended(task):
        assert not task.cancelled() and task.exception() is None
        runtime.observed_server_end(server)
        failed_end.set()

    task = asyncio.create_task(failed_server_finished())
    task.add_done_callback(failed_server_ended)
    await task
    await failed_end.wait()
    await runtime.finish_server(server)
    async with connector.acquire() as committed:
        assert await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_ends WHERE copy_generation=$1)",
            server.generation,
        )
    server = runtime.allocate_server()
    await runtime.commit_server(server)
    binding = await runtime.reserve(endpoint, raw, server=server)
    assert binding is not None
    with pytest.raises(ValueError, match="bundle was already captured"):
        await runtime.reserve(endpoint, raw, server=server)
    generation = runtime.processing_generation(binding)
    runtime.require_body(binding, endpoint, raw)
    with pytest.raises(ValueError, match="body changed"):
        runtime.require_body(binding, endpoint, {**raw, "lat": 1.5})
    with pytest.raises(ValueError, match="body changed"):
        runtime.require_body(replace(binding), endpoint, raw)
    with pytest.raises(ValueError, match="still active"):
        await runtime.finish(binding, "webhook_processing")
    async with connector.acquire() as committed:
        assert (
            await committed.fetchval(
                "SELECT count(*) FROM connectors.owntracks_input_copy_births WHERE copy_bundle=$1",
                binding.bundle,
            )
            == 2
        )
        with pytest.raises(ValueError, match="server cohort is still active"):
            await require_inputs_ended(
                committed, generation, binding.logical_digest, binding.raw_digest
            )
    assert await persist_location_point(
        connector,
        endpoint_identity=endpoint,
        tst=raw["tst"],
        lat=raw["lat"],
        lon=raw["lon"],
        accuracy=None,
        trigger=None,
        raw_payload=raw,
        accepted_request_id=uuid4(),
        accepted_payload_digest=content_digest(raw),
        accepted_normalized_digest=b"n" * 32,
        _native_input_generation=generation,
    )
    async with connector.acquire() as committed:
        point = await committed.fetchrow(
            "SELECT * FROM connectors.owntracks_points WHERE endpoint_identity=$1", endpoint
        )
        assert point["source_input_generation"] == generation
        assert point["content_digest"] == binding.raw_digest

    # Only actual successfully ended Task callbacks mark these private ends.
    async def ended_task():
        return None

    observed = asyncio.Event()

    def processing_ended(task):
        assert not task.cancelled() and task.exception() is None
        runtime.observed_end(binding, "webhook_processing")
        observed.set()

    task = asyncio.create_task(ended_task())
    runtime.processing_started(binding)
    task.add_done_callback(processing_ended)
    await task
    await observed.wait()
    await creator.execute("""
        CREATE FUNCTION connectors.test_input_end_fault() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'planted input end fault'; END $$;
        CREATE TRIGGER test_input_end_fault BEFORE INSERT
          ON connectors.owntracks_input_copy_ends FOR EACH ROW
          EXECUTE FUNCTION connectors.test_input_end_fault()
    """)
    try:
        with pytest.raises(asyncpg.RaiseError, match="planted input end fault"):
            await runtime.finish(binding, "webhook_processing")
        async with connector.acquire() as committed:
            assert not await committed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_copy_ends WHERE copy_generation=$1)",
                generation,
            )
            assert (
                await committed.fetchval(
                    "SELECT raw_digest FROM connectors.owntracks_input_copy_births WHERE copy_generation=$1",
                    generation,
                )
                == binding.raw_digest
            )
    finally:
        await creator.execute(
            "DROP TRIGGER test_input_end_fault ON connectors.owntracks_input_copy_ends; DROP FUNCTION connectors.test_input_end_fault()"
        )
    await runtime.finish(binding, "webhook_processing")
    async with connector.acquire() as committed:
        with pytest.raises(ValueError, match="server cohort is still active"):
            await require_inputs_ended(
                committed, generation, binding.logical_digest, binding.raw_digest
            )
    observed.clear()

    def server_ended(task):
        assert not task.cancelled() and task.exception() is None
        runtime.observed_server_end(server)
        observed.set()

    task = asyncio.create_task(ended_task())
    task.add_done_callback(server_ended)
    await task
    await observed.wait()
    await runtime.finish_server(server)
    async with connector.acquire() as committed:
        await require_inputs_ended(
            committed, generation, binding.logical_digest, binding.raw_digest
        )
        assert (
            await committed.fetchval(
                "SELECT count(*) FROM connectors.owntracks_input_copy_ends e JOIN connectors.owntracks_input_copy_births b USING(copy_generation) WHERE b.copy_bundle=$1",
                binding.bundle,
            )
            == 2
        )
        assert await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_ends WHERE copy_generation=$1)",
            server.generation,
        )
        assert dict(
            await committed.fetchrow(
                "SELECT * FROM connectors.owntracks_points WHERE id=$1", point["id"]
            )
        ) == dict(point)
    # A separately admitted same-source replay is an actual live sibling,
    # not erased by the earlier webhook's complete terminal receipts.
    replay = await runtime.reserve(endpoint, raw, replay=True)
    assert replay is not None
    replay_generation = runtime.processing_generation(replay)
    async with connector.acquire() as committed:
        with pytest.raises(ValueError, match="source cohort is still active"):
            await require_inputs_ended(
                committed, generation, binding.logical_digest, binding.raw_digest
            )
    observed.clear()

    def replay_ended(task):
        assert not task.cancelled() and task.exception() is None
        runtime.observed_end(replay, "replay_processing")
        observed.set()

    task = asyncio.create_task(ended_task())
    runtime.processing_started(replay)
    task.add_done_callback(replay_ended)
    await task
    await observed.wait()
    await runtime.finish(replay, "replay_processing")
    async with connector.acquire() as committed:
        await require_inputs_ended(
            committed, generation, binding.logical_digest, binding.raw_digest
        )
        assert (
            await committed.fetchval(
                "SELECT raw_digest FROM connectors.owntracks_input_copy_ends WHERE copy_generation=$1",
                replay_generation,
            )
            == binding.raw_digest
        )

    # Remove ONLY this fresh synthetic fixture point after proving its actual
    # immutable producer readback. This test cleanup is not an erasure receipt.
    await creator.execute("DELETE FROM connectors.owntracks_points WHERE id=$1", point["id"])
