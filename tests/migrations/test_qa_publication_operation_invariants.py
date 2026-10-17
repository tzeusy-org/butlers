"""core_267: database-enforced QA publication ledger invariants (bu-ie4wl1).

Real-Postgres contract for REQ-qa-publication-authority-002 / -006.  core_266 left
forward-only stages, binding immutability and exact reconciliation to application
code; every case below runs as ``butler_qa_rw``, the only role forced row security
lets update the ledger, and must be refused by the database itself.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_TABLE = "public.qa_publication_operations"
_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64
_EXPECTED = "pr-7"

_EGRESS = "stage = 'egress_started', stage_order = 2"
_AMBIGUOUS = "stage = 'ambiguous', stage_order = 3, result = 'ambiguous'"
_SUCCEEDED = "stage = 'succeeded', stage_order = 3, result = 'success'"
_BOUND = "stage = 'bound', stage_order = 1"
_FOUND = (
    "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_found', "
    f"reconciled_object_id = '{_EXPECTED}', reconciled_at = now()"
)
_ABSENT = (
    "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_absent', "
    "reconciled_at = now()"
)


@pytest.fixture(scope="module")
def db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


def _insert_attempt(conn) -> uuid.UUID:
    attempt = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO public.healing_attempts "
            "(id, fingerprint, butler_name, status, severity, exception_type, call_site) "
            "VALUES (:id, :fp, 'qa', 'investigating', 2, 'E', 'x:y')"
        ),
        {"id": attempt, "fp": uuid.uuid4().hex * 2},
    )
    return attempt


@pytest.fixture
def operation(db_url: str) -> uuid.UUID:
    """A bound ``pr_create`` row that expects ``pr-7``."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            attempt = _insert_attempt(conn)
        operation_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_qa_rw")
            conn.exec_driver_sql(
                f"""
                INSERT INTO {_TABLE}
                    (id, attempt_id, kind, stage, stage_order, binding_digest, request_digest,
                     expected_object_id)
                VALUES ('{operation_id}', '{attempt}', 'pr_create', 'bound', 1,
                        '{_DIGEST}', '{_DIGEST}', '{_EXPECTED}')
                """
            )
    finally:
        engine.dispose()
    return operation_id


