"""core_199 runtime-attention producer activation and rollback contracts.

REQ-model-catalog-001; REQ-runtime-attention-outbox-001;
REQ-dashboard-spend-dashboard-001; REQ-database-security-007.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    assert_at_chain_head,
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)


@pytest.fixture(scope="module")
def migrated_v2_db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def migrated_v2_db_url(postgres_container, migrated_v2_db_name: str) -> str:
    return create_migrated_test_db(postgres_container, migrated_v2_db_name, chains=["core"])


@pytest.fixture(scope="module")
def migrated_v2_bootstrap_url(
    postgres_container, migrated_v2_db_name: str, migrated_v2_db_url: str
) -> str:
    """Privileged view of the same database.

    ``create_migrated_test_db`` returns the ordinary migration login, and
    ``finalize_interface`` revokes every privilege that login holds on
    ``public.runtime_attention_outbox``, so episodes are only readable here.
    """
    return migration_bootstrap_db_url(postgres_container, migrated_v2_db_name)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated_v2_pool(migrated_v2_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        migrated_v2_db_url.replace("postgresql+psycopg2://", "postgresql://", 1),
        min_size=1,
        max_size=2,
    )
    yield pool
    await pool.close()


def test_core_199_is_the_versioned_runtime_attention_upgrade() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_199_runtime_attention_producer_v2.py"
    )
    spec = importlib.util.spec_from_file_location(
        "core_199_runtime_attention_producer_v2", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "core_199"
    assert migration.down_revision == "core_198"


def test_upgrade_requires_bootstrap_installed_v2_and_revokes_upgrade_authority(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core_config = _build_alembic_config(db_url, chains=["core"])

    command.upgrade(core_config, "core_198")
    command.upgrade(core_config, "core@head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert_at_chain_head(connection)
            assert connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_trigger
                        WHERE tgrelid = 'public.model_dispatch_attempts'::regclass
                          AND tgname = 'runtime_attention_plant_legacy_debounce_marker_trigger'
                          AND NOT tgisinternal
                    )
                    """
                )
            ).scalar_one()
            assert not connection.execute(
                text(
                    """
                    SELECT has_function_privilege(
                        current_user,
                        upgrader.oid,
                        'EXECUTE'
                    )
                    FROM pg_proc AS upgrader
                    JOIN pg_namespace AS admin_schema
                      ON admin_schema.oid = upgrader.pronamespace
                    WHERE admin_schema.nspname = 'runtime_attention_admin'
                      AND upgrader.proname = 'upgrade_producers_v2'
                      AND upgrader.pronargs = 0
                    """
                )
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_schema_privilege(current_user, 'runtime_attention_admin', 'USAGE')"
                )
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_runtime_writers_get_debounce_markers_planted_for_them(
    migrated_v2_pool: asyncpg.Pool,
) -> None:
    """A pre-v2 writer's INSERT succeeds and leaves one marker row per branch.

    Nothing is fenced here and the old name of this test said otherwise.  The
    trigger returns NEW unconditionally, so both INSERTs below land; the only
    effect asserted is the (target, action) pair the retired
    model_breaker_attention and fleet_halt_attention helpers debounced on --
    with no actor filter, which is what let a row planted by a different actor
    suppress them.  This is cooperative self-suppression by a binary that still
    honours its own debounce, not an ingress gate.
    """
    pool = migrated_v2_pool
    async with pool.acquire() as admin_connection:
        await admin_connection.execute(
            "TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE"
        )
    try:
        entry_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'legacy-cutover', 'codex', 'legacy-cutover-model')
            """,
            entry_id,
        )
        async with pool.acquire() as connection:
            await connection.execute('SET ROLE "butler_general_rw"')
            try:
                await connection.execute(
                    """
                    INSERT INTO public.model_dispatch_attempts (
                        catalog_entry_id, butler, outcome, failure_reason
                    ) VALUES ($1, 'general', 'runtime_failure', 'legacy writer')
                    """,
                    entry_id,
                )
                await connection.execute(
                    """
                    INSERT INTO public.model_dispatch_attempts (
                        catalog_entry_id, butler, outcome, failure_reason
                    ) VALUES (
                        $1, 'general', 'quota_skip',
                        'Monthly spend ceiling reached: legacy writer'
                    )
                    """,
                    entry_id,
                )
            finally:
                await connection.execute("RESET ROLE")

        rows = await pool.fetch(
            """
            SELECT actor, action, target, note
            FROM public.audit_log
            WHERE actor = 'runtime_attention_legacy_debounce_marker'
            ORDER BY id
            """
        )
        assert [row["action"] for row in rows] == [
            "model_breaker_open_notified",
            "ceiling_halt_notified",
        ]
        assert rows[0]["target"] == f"model_breaker:{entry_id}"
        assert rows[1]["target"] == "ceiling_halt"
        # The breaker note is read by nobody, so bu-95gq7 renamed it.  The
        # ceiling note is the retired fleet-halt helper's debounce key and must
        # stay the current UTC month, formatted exactly this way.
        assert rows[0]["note"] == "legacy_debounce_planted"
        assert rows[1]["note"] == await pool.fetchval(
            "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM')"
        )
        assert not await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.audit_log
                WHERE actor = 'runtime_attention_cutover_fence'
                   OR note = 'blocked_old_binary'
            )
            """
        ), "the retired audit vocabulary is still being planted"
    finally:
        await pool.execute("TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE")


