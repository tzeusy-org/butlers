"""core_362 (bu-ejow5p.11): the containment label and the sealed-before-isolation flag.

Real-Postgres contract: an export inserted without the flag is flagged (default true), and the
core_301 guard refuses any UPDATE that changes the flag, for the table owner with row security
lifted as for the QA role. Docker-only, like its sibling ``test_qa_publication_exports_migration``.
"""

from __future__ import annotations

import shutil
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_qa_publication_exports_migration import (
    _HEAD,
    _TABLE,
    _admin,
    _as_role,
    _close,
    _new_attempt,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, db_name: str) -> str:
    return create_migrated_test_db(postgres_container, db_name, chains=["core"])


def _insert_without_flag(attempt_id: uuid.UUID) -> str:
    return (
        f"INSERT INTO {_TABLE} (attempt_id, base_sha, head_sha, commits, objects, digest) "
        f"VALUES ('{attempt_id}', '{'b' * 40}', '{_HEAD}', ARRAY['{_HEAD}'], '\\x00'::bytea, "
        f"'{'a' * 64}')"
    )


def _scalar(db_url: str, sql: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.exec_driver_sql(sql).scalar_one()
    finally:
        engine.dispose()


def _prune_flipping_flag_as_owner(db_url: str, attempt_id: uuid.UUID, flag: bool) -> None:
    """A prune that is otherwise eligible, also rewriting the flag, as a privileged login."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
            conn.exec_driver_sql(
                f"UPDATE {_TABLE} SET objects = ''::bytea, pruned_at = now(), "
                f"sealed_before_isolation = {str(flag).lower()} WHERE attempt_id = '{attempt_id}'"
            )
            conn.exec_driver_sql(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    finally:
        engine.dispose()


def test_an_export_inserted_without_the_flag_is_flagged(db_url: str) -> None:
    attempt = _new_attempt(db_url)
    assert _as_role(db_url, "butler_qa_rw", _insert_without_flag(attempt)) == 1
    flag = _scalar(
        db_url, f"SELECT sealed_before_isolation FROM {_TABLE} WHERE attempt_id = '{attempt}'"
    )
    assert flag is True


def test_the_flag_cannot_be_flipped_even_by_an_otherwise_eligible_privileged_prune(
    db_url: str,
) -> None:
    attempt = _new_attempt(db_url)
    _as_role(db_url, "butler_qa_rw", _insert_without_flag(attempt))
    _close(db_url, attempt, days_ago=31)

    with pytest.raises(DBAPIError, match="retained QA export is immutable"):
        _prune_flipping_flag_as_owner(db_url, attempt, False)
    unchanged = _scalar(
        db_url,
        f"SELECT sealed_before_isolation AND pruned_at IS NULL FROM {_TABLE} "
        f"WHERE attempt_id = '{attempt}'",
    )
    assert unchanged is True

    # Positive control: the same prune leaving the flag alone is admitted, so the refusal above
    # is the flag clause and not the retention checks.
    _prune_flipping_flag_as_owner(db_url, attempt, True)
    assert (
        _scalar(
            db_url, f"SELECT pruned_at IS NOT NULL FROM {_TABLE} WHERE attempt_id = '{attempt}'"
        )
        is True
    )


def test_the_qa_role_cannot_write_the_flag_column_at_all(db_url: str) -> None:
    attempt = _new_attempt(db_url)
    _as_role(db_url, "butler_qa_rw", _insert_without_flag(attempt))
    with pytest.raises(DBAPIError, match="permission denied|immutable"):
        _as_role(
            db_url,
            "butler_qa_rw",
            f"UPDATE {_TABLE} SET sealed_before_isolation = false WHERE attempt_id = '{attempt}'",
        )


def test_the_containment_label_admits_only_none_or_job(db_url: str) -> None:
    attempt = _new_attempt(db_url)
    for value in ("'none'", "'job'", "NULL"):
        _admin(
            db_url,
            f"UPDATE public.healing_attempts SET containment = {value} WHERE id = '{attempt}'",
        )
    with pytest.raises(DBAPIError, match="ck_healing_attempts_containment"):
        _admin(
            db_url,
            f"UPDATE public.healing_attempts SET containment = 'pod' WHERE id = '{attempt}'",
        )
