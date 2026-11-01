"""core_294: retained sealed QA exports (bu-mc01y5.19).

Real-Postgres contract for the spec scenario "Committed proposal cannot be
published": the sealed artifact is retained outside agent control, only the QA
role writes it, and a retained row can be neither rewritten nor dropped by a
rollback.  Modelled on ``test_qa_publication_operations_migration.py``.
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_qa_publication_operations_migration import _bootstrap_rerun

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_294_qa_publication_exports.py"
)
_TABLE = "public.qa_publication_exports"
_HEAD = "c" * 40


def _insert_sql(attempt_id: uuid.UUID) -> str:
    return f"""
        INSERT INTO {_TABLE} (attempt_id, base_sha, head_sha, commits, objects, digest)
        VALUES ('{attempt_id}', '{"b" * 40}', '{_HEAD}', ARRAY['{_HEAD}'], '\\x00'::bytea,
                '{"a" * 64}')
    """


def _as_role(db_url: str, role: str, sql: str) -> int:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET ROLE {role}")
            return conn.exec_driver_sql(sql).rowcount
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


def test_only_qa_retains_and_no_role_rewrites_a_retained_export(
    postgres_container, db_name: str, db_url: str, attempt_id: uuid.UUID
) -> None:
    migration_user = urlparse(db_url).username
    assert migration_user is not None
    _bootstrap_rerun(postgres_container, db_name, migration_user)

    assert _as_role(db_url, "butler_qa_rw", _insert_sql(attempt_id)) == 1
    with pytest.raises(DBAPIError, match="row-level security|permission denied"):
        _as_role(db_url, "butler_general_rw", _insert_sql(uuid.uuid4()))
    for role in ("butler_qa_rw", "butler_general_rw"):
        assert _as_role(db_url, role, f"UPDATE {_TABLE} SET digest = '{'f' * 64}'") == 0
        assert _as_role(db_url, role, f"DELETE FROM {_TABLE}") == 0
        seen = _as_role(db_url, role, f"SELECT 1 FROM {_TABLE} WHERE attempt_id = '{attempt_id}'")
        assert seen == 1
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            forced = conn.exec_driver_sql(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{_TABLE}'::regclass"
            ).one()
            guarded = conn.exec_driver_sql(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'qa_publication_exports_immutable'"
            ).scalar_one()
        assert (tuple(forced), guarded) == ((True, True), 1)
    finally:
        engine.dispose()


def _replay(db_url: str, direction: str) -> None:
    """Execute the migration's real upgrade/downgrade SQL as the last installed schema."""
    spec = importlib.util.spec_from_file_location("core_294_replay", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with (
        patch.object(migration, "op", fake_op),
        patch.object(migration, "_core_294_installation_count", return_value=1),
    ):
        getattr(migration, direction)()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
    finally:
        engine.dispose()


def test_downgrade_refuses_while_an_export_is_retained(db_url: str, attempt_id: uuid.UUID) -> None:
    _replay(db_url, "upgrade")  # idempotent re-apply on a migrated database
    _as_role(db_url, "butler_qa_rw", _insert_sql(attempt_id))

    with pytest.raises(DBAPIError, match="sealed QA export is retained"):
        _replay(db_url, "downgrade")