@pytest.mark.asyncio(loop_scope="module")
async def test_rollover_crossing_denial_is_evidence_for_the_month_being_guarded(
    migrated_v2_pool: asyncpg.Pool,
    migrated_v2_bootstrap_url: str,
) -> None:
    """bu-guxz8: the fleet-halt month and the denial's ``ts`` read two clocks.

    ``v_month`` is transaction-stable (``now()``) so it matches the month the
    producer locks and writes; the denial row's ``ts`` stays on the statement
    clock because REQ-model-catalog-001 orders outcomes by the instant they were
    serialized.  A transaction that crosses the UTC month rollover between BEGIN
    and its insert therefore stamps the denial in the month *after* the one the
    call is guarding.

    The seeded row is the state that leaves behind: one ceiling denial stamped
    at the first instant of the next UTC month, none in the current one, read by
    a caller whose ``now()`` is still inside the current month.  No test can push
    a real server clock past a real rollover, so the row is hand-dated to the
    instant a crossing ``clock_timestamp()`` would have written; the producer
    reads only ``now()`` and the stored ``ts``, so it cannot tell the two apart.

    An equality on both bounds skipped that row, so the count was zero and the
    producer raised ``23514``; ``_produce_edge`` does not absorb that, so the
    denial's own ``model_dispatch_attempts`` row rolled back with it.
    """
    pool = migrated_v2_pool
    await pool.execute("TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE")
    try:
        entry_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'fleet-halt-rollover', 'codex', 'fleet-halt-rollover-model')
            """,
            entry_id,
        )
        guarded_month = await pool.fetchval(
            "SELECT date_trunc('month', now() AT TIME ZONE 'UTC')::date"
        )
        await pool.execute(
            """
            INSERT INTO public.model_dispatch_attempts (
                catalog_entry_id, ts, butler, outcome, failure_reason
            )
            VALUES (
                $1,
                (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month')
                    AT TIME ZONE 'UTC',
                'general', 'quota_skip',
                'Monthly spend ceiling reached: rollover-crossing denial'
            )
            """,
            entry_id,
        )
        assert (
            await pool.fetchval(
                """
                SELECT count(*)
                FROM public.model_dispatch_attempts
                WHERE outcome = 'quota_skip'
                  AND date_trunc('month', ts AT TIME ZONE 'UTC')::date = $1
                """,
                guarded_month,
            )
            == 0
        ), "the seeded denial landed inside the guarded month, so nothing is under test"

        async with pool.acquire() as connection:
            await connection.execute('SET ROLE "butler_general_rw"')
            try:
                episode_id = await connection.fetchval(
                    "SELECT public.append_runtime_attention_fleet_halt()"
                )
            finally:
                await connection.execute("RESET ROLE")
        assert isinstance(episode_id, uuid.UUID)

        engine = create_engine(migrated_v2_bootstrap_url)
        try:
            with engine.connect() as connection:
                episode = connection.execute(
                    text(
                        """
                        SELECT fleet_halt_month, source_snapshot
                        FROM public.runtime_attention_outbox
                        WHERE id = CAST(:episode_id AS uuid)
                        """
                    ),
                    {"episode_id": str(episode_id)},
                ).one()
        finally:
            engine.dispose()
        # The episode belongs to the month the call guarded, not to the month
        # the crossing denial's ts fell into.
        assert episode.fleet_halt_month == guarded_month
        assert episode.source_snapshot["denied_count"] == 1
    finally:
        await pool.execute("TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE")
        engine = create_engine(migrated_v2_bootstrap_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text("DELETE FROM public.runtime_attention_outbox"))
        finally:
            engine.dispose()


def test_bootstrap_rollback_disables_producers_without_restoring_direct_paths(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")

    command.downgrade(_build_alembic_config(bootstrap_url, chains=["core"]), "core_198")

    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as connection:
            enabled = connection.execute(
                text(
                    """
                    SELECT producers_enabled
                    FROM runtime_attention_admin.bootstrap_configuration
                    WHERE singleton
                    """
                )
            ).scalar_one()
            assert enabled is False
            entry_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
                    VALUES (:id, 'rollback-disabled', 'codex', 'rollback-disabled-model')
                    """
                ),
                {"id": entry_id},
            )
            trigger_id = None
            for _ in range(5):
                trigger_id = connection.execute(
                    text(
                        """
                        INSERT INTO public.model_dispatch_attempts (
                            catalog_entry_id, butler, outcome
                        ) VALUES (:id, 'general', 'runtime_failure')
                        RETURNING id
                        """
                    ),
                    {"id": entry_id},
                ).scalar_one()
            connection.execute(text('SET ROLE "butler_general_rw"'))
            try:
                episode_id = connection.execute(
                    text("SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
                    {"trigger_id": trigger_id},
                ).scalar_one_or_none()
            finally:
                connection.execute(text("RESET ROLE"))
            assert episode_id is None
            assert (
                connection.execute(
                    text("SELECT count(*) FROM public.runtime_attention_outbox")
                ).scalar_one()
                == 0
            )
            assert connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgrelid = 'public.model_dispatch_attempts'::regclass
                          AND tgname = 'runtime_attention_plant_legacy_debounce_marker_trigger'
                          AND NOT tgisinternal
                    )
                    """
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NOT NULL"
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NOT NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()


@contextmanager
def _transient_role(engine: Engine, role: str, options: str) -> Iterator[None]:
    """Create one cluster role for the duration of a test, then drop it again.

    The Postgres testcontainer is session scoped, so a leaked role stays visible
    to every later test that inspects ``pg_roles``.
    """
    with engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" {options}'))
    try:
        yield
    finally:
        with engine.begin() as connection:
            # A committed SET SESSION AUTHORIZATION outlives its transaction and
            # rides the pooled connection back here, where Postgres refuses to
            # drop the role the session is currently running as.
            connection.execute(text("RESET SESSION AUTHORIZATION"))
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))


def _database_at_finalized_v1(postgres_container) -> tuple[str, str]:
    """Provision a disposable database parked at the finalized v1 interface.

    ``core_198`` runs as the ordinary NOSUPERUSER migration login the harness
    creates, exactly as production does, so
    ``bootstrap_configuration.migration_role`` is that login and no superuser
    session can accidentally also satisfy the guard's migration-role disjunct.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core_198")
    return db_name, migration_bootstrap_db_url(postgres_container, db_name)


