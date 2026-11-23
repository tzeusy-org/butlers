"""core_364: the shared ``public.owner_watches`` table over two butler schemas (bu-4c7nvp.1.5).

The core chain runs once per butler schema over this shared table, so the upgrade is
idempotent, a downgrade of one schema keeps the table for the other, and only the last schema to
leave drops it, and only while it is empty. Real migrated databases only; synthetic rows.
"""

from __future__ import annotations

import shutil
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
    table_exists,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_FIRST, _SECOND = "general", "health"
_ROW = (
    "INSERT INTO public.owner_watches (id, version, generation, predicate, predicate_digest, "
    "grant_digest, sources, categories, delivery, state, created_at, expires_at, approval_digest)"
    ' VALUES (\'w1\', 1, 1, \'{"tag":"bundle","bundle_id":"x"}\'::jsonb, repeat(\'a\', 64), '
    "repeat('a', 64), '{bundle:x}', '{}', 'digest', 'active', now(), now() + interval '1 day', "
    "repeat('a', 64))"
)


def _run(url: str, statement: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def test_two_schemas_share_one_table_and_only_the_last_to_leave_drops_it(postgres_container):
    name = migration_db_name()
    db_url = create_migration_db(postgres_container, name)
    first = _build_alembic_config(db_url, chains=["core"], target_schema=_FIRST)
    second = _build_alembic_config(db_url, chains=["core"], target_schema=_SECOND)

    command.upgrade(first, "core_364")
    command.upgrade(second, "core_364")
    command.upgrade(second, "core_364")  # an idempotent replay
    assert table_exists(db_url, "owner_watches")

    _run(db_url, _ROW)
    command.downgrade(first, "core_363")
    assert table_exists(db_url, "owner_watches")  # the other schema still depends on it

    with pytest.raises(Exception, match="cannot drop the owner watches"):
        command.downgrade(second, "core_363")
    assert table_exists(db_url, "owner_watches")  # a populated table is never dropped

    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(db_url).path.lstrip("/"))
    _run(bootstrap, "ALTER TABLE public.owner_watches DISABLE TRIGGER ALL")
    _run(bootstrap, "DELETE FROM public.owner_watches")
    _run(bootstrap, "ALTER TABLE public.owner_watches ENABLE TRIGGER ALL")
    command.downgrade(second, "core_363")
    assert not table_exists(db_url, "owner_watches")

    command.upgrade(first, "core_364")
    assert table_exists(db_url, "owner_watches")


def test_the_catalog_check_is_true_once_installed_and_false_when_a_piece_is_missing(
    postgres_container,
):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_364_owner_watches.py"
    spec = importlib.util.spec_from_file_location("core_364_for_catalog_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    name = migration_db_name()
    db_url = create_migration_db(postgres_container, name)
    config = _build_alembic_config(db_url, chains=["core"], target_schema=_FIRST)

    def installed() -> bool:
        engine = create_engine(db_url)
        try:
            with engine.connect() as connection:
                return bool(connection.exec_driver_sql(module._ALREADY_INSTALLED).scalar())
        finally:
            engine.dispose()

    assert installed() is False
    command.upgrade(config, "core_364")
    assert installed() is True
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(db_url).path.lstrip("/"))
    _run(bootstrap, "DROP TRIGGER owner_watch_cap ON public.owner_watches")
    assert installed() is False
    _run(
        bootstrap,
        "CREATE TRIGGER owner_watch_cap BEFORE INSERT OR UPDATE ON public.owner_watches"
        " FOR EACH ROW EXECUTE FUNCTION public.owner_watch_cap()",
    )
    assert installed() is True
    _run(bootstrap, "ALTER TABLE public.owner_watches FORCE ROW LEVEL SECURITY")
    assert installed() is False  # a forced RLS flag is not the installed posture
    _run(bootstrap, "ALTER TABLE public.owner_watches NO FORCE ROW LEVEL SECURITY")
    assert installed() is True

    # Grant-option drift: the same privileges, now re-grantable, is not the installed posture.
    _run(
        bootstrap,
        "GRANT SELECT, INSERT, UPDATE ON public.owner_watches TO butler_switchboard_rw"
        " WITH GRANT OPTION",
    )
    assert installed() is False
    _run(
        bootstrap,
        "REVOKE GRANT OPTION FOR SELECT, INSERT, UPDATE ON public.owner_watches"
        " FROM butler_switchboard_rw",
    )
    assert installed() is True

    # Policy drift: a same-named policy with another expression, command or role set is drift.
    for drifted in (
        "USING (true) WITH CHECK (current_user = 'butler_switchboard_rw')",
        "USING (current_user = 'butler_switchboard_rw') WITH CHECK (true)",
    ):
        _run(bootstrap, "DROP POLICY owner_watches_switchboard ON public.owner_watches")
        _run(
            bootstrap,
            "CREATE POLICY owner_watches_switchboard ON public.owner_watches FOR ALL TO PUBLIC "
            + drifted,
        )
        assert installed() is False, drifted
    for drifted in (
        "FOR SELECT TO PUBLIC USING (current_user = 'butler_switchboard_rw')",
        "FOR ALL TO butler_switchboard_rw USING (current_user = 'butler_switchboard_rw')"
        " WITH CHECK (current_user = 'butler_switchboard_rw')",
    ):
        _run(bootstrap, "DROP POLICY owner_watches_switchboard ON public.owner_watches")
        _run(
            bootstrap, "CREATE POLICY owner_watches_switchboard ON public.owner_watches " + drifted
        )
        assert installed() is False, drifted
    _run(bootstrap, "DROP POLICY owner_watches_switchboard ON public.owner_watches")
    _run(
        bootstrap,
        "CREATE POLICY owner_watches_switchboard ON public.owner_watches FOR ALL TO PUBLIC"
        " USING (current_user = 'butler_switchboard_rw')"
        " WITH CHECK (current_user = 'butler_switchboard_rw')",
    )
    assert installed() is True


