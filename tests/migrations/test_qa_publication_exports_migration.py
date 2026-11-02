"""core_294: retained sealed QA exports (bu-mc01y5.19); core_301: pruning (bu-mc01y5.25).

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
_PRUNE_MIGRATION_PATH = _MIGRATION_PATH.with_name("core_301_qa_publication_export_retention.py")
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
    # core_301: the QA role reaches the row for pruning, and the guard refuses any rewrite.
    with pytest.raises(DBAPIError, match="retained QA export is immutable"):
        _as_role(db_url, "butler_qa_rw", f"UPDATE {_TABLE} SET digest = '{'f' * 64}'")
    assert _as_role(db_url, "butler_general_rw", f"UPDATE {_TABLE} SET digest = '{'f' * 64}'") == 0
    for role in ("butler_qa_rw", "butler_general_rw"):
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


def _replay(db_url: str, direction: str, revision: str = "core_294") -> None:
    """Execute the migration's real upgrade/downgrade SQL as the last installed schema."""
    path = _MIGRATION_PATH if revision == "core_294" else _PRUNE_MIGRATION_PATH
    spec = importlib.util.spec_from_file_location(f"{revision}_replay", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with (
        patch.object(migration, "op", fake_op),
        patch.object(migration, f"_{revision}_installation_count", return_value=1),
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


def _prune_sql(attempt_id: uuid.UUID) -> str:
    return (
        f"UPDATE {_TABLE} SET objects = ''::bytea, pruned_at = now() "
        f"WHERE attempt_id = '{attempt_id}'"
    )


def _admin(db_url: str, sql: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
    finally:
        engine.dispose()


def _close(db_url: str, attempt_id: uuid.UUID, *, days_ago: int, status: str = "failed") -> None:
    _admin(
        db_url,
        f"UPDATE public.healing_attempts SET status = '{status}', "
        f"closed_at = now() - interval '{days_ago} days' WHERE id = '{attempt_id}'",
    )


def _new_attempt(db_url: str) -> uuid.UUID:
    attempt = uuid.uuid4()
    _admin(
        db_url,
        "INSERT INTO public.healing_attempts "
        "(id, fingerprint, butler_name, status, severity, exception_type, call_site) "
        f"VALUES ('{attempt}', '{uuid.uuid4().hex * 2}', 'qa', 'investigating', 2, 'E', 'x:y')",
    )
    return attempt


def _prune_bypassing_row_security(db_url: str, attempt_id: uuid.UUID) -> None:
    """Prune as the table owner with FORCE lifted, as a privileged login would."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
            conn.exec_driver_sql(_prune_sql(attempt_id))
    finally:
        engine.dispose()


def test_only_qa_prunes_and_only_past_retention_without_a_hold(
    db_url: str, attempt_id: uuid.UUID
) -> None:
    _replay(db_url, "upgrade", "core_301")  # restore the guard a core_294 replay replaced
    held = _new_attempt(db_url)
    for attempt in (attempt_id, held):
        _as_role(db_url, "butler_qa_rw", _insert_sql(attempt))

    # A tombstone cannot be inserted directly: that would skip eligibility and the digest.
    tombstone = _new_attempt(db_url)
    _close(db_url, tombstone, days_ago=31)
    inserted_pruned = (
        f"INSERT INTO {_TABLE} "
        "(attempt_id, base_sha, head_sha, commits, objects, digest, pruned_at) "
        f"VALUES ('{tombstone}', '{'b' * 40}', '{_HEAD}', ARRAY['{_HEAD}'], ''::bytea, "
        f"'{'a' * 64}', now())"
    )
    with pytest.raises(DBAPIError, match="cannot be inserted pruned"):
        _as_role(db_url, "butler_qa_rw", inserted_pruned)
    engine = create_engine(db_url)
    try:
        with pytest.raises(DBAPIError, match="cannot be inserted pruned"):
            with engine.begin() as conn:
                conn.exec_driver_sql(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
                conn.exec_driver_sql(inserted_pruned)
    finally:
        engine.dispose()

    # Active, then terminal inside the 30-day window: refused, even bypassing row security.
    with pytest.raises(DBAPIError, match="not past retention"):
        _as_role(db_url, "butler_qa_rw", _prune_sql(attempt_id))
    _close(db_url, attempt_id, days_ago=29)
    with pytest.raises(DBAPIError, match="not past retention"):
        _prune_bypassing_row_security(db_url, attempt_id)

    # Past retention but holding a publication lineage: refused.
    _close(db_url, held, days_ago=31)
    _as_role(
        db_url,
        "butler_qa_rw",
        "INSERT INTO public.qa_publication_operations (attempt_id, kind, stage, stage_order, "
        "binding_digest, request_digest, result, ended_at, egress_started_at) "
        f"VALUES ('{held}', 'branch_push', 'ambiguous', 3, '{'1' * 64}', '{'2' * 64}', "
        "'ambiguous', now(), now())",
    )
    with pytest.raises(DBAPIError, match="lineage is held"):
        _as_role(db_url, "butler_qa_rw", _prune_sql(held))

    # Eligible: another runtime role reaches nothing; the QA role prunes once, finally.
    _close(db_url, attempt_id, days_ago=31)
    assert _as_role(db_url, "butler_general_rw", _prune_sql(attempt_id)) == 0
    assert _as_role(db_url, "butler_qa_rw", _prune_sql(attempt_id)) == 1
    with pytest.raises(DBAPIError, match="pruned export is final"):
        _as_role(db_url, "butler_qa_rw", _prune_sql(attempt_id))
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                f"SELECT octet_length(objects), pruned_at IS NOT NULL, head_sha FROM {_TABLE} "
                f"WHERE attempt_id = '{attempt_id}'"
            ).one()
    finally:
        engine.dispose()
    assert tuple(row) == (0, True, _HEAD)


def test_prune_downgrade_refuses_while_an_export_is_pruned(
    db_url: str, attempt_id: uuid.UUID
) -> None:
    _replay(db_url, "upgrade", "core_301")  # idempotent re-apply on a migrated database
    _as_role(db_url, "butler_qa_rw", _insert_sql(attempt_id))
    _close(db_url, attempt_id, days_ago=31)
    _as_role(db_url, "butler_qa_rw", _prune_sql(attempt_id))

    with pytest.raises(DBAPIError, match="pruned export exists"):
        _replay(db_url, "downgrade", "core_301")