def test_upgrade_admits_a_cluster_superuser_that_is_not_the_migration_role(
    postgres_container,
) -> None:
    """The ``rolsuper`` disjunct alone must admit the manual-rerun operator.

    ``scripts/init-db.sql`` documents that "a different superuser may perform a
    later rerun", and that operator has no automated caller in the tree.  Every
    other test that reaches ``upgrade_producers_v2`` arrives through Alembic as
    the migration login, which ``butlers.testing.migration`` creates
    NOSUPERUSER, so the migration-role disjunct is the only one they can
    exercise.  This test drives the other one directly.
    """
    db_name, bootstrap_url = _database_at_finalized_v1(postgres_container)
    operator_role = f"rerun_operator_{db_name}"

    engine = create_engine(bootstrap_url)
    try:
        with _transient_role(engine, operator_role, "NOLOGIN SUPERUSER"):
            with engine.begin() as connection:
                connection.execute(text(f'SET SESSION AUTHORIZATION "{operator_role}"'))
                (
                    caller_is_superuser,
                    caller_is_migration_role,
                    caller_is_bootstrap_owner,
                ) = connection.execute(
                    text(
                        """
                        SELECT
                            COALESCE(
                                (SELECT rolsuper FROM pg_roles WHERE rolname = session_user),
                                false
                            ),
                            session_user = configuration.migration_role,
                            session_user = configuration.bootstrap_role
                        FROM runtime_attention_admin.bootstrap_configuration AS configuration
                        WHERE configuration.singleton
                        """
                    )
                ).one()
                # Without these three facts an admitted call proves nothing:
                # it could have been admitted by the migration-role disjunct,
                # or by the caller happening to be the bootstrap owner.
                assert caller_is_superuser
                assert not caller_is_migration_role
                assert not caller_is_bootstrap_owner

                connection.execute(text("SELECT runtime_attention_admin.upgrade_producers_v2()"))

                assert connection.execute(
                    text(
                        """
                        SELECT interface_version = 2 AND producers_enabled
                        FROM runtime_attention_admin.bootstrap_configuration
                        WHERE singleton
                        """
                    )
                ).scalar_one()
                assert connection.execute(
                    text(
                        """
                        SELECT interface_version = 2 AND producers_enabled
                        FROM public.runtime_attention_producer_control
                        WHERE singleton
                        """
                    )
                ).scalar_one()
    finally:
        engine.dispose()