def test_last_downgrade_preserves_a_concurrent_committing_writer(postgres_container):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(url, chains=["core"], target_schema=_FIRST)
    command.upgrade(config, "core_364")
    engine = create_engine(url)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor, engine.connect() as observer:
            with engine.begin() as writer:
                writer.execute(text(_ROW))
                downgrade = executor.submit(command.downgrade, config, "core_363")
                deadline = monotonic() + 8
                waiters = text(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE relation = to_regclass('public.owner_watches')
                      AND mode = 'AccessExclusiveLock'
                      AND NOT granted
                    """
                )
                while not observer.execute(waiters).scalar_one():
                    if downgrade.done() or monotonic() >= deadline:
                        raise AssertionError("downgrade never waited for the writer's table lock")
                    sleep(0.03)
            failure = downgrade.exception(timeout=15)
            assert table_exists(url, "owner_watches"), "downgrade dropped a committed watch"
            assert failure is not None
            assert "cannot drop the owner watches" in str(failure), type(failure)
            assert (
                observer.execute(text("SELECT count(*) FROM public.owner_watches")).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_already_installed_watch_does_not_lock_out_a_reader(postgres_container):
    from sqlalchemy.engine import make_url

    url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(url, chains=["core"], target_schema=_FIRST)
    second = _build_alembic_config(url, chains=["core"], target_schema=_SECOND)
    command.upgrade(first, "core_364")
    command.upgrade(second, "core_363")

    timed_url = make_url(url).update_query_dict({"options": "-c lock_timeout=1200ms"})
    timed_second = _build_alembic_config(
        timed_url.render_as_string(hide_password=False), chains=["core"], target_schema=_SECOND
    )
    engine = create_engine(url)
    try:
        with engine.begin() as reader:
            reader.execute(text("SELECT count(*) FROM public.owner_watches"))
            command.upgrade(timed_second, "core_364")
            version = reader.execute(text("SELECT version_num FROM health.alembic_version"))
            assert version.scalar_one() == "core_364"
    finally:
        engine.dispose()


def _drifted_then_repaired(postgres_container, drift: list[str]):
    """Install in two schemas, apply ``drift`` as the bootstrap login, re-run one schema's chain."""
    url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(url, chains=["core"], target_schema=_FIRST)
    second = _build_alembic_config(url, chains=["core"], target_schema=_SECOND)
    command.upgrade(first, "core_364")
    command.upgrade(second, "core_364")
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(url).path.lstrip("/"))
    for statement in drift:
        _run(bootstrap, statement)
    command.downgrade(second, "core_363")  # the other schema still depends on the table
    command.upgrade(second, "core_364")  # the catalog check sees drift, so the chain converges
    return url


def _scalar(url: str, statement: str):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def test_a_stray_grant_to_an_unregistered_role_is_revoked_on_repair(postgres_container):
    url = _drifted_then_repaired(
        postgres_container,
        [
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stray_watch_reader')"
            " THEN CREATE ROLE stray_watch_reader NOLOGIN; END IF; END $$",
            "GRANT SELECT, DELETE ON public.owner_watches TO stray_watch_reader",
            "GRANT SELECT ON public.owner_watches TO PUBLIC",
        ],
    )
    grantees = _scalar(
        url,
        "SELECT string_agg(g, ',' ORDER BY g) FROM ("
        "SELECT DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC' "
        "ELSE pg_get_userbyid(a.grantee) END AS g "
        "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
        "WHERE c.oid = 'public.owner_watches'::regclass AND a.grantee <> c.relowner) q",
    )
    assert grantees == "butler_switchboard_rw", grantees


def test_an_extra_restrictive_policy_is_dropped_on_repair(postgres_container):
    url = _drifted_then_repaired(
        postgres_container,
        [
            "CREATE POLICY owner_watches_extra ON public.owner_watches AS RESTRICTIVE"
            " FOR ALL TO PUBLIC USING (false)",
        ],
    )
    policies = _scalar(
        url,
        "SELECT string_agg(polname, ',' ORDER BY polname) FROM pg_policy "
        "WHERE polrelid = 'public.owner_watches'::regclass",
    )
    assert policies == "owner_watches_switchboard", policies
