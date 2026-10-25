"""core_199 runtime-attention producer activation and rollback contracts.

REQ-model-catalog-001; REQ-runtime-attention-outbox-001;
REQ-dashboard-spend-dashboard-001; REQ-database-security-007.
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text

from alembic import command
from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError
from butlers.db_authority.apply import transition_runtime_attention
from butlers.db_authority.interfaces import install_revision_group, protected_group_functions
from butlers.db_authority.roles import AuthorityError, compile_deployment_profiles
from butlers.migrations import (
    _build_alembic_config,
    finalize_authority,
    maintenance_profile,
    migration_session,
)
from butlers.testing.migration import (
    _upgrade_chain_to_revision,
    assert_at_chain_head,
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.audit_helpers import clear_audit_log_for_test

# Without these this file is invisible to `-m db` / `-m integration` selection
# and *errors* rather than skips on a host with no Docker, unlike its sibling
# tests/migrations/test_runtime_attention_outbox_migration.py.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


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
async def migrated_v2_pool(migrated_v2_bootstrap_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        migrated_v2_bootstrap_url.replace("postgresql+psycopg2://", "postgresql://", 1),
        min_size=1,
        max_size=2,
    )
    yield pool
    await pool.close()


def _upgrade_current(bootstrap_url: str, db_url: str, revision: str) -> None:
    """One physical held session owns ordinary DDL and current authority finalization."""
    with migration_session(bootstrap_url, source_database="butlers") as connection:
        _upgrade_chain_to_revision(
            db_url, chain="core", schema=None, revision=revision, connection=connection
        )
        if revision == "core@head":
            finalize_authority(connection, source_database="butlers", plans=(("core", None, None),))
        connection.commit()


def _attention_transition(bootstrap_url: str, component: str, enabled: bool) -> None:
    with migration_session(bootstrap_url, source_database="butlers") as connection:
        transition_runtime_attention(connection, component=component, enabled=enabled)
        connection.commit()


def _insert_breaker_attempts(connection, entry_id):
    trigger_id = None
    for _ in range(5):
        trigger_id = connection.execute(
            text(
                "INSERT INTO public.model_dispatch_attempts(catalog_entry_id,butler,outcome) "
                "VALUES(:id,'general','runtime_failure') RETURNING id"
            ),
            {"id": entry_id},
        ).scalar_one()
    return trigger_id


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
    """Current held maintenance installs, without a serving installer capability."""
    name = migration_db_name()
    url = create_migration_db(postgres_container, name)
    admin = migration_bootstrap_db_url(postgres_container, name)
    _upgrade_current(admin, url, "core_198")
    with pytest.raises((AuthorityError, BootstrapPrerequisiteError)):
        command.upgrade(_build_alembic_config(url, chains=["core"]), "core@head")
    _upgrade_current(admin, url, "core@head")
    engine = create_engine(admin)
    try:
        with engine.connect() as connection:
            assert_at_chain_head(connection)
            assert not connection.execute(
                text(
                    "SELECT EXISTS(SELECT FROM pg_trigger WHERE "
                    "tgrelid='public.model_dispatch_attempts'::regclass AND "
                    "tgname='runtime_attention_plant_legacy_debounce_marker_trigger' AND NOT tgisinternal)"
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'runtime_attention_admin.upgrade_producers_v2()') IS NULL"
                )
            ).scalar_one()
            assert connection.execute(
                text("SELECT to_regnamespace('runtime_attention_admin') IS NULL")
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_runtime_writers_get_debounce_markers_planted_for_them(
    migrated_v2_pool: asyncpg.Pool,
) -> None:
    """Legacy INSERTs still work; notification belongs to the validated current producer.

    No retired debounce marker is planted. The case now proves both retained
    write success and positive real current outbox emission, not broad refusal.
    """
    pool = migrated_v2_pool
    await clear_audit_log_for_test(pool)
    await pool.execute("TRUNCATE public.model_dispatch_attempts CASCADE")
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
        assert rows == []
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=$1",
                entry_id,
            )
            == 2
        )
        async with pool.acquire() as connection:
            await connection.execute('SET ROLE "butler_general_rw"')
            try:
                trigger_id = None
                for _ in range(5):
                    trigger_id = await connection.fetchval(
                        "INSERT INTO public.model_dispatch_attempts(catalog_entry_id,butler,outcome) "
                        "VALUES($1,'general','runtime_failure') RETURNING id",
                        entry_id,
                    )
                episode_id = await connection.fetchval(
                    "SELECT public.append_runtime_attention_model_breaker($1)",
                    trigger_id,
                )
            finally:
                await connection.execute("RESET ROLE")
        assert isinstance(episode_id, uuid.UUID)
        assert (
            await pool.fetchval(
                "SELECT triggering_attempt_id FROM public.runtime_attention_outbox WHERE id=$1",
                episode_id,
            )
            == trigger_id
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
        await clear_audit_log_for_test(pool)
        await pool.execute("TRUNCATE public.model_dispatch_attempts CASCADE")


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
    await clear_audit_log_for_test(pool)
    await pool.execute("TRUNCATE public.model_dispatch_attempts CASCADE")
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
        await clear_audit_log_for_test(pool)
        await pool.execute("TRUNCATE public.model_dispatch_attempts CASCADE")
        engine = create_engine(migrated_v2_bootstrap_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text("DELETE FROM public.runtime_attention_outbox"))
        finally:
            engine.dispose()


def test_bootstrap_rollback_disables_producers_without_restoring_direct_paths(
    postgres_container,
) -> None:
    """Positive operator disable/re-enable retains evidence; a restamp is no substitute."""
    name = migration_db_name()
    url = create_migrated_test_db(postgres_container, name, chains=["core"])
    admin = migration_bootstrap_db_url(postgres_container, name)
    _attention_transition(admin, "producers", False)
    engine = create_engine(admin)
    try:
        with pytest.raises(AuthorityError):
            with migration_session(admin, source_database="butlers") as held:
                config = _build_alembic_config(url, chains=["core"])
                config.attributes["connection"] = held
                command.downgrade(config, "core_198")
        with engine.begin() as connection:
            enabled = connection.execute(
                text(
                    "SELECT producers_enabled FROM "
                    "public.runtime_attention_producer_control WHERE singleton"
                )
            ).scalar_one()
            assert enabled is False
            entry_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO public.model_catalog(id,alias,runtime_type,model_id) "
                    "VALUES(:id,'rollback-disabled','codex','rollback-disabled-model')"
                ),
                {"id": entry_id},
            )
            trigger_id = _insert_breaker_attempts(connection, entry_id)
            connection.execute(text('SET ROLE "butler_general_rw"'))
            try:
                episode_id = connection.execute(
                    text("SELECT public.append_runtime_attention_model_breaker(:id)"),
                    {"id": trigger_id},
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
            assert not connection.execute(
                text(
                    "SELECT EXISTS(SELECT FROM pg_trigger WHERE "
                    "tgrelid='public.model_dispatch_attempts'::regclass AND "
                    "tgname='runtime_attention_plant_legacy_debounce_marker_trigger' AND NOT tgisinternal)"
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'public.append_runtime_attention_model_breaker(bigint)') IS NOT NULL"
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'public.append_runtime_attention_fleet_halt()') IS NOT NULL"
                )
            ).scalar_one()
        _attention_transition(admin, "producers", True)
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM public.model_dispatch_attempts "
                        "WHERE catalog_entry_id=:id"
                    ),
                    {"id": entry_id},
                ).scalar_one()
                == 5
            )
            trigger_id = _insert_breaker_attempts(connection, entry_id)
            connection.execute(text('SET ROLE "butler_general_rw"'))
            try:
                assert (
                    connection.execute(
                        text("SELECT public.append_runtime_attention_model_breaker(:id)"),
                        {"id": trigger_id},
                    ).scalar_one()
                    is not None
                )
            finally:
                connection.execute(text("RESET ROLE"))
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
    """Real historical edge with the current held, source-owned installer."""
    name = migration_db_name()
    url = create_migration_db(postgres_container, name)
    admin = migration_bootstrap_db_url(postgres_container, name)
    _upgrade_current(admin, url, "core_198")
    return name, admin


def test_upgrade_admits_a_cluster_superuser_that_is_not_the_migration_role(
    postgres_container,
) -> None:
    """A separately selected maintenance actor executes the real current installer."""
    db_name, bootstrap_url = _database_at_finalized_v1(postgres_container)
    operator_role = f"rerun_operator_{db_name}"
    engine = create_engine(bootstrap_url)
    try:
        with _transient_role(engine, operator_role, "NOLOGIN SUPERUSER"):
            with migration_session(bootstrap_url, source_database="butlers") as connection:
                original_actor = connection.exec_driver_sql("SELECT session_user").scalar_one()
                connection.exec_driver_sql(f'SET SESSION AUTHORIZATION "{operator_role}"')
                try:
                    owner = maintenance_profile(connection)
                    caller_is_superuser, caller_is_migration_role, caller_is_bootstrap_owner = (
                        connection.execute(
                            text(
                                "SELECT (SELECT rolsuper FROM pg_roles WHERE rolname=session_user), "
                                "session_user=:owner, session_user=:original"
                            ),
                            {"owner": owner, "original": original_actor},
                        ).one()
                    )
                    assert caller_is_superuser
                    assert not caller_is_migration_role
                    assert not caller_is_bootstrap_owner
                    install_revision_group(connection, "attention")
                    assert connection.execute(
                        text("SELECT to_regnamespace('runtime_attention_admin') IS NULL")
                    ).scalar_one()
                    assert connection.execute(
                        text(
                            "SELECT producers_enabled FROM "
                            "public.runtime_attention_producer_control WHERE singleton"
                        )
                    ).scalar_one()
                    connection.commit()
                finally:
                    connection.exec_driver_sql("RESET SESSION AUTHORIZATION")
                    connection.commit()
    finally:
        engine.dispose()


def test_upgrade_refuses_a_non_superuser_that_is_not_the_migration_role(postgres_container) -> None:
    """Real source prerequisite refuses before effects, without arbitrary object GRANTs."""
    db_name, bootstrap_url = _database_at_finalized_v1(postgres_container)
    probe_role = f"upgrade_probe_{db_name}"
    engine = create_engine(bootstrap_url)
    try:
        with _transient_role(engine, probe_role, "NOLOGIN NOSUPERUSER"):
            with engine.connect() as connection:
                connection.info["butlers.source_database"] = "butlers"
                assert connection.execute(
                    text(
                        "SELECT to_regprocedure("
                        "'runtime_attention_admin.upgrade_producers_v2()') IS NULL"
                    )
                ).scalar_one()
                connection.execute(text(f'SET SESSION AUTHORIZATION "{probe_role}"'))
                assert probe_role != f"migration_{db_name}"
                with pytest.raises(AuthorityError) as refusal:
                    install_revision_group(connection, "attention")
                assert "maintenance/ownership prerequisite" in str(refusal.value)
                connection.rollback()
                connection.exec_driver_sql("RESET SESSION AUTHORIZATION")
                connection.commit()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM public.runtime_attention_outbox")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("function_name", "expected_message"),
    [
        (
            "deactivate_producers_v2",
            "core_199 downgrade requires the managed privileged bootstrap owner",
        ),
        (
            "rollback_interface",
            "core_198 downgrade requires the managed privileged bootstrap owner",
        ),
    ],
    ids=("core-199-deactivate-producers-v2", "core-198-runtime-attention-rollback"),
)
def test_runtime_attention_downgrade_functions_refuse_an_executable_nonsuperuser(
    postgres_container,
    function_name: str,
    expected_message: str,
) -> None:
    """Retained parameter identities now execute the current source operator refusal.

    The old raw-message oracle is superseded by the closed authority category;
    no legacy function or executable grant is recreated to make it reachable.
    """
    db_name, bootstrap_url = _database_at_finalized_v1(postgres_container)
    probe_role = f"runtime_attention_rollback_probe_{db_name}"
    function_ref = f"runtime_attention_admin.{function_name}()"
    engine = create_engine(bootstrap_url)
    try:
        with _transient_role(engine, probe_role, "NOLOGIN NOSUPERUSER"):
            with engine.connect() as connection:
                connection.info["butlers.source_database"] = "butlers"
                assert connection.execute(
                    text("SELECT to_regprocedure(:identity) IS NULL"), {"identity": function_ref}
                ).scalar_one()
                connection.execute(text(f'SET SESSION AUTHORIZATION "{probe_role}"'))
                assert not connection.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname=session_user")
                ).scalar_one()
                with pytest.raises(AuthorityError) as refusal:
                    transition_runtime_attention(connection, component="producers", enabled=False)
                assert "maintenance/ownership prerequisite" in str(refusal.value)
                connection.rollback()
                connection.exec_driver_sql("RESET SESSION AUTHORIZATION")
                connection.commit()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT producers_enabled FROM "
                        "public.runtime_attention_producer_control WHERE singleton"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()


def test_core_251_condition_producer_pins_the_controller_identities() -> None:
    """REQ-butler-control-plane-liveness-007: SQL and Python name one identity.

    The producer recomputes nothing: it pages only the fixed fleet and
    overdue-QA fingerprints written into init-db.  If the controller's identity
    version moved without the SQL, conditions would open and never page.
    """
    from butlers.core.fleet_conditions import (
        FLEET_FINGERPRINT,
        QA_PATROL_OVERDUE_FINGERPRINT,
        QA_PATROL_STOPPED_FINGERPRINT,
    )

    root = Path(__file__).resolve().parents[2]
    migration_path = root / "alembic/versions/core/core_251_runtime_attention_condition.py"
    spec = importlib.util.spec_from_file_location("core_251", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("core_251", "core_250")

    definitions = protected_group_functions(
        compile_deployment_profiles("butlers", source_database="butlers"),
        "normal_owner",
        ("attention",),
    )
    body = next(d.body for d in definitions if d.name == "append_runtime_attention_condition")
    assert f"'{FLEET_FINGERPRINT}'" in body
    assert f"'{QA_PATROL_OVERDUE_FINGERPRINT}'" in body
    assert QA_PATROL_STOPPED_FINGERPRINT not in body


def test_core_251_upgrade_requires_the_bootstrap_upgrader_and_then_revokes_it(
    postgres_container,
) -> None:
    """Held maintenance installs; no delegated upgrade function authority survives."""
    name = migration_db_name()
    url = create_migration_db(postgres_container, name)
    admin = migration_bootstrap_db_url(postgres_container, name)
    _upgrade_current(admin, url, "core_250")
    with pytest.raises((AuthorityError, BootstrapPrerequisiteError)):
        command.upgrade(_build_alembic_config(url, chains=["core"]), "core@head")
    _upgrade_current(admin, url, "core@head")
    migration_login = create_engine(url).url.username
    engine = create_engine(admin)
    try:
        with engine.connect() as connection:
            assert_at_chain_head(connection)
            proof = connection.execute(
                text("""
                SELECT to_regprocedure('public.runtime_attention_upgrade_condition_v4()') IS NULL,
                  NOT EXISTS(SELECT FROM pg_proc producer
                    CROSS JOIN LATERAL aclexplode(producer.proacl) acl
                    WHERE producer.oid='public.append_runtime_attention_condition(uuid)'::regprocedure
                      AND acl.grantee<>producer.proowner
                      AND acl.grantee<>'butler_switchboard_rw'::regrole),
                  has_function_privilege(:login,'public.observe_runtime_attention_conditions()','EXECUTE'),
                  (SELECT producer_enabled FROM public.runtime_attention_condition_control WHERE singleton)
            """),
                {"login": migration_login},
            ).one()
            assert all(proof)
    finally:
        engine.dispose()


def test_core_251_upgrade_names_the_bootstrap_when_the_upgrader_was_never_installed(
    postgres_container,
) -> None:
    """A genuine missing owner prerequisite refuses before revisions or protected writes."""
    name = migration_db_name()
    url = create_migration_db(postgres_container, name)
    admin = migration_bootstrap_db_url(postgres_container, name)
    _upgrade_current(admin, url, "core_250")
    bootstrap = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with bootstrap.connect() as connection:
            before = connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'public.runtime_attention_upgrade_condition_v4()') IS NULL"
                )
            ).scalar_one()
            connection.exec_driver_sql('ALTER ROLE "runtime_attention_outbox_owner" LOGIN')
        try:
            with pytest.raises(BootstrapPrerequisiteError):
                _upgrade_current(admin, url, "core@head")
            with bootstrap.connect() as connection:
                assert (
                    connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()
                    == before
                )
        finally:
            with bootstrap.connect() as connection:
                connection.exec_driver_sql('ALTER ROLE "runtime_attention_outbox_owner" NOLOGIN')
        _upgrade_current(admin, url, "core@head")
    finally:
        bootstrap.dispose()


def test_core_251_downgrade_never_refuses_and_only_bootstrap_disables_the_producer(
    postgres_container,
) -> None:
    """Restamping cannot disable; a stopped operator preserves markers across disable/re-enable."""
    name = migration_db_name()
    url = create_migrated_test_db(postgres_container, name, chains=["core"])
    admin = migration_bootstrap_db_url(postgres_container, name)
    enabled_sql = text(
        "SELECT producer_enabled FROM public.runtime_attention_condition_control WHERE singleton"
    )
    engine = create_engine(admin)
    try:
        with pytest.raises((AuthorityError, BootstrapPrerequisiteError)):
            command.downgrade(_build_alembic_config(url, chains=["core"]), "core_250")
        with engine.connect() as connection:
            assert connection.execute(enabled_sql).scalar_one() is True
            before = connection.exec_driver_sql(
                "SELECT * FROM public.runtime_attention_condition_episodes ORDER BY condition_id"
            ).all()
        _attention_transition(admin, "conditions", False)
        with engine.connect() as connection:
            assert connection.execute(enabled_sql).scalar_one() is False
            assert connection.execute(
                text(
                    "SELECT to_regclass('public.runtime_attention_condition_episodes') IS NOT NULL"
                )
            ).scalar_one()
            assert (
                connection.exec_driver_sql(
                    "SELECT * FROM public.runtime_attention_condition_episodes "
                    "ORDER BY condition_id"
                ).all()
                == before
            )
        _attention_transition(admin, "conditions", True)
        with engine.connect() as connection:
            assert connection.execute(enabled_sql).scalar_one() is True
    finally:
        engine.dispose()


def test_core_253_marker_sync_upgrade_is_one_shot_and_downgrade_keeps_markers(
    postgres_container,
) -> None:
    """Actual operator disable/reinstall retains every existing marker and fixed trigger ABI."""
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    admin = migration_bootstrap_db_url(postgres_container, name)
    installed_sql = text("""SELECT to_regprocedure('public.runtime_attention_sync_condition_marker()') IS NOT NULL
        AND EXISTS(SELECT FROM pg_trigger WHERE tgrelid='public.runtime_attention_outbox'::regclass
        AND tgname='runtime_attention_condition_marker_sync_trigger')""")
    marker_count_sql = text("SELECT count(*) FROM public.runtime_attention_condition_episodes")
    engine = create_engine(admin)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.runtime_attention_condition_episodes "
                    "(condition_id,condition_kind,episode_id,last_delivery_state) "
                    "VALUES(gen_random_uuid(),'fleet_control',gen_random_uuid(),'sent')"
                )
            )
            before = connection.exec_driver_sql(
                "SELECT * FROM public.runtime_attention_condition_episodes ORDER BY condition_id"
            ).all()
            assert connection.execute(installed_sql).scalar_one() is True
            assert connection.execute(
                text(
                    "SELECT to_regprocedure("
                    "'public.runtime_attention_upgrade_condition_marker_v5()') IS NULL"
                )
            ).scalar_one()
        _attention_transition(admin, "marker", False)
        with engine.connect() as connection:
            assert connection.execute(installed_sql).scalar_one() is False
            assert connection.execute(marker_count_sql).scalar_one() == 1
        _attention_transition(admin, "marker", True)
        with engine.connect() as connection:
            assert connection.execute(installed_sql).scalar_one() is True
            assert connection.execute(marker_count_sql).scalar_one() == 1
            assert (
                connection.exec_driver_sql(
                    "SELECT * FROM public.runtime_attention_condition_episodes "
                    "ORDER BY condition_id"
                ).all()
                == before
            )
        _attention_transition(admin, "marker", True)
        with engine.connect() as connection:
            assert connection.execute(installed_sql).scalar_one() is True
            assert connection.execute(marker_count_sql).scalar_one() == 1
            assert (
                connection.exec_driver_sql(
                    "SELECT * FROM public.runtime_attention_condition_episodes "
                    "ORDER BY condition_id"
                ).all()
                == before
            )
    finally:
        engine.dispose()