def test_upgrade_refuses_a_non_superuser_that_is_not_the_migration_role(
    postgres_container,
) -> None:
    """Admission has to be attributable to one of the guard's two properties.

    The superuser test above would stay green if the guard were deleted
    outright.  This one goes red in that case, so the pair pins the guard to
    exactly its two disjuncts rather than to "the function can be called".
    """
    db_name, bootstrap_url = _database_at_finalized_v1(postgres_container)
    probe_role = f"upgrade_probe_{db_name}"

    engine = create_engine(bootstrap_url)
    try:
        with _transient_role(engine, probe_role, "NOLOGIN NOSUPERUSER"):
            with engine.begin() as connection:
                connection.execute(
                    text(f'GRANT USAGE ON SCHEMA runtime_attention_admin TO "{probe_role}"')
                )
                connection.execute(
                    text(
                        "GRANT EXECUTE ON FUNCTION "
                        f'runtime_attention_admin.upgrade_producers_v2() TO "{probe_role}"'
                    )
                )
            with engine.connect() as connection:
                connection.execute(text(f'SET SESSION AUTHORIZATION "{probe_role}"'))
                assert probe_role != f"migration_{db_name}"
                # The grants above exist so that the refusal below comes from
                # the guard and not from an ACL check the caller never reached.
                assert connection.execute(
                    text(
                        "SELECT has_function_privilege(session_user, "
                        "'runtime_attention_admin.upgrade_producers_v2()', 'EXECUTE')"
                    )
                ).scalar_one()
                with pytest.raises(DBAPIError) as refusal:
                    connection.execute(
                        text("SELECT runtime_attention_admin.upgrade_producers_v2()")
                    )
        assert "runtime-attention v2 upgrade requires its configured migration role" in str(
            refusal.value
        )
    finally:
        engine.dispose()
