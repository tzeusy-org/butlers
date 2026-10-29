"""core_266: QA publication operation ledger (bu-mc01y5.5).

Real-Postgres contract for REQ-qa-publication-authority-002/-006.  Modelled on
``test_qa_investigation_events_migration.py`` for the table shape and on
``tests/test_cost_claims_rls.py`` for the bootstrap-replay and ``SET ROLE``
proof: ``scripts/init-db.sql`` re-grants public-schema DML to every runtime role
on each run, so the durable fence is forced row security keyed to the QA role.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_266_qa_publication_operations.py"
)
_TABLE = "public.qa_publication_operations"
_DIGEST = "a" * 64


def _replay(db_url: str, direction: str) -> None:
    """Execute the migration's real upgrade/downgrade SQL against an existing database."""
    spec = importlib.util.spec_from_file_location("core_266_replay", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(migration, "op", fake_op):
        getattr(migration, direction)()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
    finally:
        engine.dispose()


def _bootstrap_rerun(postgres_container, db_name: str, migration_user: str) -> None:
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    source = init_db_sql_for_dbapi()
    try:
        raw = engine.raw_connection()
        try:
            raw.autocommit = True
            with raw.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('butlers.connecting_user', %s, false)", (migration_user,)
                )
                cursor.execute(source)
        finally:
            raw.close()
    finally:
        engine.dispose()