def _update_as_qa(db_url: str, operation_id: uuid.UUID, assignments: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_qa_rw")
            result = conn.exec_driver_sql(
                f"UPDATE {_TABLE} SET {assignments} WHERE id = '{operation_id}'"
            )
            assert result.rowcount == 1
    finally:
        engine.dispose()


def _refused(db_url: str, operation_id: uuid.UUID, assignments: str, match: str) -> None:
    with pytest.raises(DBAPIError, match=match):
        _update_as_qa(db_url, operation_id, assignments)


def _stage_of(db_url: str, operation_id: uuid.UUID) -> str:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.exec_driver_sql(
                f"SELECT stage FROM {_TABLE} WHERE id = '{operation_id}'"
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("path", "regression"),
    [
        ((_EGRESS,), _BOUND),
        ((_EGRESS, _AMBIGUOUS), _EGRESS),
        ((_EGRESS, _SUCCEEDED), _BOUND),
        ((_EGRESS, _AMBIGUOUS), _SUCCEEDED),
        ((_EGRESS, _SUCCEEDED), _AMBIGUOUS),
        ((_EGRESS, _AMBIGUOUS, _FOUND), _EGRESS),
    ],
    ids=[
        "egress-to-bound",
        "ambiguous-to-egress",
        "succeeded-to-bound",
        "ambiguous-rewritten-as-succeeded",
        "succeeded-rewritten-as-ambiguous",
        "reconciled-reopened",
    ],
)
def test_stage_cannot_regress_or_be_rewritten(
    db_url: str, operation: uuid.UUID, path: tuple[str, ...], regression: str
) -> None:
    for step in path:
        _update_as_qa(db_url, operation, step)
    before = _stage_of(db_url, operation)

    _refused(db_url, operation, regression, "can only move forward|is final")

    assert _stage_of(db_url, operation) == before


def test_forward_moves_are_still_allowed(db_url: str, operation: uuid.UUID) -> None:
    # Skipping a stage is forward; the guard forbids only going back or sideways.
    _update_as_qa(db_url, operation, _AMBIGUOUS)
    _update_as_qa(db_url, operation, _FOUND)
    assert _stage_of(db_url, operation) == "reconciled"


@pytest.mark.parametrize(
    "assignment",
    [
        f"binding_digest = '{_OTHER_DIGEST}'",
        f"request_digest = '{_OTHER_DIGEST}'",
        f"attempt_id = '{uuid.uuid4()}'",
        "kind = 'pr_update'",
        "expected_object_id = 'pr-8'",
        "expected_object_id = NULL",
        "started_at = started_at + interval '1 second'",
    ],
    ids=[
        "binding-digest",
        "request-digest",
        "attempt-binding",
        "kind",
        "expected-object",
        "expected-object-cleared",
        "started-at",
    ],
)
def test_operation_binding_is_immutable_once_written(
    db_url: str, operation: uuid.UUID, assignment: str
) -> None:
    for stage in (_BOUND, _EGRESS):
        _refused(db_url, operation, f"{stage}, {assignment}", "binding is immutable")
        _update_as_qa(db_url, operation, _EGRESS)


def test_unchanged_binding_still_updates(db_url: str, operation: uuid.UUID) -> None:
    _update_as_qa(db_url, operation, f"{_EGRESS}, binding_digest = '{_DIGEST}'")
    _update_as_qa(db_url, operation, "new_object_id = 'pr-7'")
    assert _stage_of(db_url, operation) == "egress_started"


@pytest.mark.parametrize(
    "assignment",
    [
        "new_object_id = 'pr-7'",
        f"binding_digest = '{_OTHER_DIGEST}'",
        _AMBIGUOUS,
    ],
    ids=["new-object", "binding-digest", "stage"],
)
def test_reconciled_row_is_final(db_url: str, operation: uuid.UUID, assignment: str) -> None:
    _update_as_qa(db_url, operation, _AMBIGUOUS)
    _update_as_qa(db_url, operation, _ABSENT)

    _refused(db_url, operation, assignment, "is final")


@pytest.mark.parametrize(
    "assignment",
    [
        # Found, but not the bound object.
        "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_found', "
        "reconciled_object_id = 'pr-99', reconciled_at = now()",
        # Found, naming nothing.
        "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_found', "
        "reconciled_at = now()",
        # Absent, yet naming an object.
        "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_absent', "
        f"reconciled_object_id = '{_EXPECTED}', reconciled_at = now()",
        # A reconciliation on a row that is still held.
        "reconciliation = 'resource_absent'",
        f"reconciled_object_id = '{_EXPECTED}'",
    ],
    ids=[
        "found-other-object",
        "found-without-object",
        "absent-with-object",
        "held-row-with-reconciliation",
        "held-row-with-reconciled-object",
    ],
)
def test_inexact_reconciliation_is_refused(
    db_url: str, operation: uuid.UUID, assignment: str
) -> None:
    _update_as_qa(db_url, operation, _AMBIGUOUS)

    _refused(db_url, operation, assignment, "ck_qa_publication_operations_exact_reconciliation")

    assert _stage_of(db_url, operation) == "ambiguous"


def test_found_cannot_reconcile_a_row_that_bound_no_object(db_url: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            attempt = _insert_attempt(conn)
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_qa_rw")
            conn.exec_driver_sql(
                f"""
                INSERT INTO {_TABLE}
                    (attempt_id, kind, stage, stage_order, binding_digest, request_digest)
                VALUES ('{attempt}', 'branch_push', 'ambiguous', 3, '{_DIGEST}', '{_DIGEST}')
                """
            )
        with pytest.raises(DBAPIError, match="ck_qa_publication_operations_exact_reconciliation"):
            with engine.begin() as conn:
                conn.exec_driver_sql("SET ROLE butler_qa_rw")
                conn.exec_driver_sql(f"UPDATE {_TABLE} SET {_FOUND} WHERE attempt_id = '{attempt}'")
    finally:
        engine.dispose()


@pytest.mark.parametrize("reconciliation", [_FOUND, _ABSENT], ids=["found", "absent"])
def test_exact_reconciliation_releases_the_hold(
    db_url: str, operation: uuid.UUID, reconciliation: str
) -> None:
    _update_as_qa(db_url, operation, _EGRESS)

    _update_as_qa(db_url, operation, reconciliation)

    assert _stage_of(db_url, operation) == "reconciled"


def _guard_objects(conn) -> tuple[int, int, int]:
    trigger = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'qa_publication_operations_guard' "
        "AND NOT tgisinternal"
    ).scalar_one()
    function = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_proc WHERE proname = 'qa_publication_operations_guard'"
    ).scalar_one()
    constraint = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_qa_publication_operations_exact_reconciliation'"
    ).scalar_one()
    return trigger, function, constraint


def test_core_267_shared_guard_survives_all_but_the_last_schema_downgrade(
    postgres_container,
) -> None:
    """The core chain runs once per butler schema over the shared table."""
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert _guard_objects(conn) == (1, 1, 1)

        # The first schema to leave core_267 keeps the guard: the other still runs head code.
        command.downgrade(general, "core_266")
        with engine.connect() as conn:
            assert _guard_objects(conn) == (1, 1, 1)

        # The last schema removes it.
        command.downgrade(switchboard, "core_266")
        with engine.connect() as conn:
            assert _guard_objects(conn) == (0, 0, 0)
            assert conn.exec_driver_sql(f"SELECT to_regclass('{_TABLE}')").scalar() is not None

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert _guard_objects(conn) == (1, 1, 1)
    finally:
        engine.dispose()