def _update_as_qa(db_url: str, sql: str) -> None:
    """Forced RLS applies to the table owner too: only the QA role may update."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_qa_rw")
            assert conn.exec_driver_sql(sql).rowcount == 1
    finally:
        engine.dispose()


def _insert_as(db_url: str, role: str, attempt_id: uuid.UUID, kind: str = "branch_push") -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET ROLE {role}")
            conn.exec_driver_sql(
                f"""
                INSERT INTO {_TABLE}
                    (attempt_id, kind, stage, stage_order, binding_digest, request_digest)
                VALUES ('{attempt_id}', '{kind}', 'bound', 1, '{_DIGEST}', '{_DIGEST}')
                """
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, db_name: str) -> str:
    return create_migrated_test_db(postgres_container, db_name, chains=["core"])


@pytest.fixture
def attempt_id(db_url: str) -> uuid.UUID:
    attempt = uuid.uuid4()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.healing_attempts "
                    "(id, fingerprint, butler_name, status, severity, exception_type, call_site) "
                    "VALUES (:id, :fp, 'qa', 'investigating', 2, 'E', 'x:y')"
                ),
                {"id": attempt, "fp": uuid.uuid4().hex * 2},
            )
    finally:
        engine.dispose()
    return attempt


def test_upgrade_downgrade_round_trip_refuses_while_a_hold_is_unresolved(
    db_url: str, attempt_id: uuid.UUID
) -> None:
    _replay(db_url, "upgrade")  # idempotent re-apply on a migrated database
    _insert_as(db_url, "butler_qa_rw", attempt_id)

    engine = create_engine(db_url)
    try:
        _update_as_qa(
            db_url,
            f"UPDATE {_TABLE} SET stage = 'ambiguous', stage_order = 3, result = 'ambiguous' "
            f"WHERE attempt_id = '{attempt_id}'",
        )
        with pytest.raises(DBAPIError, match="unresolved publication hold"):
            _replay(db_url, "downgrade")
        _update_as_qa(
            db_url,
            f"UPDATE {_TABLE} SET stage = 'reconciled', stage_order = 4, "
            f"reconciliation = 'resource_absent', reconciled_at = now() "
            f"WHERE attempt_id = '{attempt_id}'",
        )
        _replay(db_url, "downgrade")
        assert engine.connect().exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is None
        _replay(db_url, "upgrade")
        assert (
            engine.connect().exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is not None
        )
    finally:
        engine.dispose()


def test_bootstrap_rerun_keeps_qa_only_writes_and_forced_rls(
    postgres_container, db_name: str, db_url: str, attempt_id: uuid.UUID
) -> None:
    migration_user = urlparse(db_url).username
    assert migration_user is not None
    _bootstrap_rerun(postgres_container, db_name, migration_user)
    _bootstrap_rerun(postgres_container, db_name, migration_user)

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            forced = conn.exec_driver_sql(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{_TABLE}'::regclass"
            ).one()
            delete_policies = conn.exec_driver_sql(
                f"SELECT count(*) FROM pg_policy WHERE polrelid = '{_TABLE}'::regclass "
                "AND polcmd IN ('d', '*')"
            ).scalar_one()
        assert tuple(forced) == (True, True)
        assert delete_policies == 0

        # The QA role writes through the bootstrap's re-granted DML...
        _insert_as(db_url, "butler_qa_rw", attempt_id)

        # ...and every other runtime role is refused by the policies even though
        # the bootstrap baseline re-granted it INSERT/UPDATE.
        for role in ("butler_general_rw", "butler_switchboard_rw", "connector_writer"):
            with pytest.raises(DBAPIError, match="row-level security|permission denied"):
                _insert_as(db_url, role, uuid.uuid4(), kind="pr_create")
            with engine.begin() as conn:
                conn.exec_driver_sql(f"SET ROLE {role}")
                updated = conn.exec_driver_sql(
                    f"UPDATE {_TABLE} SET stage = 'reconciled', stage_order = 4 "
                    f"WHERE attempt_id = '{attempt_id}'"
                ).rowcount
                deleted = conn.exec_driver_sql(
                    f"DELETE FROM {_TABLE} WHERE attempt_id = '{attempt_id}'"
                ).rowcount
            assert (updated, deleted) == (0, 0)
            with pytest.raises(DBAPIError, match="permission denied"):
                with engine.begin() as conn:
                    conn.exec_driver_sql(f"SET ROLE {role}")
                    conn.exec_driver_sql(f"TRUNCATE {_TABLE}")

        # Every runtime role can still read the hold.
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_general_rw")
            seen = conn.exec_driver_sql(
                f"SELECT count(*) FROM {_TABLE} WHERE attempt_id = '{attempt_id}'"
            ).scalar_one()
        assert seen == 1
    finally:
        engine.dispose()


def test_stage_and_order_cannot_disagree(db_url: str, attempt_id: uuid.UUID) -> None:
    engine = create_engine(db_url)
    try:
        # FORCE RLS refuses a non-QA row before any CHECK runs, so insert as the QA role to
        # prove the constraints themselves.
        with pytest.raises(DBAPIError, match="ck_qa_publication_operations_stage"):
            with engine.begin() as conn:
                conn.exec_driver_sql("SET ROLE butler_qa_rw")
                conn.exec_driver_sql(
                    f"""
                    INSERT INTO {_TABLE}
                        (attempt_id, kind, stage, stage_order, binding_digest, request_digest)
                    VALUES ('{attempt_id}', 'pr_labels', 'succeeded', 1, '{_DIGEST}', '{_DIGEST}')
                    """
                )
        with pytest.raises(DBAPIError, match="ck_qa_publication_operations_kind"):
            with engine.begin() as conn:
                conn.exec_driver_sql("SET ROLE butler_qa_rw")
                conn.exec_driver_sql(
                    f"""
                    INSERT INTO {_TABLE}
                        (attempt_id, kind, stage, stage_order, binding_digest, request_digest)
                    VALUES ('{attempt_id}', 'merge_pr', 'bound', 1, '{_DIGEST}', '{_DIGEST}')
                    """
                )
    finally:
        engine.dispose()


def test_core_266_shared_table_survives_all_but_the_last_schema_downgrade(
    postgres_container,
) -> None:
    """The core chain runs once per butler schema over the shared public table (K1)."""
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    attempt = uuid.uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.healing_attempts "
                    "(id, fingerprint, butler_name, status, severity, exception_type, call_site) "
                    "VALUES (:id, :fp, 'qa', 'investigating', 2, 'E', 'x:y')"
                ),
                {"id": attempt, "fp": uuid.uuid4().hex * 2},
            )
        _insert_as(url, "butler_qa_rw", attempt)
        _update_as_qa(
            url,
            f"UPDATE {_TABLE} SET stage = 'ambiguous', stage_order = 3, result = 'ambiguous' "
            f"WHERE attempt_id = '{attempt}'",
        )

        # The first schema to leave core_266 keeps the shared table: the other schema's head
        # code still queries it, and the unresolved hold is not what stops this downgrade.
        command.downgrade(general, "core_265")
        with engine.connect() as conn:
            assert conn.exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is not None
            held = conn.exec_driver_sql(
                f"SELECT count(*) FROM {_TABLE} WHERE attempt_id = '{attempt}'"
            ).scalar_one()
        assert held == 1

        # The last schema runs the hold check, then drops the table.
        with pytest.raises(DBAPIError, match="unresolved publication hold"):
            command.downgrade(switchboard, "core_265")
        _update_as_qa(
            url,
            f"UPDATE {_TABLE} SET stage = 'reconciled', stage_order = 4, "
            f"reconciliation = 'resource_absent', reconciled_at = now() "
            f"WHERE attempt_id = '{attempt}'",
        )
        command.downgrade(switchboard, "core_265")
        with engine.connect() as conn:
            assert conn.exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is None

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert conn.exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is not None
    finally:
        engine.dispose()
