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
                    (attempt_id, kind, stage, stage_order, binding_digest, request_digest, result)
                VALUES ('{attempt}', 'branch_push', 'ambiguous', 3,
                        '{_DIGEST}', '{_DIGEST}', 'ambiguous')
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


# --- core_268: set-once new_object_id, result/stage CHECK, held-row delete guard ---


def test_new_object_id_is_set_once(db_url: str, operation: uuid.UUID) -> None:
    _update_as_qa(db_url, operation, _EGRESS)

    _update_as_qa(db_url, operation, f"{_SUCCEEDED}, new_object_id = 'pr-7'")
    _update_as_qa(db_url, operation, "new_object_id = 'pr-7'")

    _refused(db_url, operation, "new_object_id = 'pr-8'", "new_object_id is set-once")
    _refused(db_url, operation, "new_object_id = NULL", "new_object_id is set-once")


@pytest.mark.parametrize(
    ("prior", "assignment"),
    [
        ((), "result = 'success'"),
        ((_EGRESS,), "result = 'failed'"),
        ((_EGRESS,), "stage = 'succeeded', stage_order = 3, result = 'failed'"),
        ((_EGRESS,), "stage = 'failed', stage_order = 3, result = 'ambiguous'"),
        ((_EGRESS,), "stage = 'ambiguous', stage_order = 3, result = 'success'"),
        ((_EGRESS,), "stage = 'succeeded', stage_order = 3"),
        (
            (_EGRESS,),
            "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_absent', "
            "reconciled_at = now(), result = 'success'",
        ),
        ((_EGRESS,), "stage = 'ambiguous', stage_order = 3"),
    ],
    ids=[
        "bound-with-result",
        "egress-with-result",
        "succeeded-with-failed",
        "failed-with-ambiguous",
        "ambiguous-with-success",
        "succeeded-without-result",
        "reconciled-from-egress-with-success",
        "ambiguous-without-result",
    ],
)
def test_result_must_match_stage(
    db_url: str, operation: uuid.UUID, prior: tuple[str, ...], assignment: str
) -> None:
    for step in prior:
        _update_as_qa(db_url, operation, step)

    _refused(db_url, operation, assignment, "ck_qa_publication_operations_stage_result")


@pytest.mark.parametrize(
    ("prior", "assignment"),
    [
        ((_EGRESS, _SUCCEEDED), "result = NULL"),
        ((_EGRESS, _SUCCEEDED), "result = 'failed'"),
        (
            (_EGRESS, _AMBIGUOUS),
            "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_absent', "
            "reconciled_at = now(), result = 'success'",
        ),
        ((_EGRESS, _AMBIGUOUS), "result = NULL"),
    ],
    ids=["erase-succeeded", "rewrite-succeeded", "reconcile-rewrites-ambiguous", "erase-ambiguous"],
)
def test_result_is_set_once(
    db_url: str, operation: uuid.UUID, prior: tuple[str, ...], assignment: str
) -> None:
    for step in prior:
        _update_as_qa(db_url, operation, step)

    _refused(db_url, operation, assignment, "result is set-once")


def test_reconciled_row_keeps_the_outcome_it_was_reached_from(
    db_url: str, operation: uuid.UUID
) -> None:
    _update_as_qa(db_url, operation, _EGRESS)
    _update_as_qa(db_url, operation, _AMBIGUOUS)
    _update_as_qa(db_url, operation, _ABSENT)

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql(
                f"SELECT result FROM {_TABLE} WHERE id = '{operation}'"
            ).scalar_one()
    finally:
        engine.dispose()
    assert result == "ambiguous"


def _superuser_url(postgres_container, db_url: str) -> str:
    """The container's privileged login on the same database: it bypasses row security."""
    from butlers.testing.migration import migration_bootstrap_db_url

    return migration_bootstrap_db_url(postgres_container, db_url.rsplit("/", 1)[1])


@pytest.mark.parametrize("held", [(_EGRESS,), (_EGRESS, _AMBIGUOUS)], ids=["egress", "ambiguous"])
def test_held_row_cannot_be_deleted_even_by_a_superuser(
    postgres_container, db_url: str, operation: uuid.UUID, held: tuple[str, ...]
) -> None:
    for step in held:
        _update_as_qa(db_url, operation, step)

    engine = create_engine(_superuser_url(postgres_container, db_url))
    try:
        with pytest.raises(DBAPIError, match="a held row cannot be deleted"):
            with engine.begin() as conn:
                conn.exec_driver_sql(f"DELETE FROM {_TABLE} WHERE id = '{operation}'")
    finally:
        engine.dispose()

    assert _stage_of(db_url, operation) in {"egress_started", "ambiguous"}


@pytest.mark.parametrize(
    "steps",
    [(), (_EGRESS, _SUCCEEDED), (_EGRESS, _AMBIGUOUS, _FOUND)],
    ids=["bound", "done", "reconciled"],
)
def test_non_held_row_can_still_be_deleted(
    postgres_container, db_url: str, operation: uuid.UUID, steps: tuple[str, ...]
) -> None:
    for step in steps:
        _update_as_qa(db_url, operation, step)

    engine = create_engine(_superuser_url(postgres_container, db_url))
    try:
        with engine.begin() as conn:
            deleted = conn.exec_driver_sql(f"DELETE FROM {_TABLE} WHERE id = '{operation}'")
            assert deleted.rowcount == 1
    finally:
        engine.dispose()


def _hardening_objects(conn) -> tuple[int, int, bool, int]:
    delete_trigger = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'qa_publication_operations_no_held_delete' "
        "AND NOT tgisinternal"
    ).scalar_one()
    constraint = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_qa_publication_operations_stage_result'"
    ).scalar_one()
    set_once = conn.exec_driver_sql(
        "SELECT bool_or(position('set-once' in prosrc) > 0) FROM pg_proc "
        "WHERE proname = 'qa_publication_operations_guard'"
    ).scalar_one()
    guard = conn.exec_driver_sql(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'qa_publication_operations_guard' "
        "AND NOT tgisinternal"
    ).scalar_one()
    return delete_trigger, constraint, bool(set_once), guard


def test_core_268_shared_hardening_survives_all_but_the_last_schema_downgrade(
    postgres_container,
) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert _hardening_objects(conn) == (1, 1, True, 1)

        # The other schema still runs core_268 code, so everything stays.
        command.downgrade(general, "core_267")
        with engine.connect() as conn:
            assert _hardening_objects(conn) == (1, 1, True, 1)

        # The last schema leaves: core_268 objects go and the core_267 guard is restored.
        command.downgrade(switchboard, "core_267")
        with engine.connect() as conn:
            assert _hardening_objects(conn) == (0, 0, False, 1)

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert _hardening_objects(conn) == (1, 1, True, 1)
    finally:
        engine.dispose()


# --- core_302: ambiguous outcomes cannot omit their result ---


def test_ambiguous_null_result_insert_is_refused(db_url: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            attempt = _insert_attempt(conn)
        with pytest.raises(DBAPIError, match="ck_qa_publication_operations_stage_result"):
            with engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL ROLE butler_qa_rw")
                conn.exec_driver_sql(
                    f"INSERT INTO {_TABLE} "
                    "(attempt_id, kind, stage, stage_order, binding_digest, request_digest) "
                    f"VALUES ('{attempt}', 'branch_push', 'ambiguous', 3, "
                    f"'{_DIGEST}', '{_DIGEST}')"
                )
    finally:
        engine.dispose()


def test_core_302_canonicalizes_legacy_rows_and_keeps_shared_hold_guards(
    postgres_container,
) -> None:
    """Only the last schema may restore the old CHECK; neither direction releases holds."""
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    command.downgrade(general, "core_301")
    command.downgrade(switchboard, "core_301")
    engine = create_engine(url)
    # Every previously allowed pair survives; only the ambiguous NULL is canonicalized.
    pairs = [
        ("bound", 1, None),
        ("egress_started", 2, None),
        ("succeeded", 3, "success"),
        ("failed", 3, "failed"),
        ("ambiguous", 3, None),
        ("ambiguous", 3, "ambiguous"),
        ("reconciled", 4, None),
        ("reconciled", 4, "ambiguous"),
    ]
    try:
        legacy_id = None
        for stage, order, outcome in pairs:
            with engine.begin() as conn:
                attempt = _insert_attempt(conn)
                conn.exec_driver_sql("SET LOCAL ROLE butler_qa_rw")
                inserted = conn.execute(
                    text(
                        f"INSERT INTO {_TABLE} "
                        "(attempt_id, kind, stage, stage_order, binding_digest, request_digest, "
                        "result, reconciliation, reconciled_at) VALUES "
                        "(:attempt, 'branch_push', :stage, :order, :digest, :digest, :result, "
                        "CASE WHEN :stage = 'reconciled' THEN 'resource_absent' END, "
                        "CASE WHEN :stage = 'reconciled' THEN now() END) RETURNING id"
                    ),
                    {
                        "attempt": attempt,
                        "stage": stage,
                        "order": order,
                        "digest": _DIGEST,
                        "result": outcome,
                    },
                ).scalar_one()
                if stage == "ambiguous" and outcome is None:
                    legacy_id = inserted
        assert legacy_id is not None
        with engine.connect() as conn:
            before = [
                dict(row)
                for row in conn.exec_driver_sql(f"SELECT * FROM {_TABLE} ORDER BY id").mappings()
            ]
            objects = _hardening_objects(conn)
            assert objects == (1, 1, True, 1)
        expected = [
            {**row, "result": "ambiguous"} if row["id"] == legacy_id else row for row in before
        ]

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert [
                dict(row)
                for row in conn.exec_driver_sql(f"SELECT * FROM {_TABLE} ORDER BY id").mappings()
            ] == expected
            assert _hardening_objects(conn) == objects
        _refused(url, legacy_id, "result = NULL", "result is set-once")
        _refused(url, legacy_id, _SUCCEEDED, "can only move forward")
        _refused(url, legacy_id, _FOUND, "ck_qa_publication_operations_exact_reconciliation")
        privileged = create_engine(_superuser_url(postgres_container, url))
        try:
            with pytest.raises(DBAPIError, match="a held row cannot be deleted"):
                with privileged.begin() as conn:
                    conn.exec_driver_sql(f"DELETE FROM {_TABLE} WHERE id = '{legacy_id}'")
        finally:
            privileged.dispose()

        # Supported names beginning with "pg" are not PostgreSQL system schemas.
        # Exercise the actual second schema, then restore the original two-schema path.
        bound_id = next(row["id"] for row in before if row["stage"] == "bound")
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER SCHEMA switchboard RENAME TO pgpeer")
        try:
            command.downgrade(general, "core_301")
            _refused(
                url,
                bound_id,
                "stage = 'ambiguous', stage_order = 3",
                "ck_qa_publication_operations_stage_result",
            )
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql("ALTER SCHEMA pgpeer RENAME TO switchboard")
            command.upgrade(general, "core@head")

        # A second schema still at core_302 must keep the strict CHECK in force.
        command.downgrade(general, "core_301")
        bound_id = next(row["id"] for row in before if row["stage"] == "bound")
        _refused(
            url,
            bound_id,
            "stage = 'ambiguous', stage_order = 3",
            "ck_qa_publication_operations_stage_result",
        )
        command.downgrade(switchboard, "core_301")
        # Historical compatibility returns, without erasing any canonicalized result or hold.
        _update_as_qa(url, bound_id, "stage = 'ambiguous', stage_order = 3")
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql(
                    f"SELECT result FROM {_TABLE} WHERE id = '{legacy_id}'"
                ).scalar_one()
                == "ambiguous"
            )
            assert _hardening_objects(conn) == objects
        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql(
                    f"SELECT count(*) FROM {_TABLE} WHERE stage = 'ambiguous' AND result IS NULL"
                ).scalar_one()
                == 0
            )
            assert _hardening_objects(conn) == objects
        # An exact read-only reconciliation remains the only way to release this hold.
        _update_as_qa(url, legacy_id, _ABSENT)
        assert _stage_of(url, legacy_id) == "reconciled"
    finally:
        engine.dispose()


# --- core_298: a held pr_create may reconcile by adopting the PR it created ---


def _adopt(object_id: str, new_object_id: str | None) -> str:
    new = "NULL" if new_object_id is None else f"'{new_object_id}'"
    return (
        "stage = 'reconciled', stage_order = 4, reconciliation = 'resource_found', "
        f"reconciled_object_id = '{object_id}', new_object_id = {new}, reconciled_at = now()"
    )


def _held_unbound_row(conn, kind: str) -> uuid.UUID:
    """An ambiguous row of ``kind`` that bound no expected object (as pr_create does)."""
    attempt = _insert_attempt(conn)
    conn.exec_driver_sql("SET ROLE butler_qa_rw")
    operation_id = uuid.uuid4()
    conn.exec_driver_sql(
        f"""
        INSERT INTO {_TABLE}
            (id, attempt_id, kind, stage, stage_order, binding_digest, request_digest, result)
        VALUES ('{operation_id}', '{attempt}', '{kind}', 'ambiguous', 3,
                '{_DIGEST}', '{_DIGEST}', 'ambiguous')
        """
    )
    conn.exec_driver_sql("RESET ROLE")
    return operation_id


@pytest.mark.parametrize(
    ("kind", "assignment", "allowed"),
    [
        ("pr_create", _adopt("123", "123"), True),
        ("pr_create", _adopt("123", None), False),
        ("pr_create", _adopt("123", "124"), False),
        ("pr_create", _adopt("0123", "0123"), False),
        ("pr_create", _adopt("pr-7", "pr-7"), False),
        ("branch_push", _adopt("123", "123"), False),
    ],
    ids=[
        "adopts-pr-number",
        "adoption-without-bound-new-object",
        "adoption-binds-another-pr",
        "not-a-pr-number",
        "pr-label-not-number",
        "only-pr-create-adopts",
    ],
)
def test_unbound_pr_create_reconciles_only_by_adopting_a_pr_number(
    db_url: str, kind: str, assignment: str, allowed: bool
) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            operation = _held_unbound_row(conn, kind)
    finally:
        engine.dispose()

    if allowed:
        _update_as_qa(db_url, operation, assignment)
        assert _stage_of(db_url, operation) == "reconciled"
    else:
        _refused(db_url, operation, assignment, _CONSTRAINT_EXACT)
        assert _stage_of(db_url, operation) == "ambiguous"


_CONSTRAINT_EXACT = "ck_qa_publication_operations_exact_reconciliation"


def _exact_check(conn) -> tuple[bool, bool]:
    """(adoption arm present, constraint validated)."""
    adoption, validated = conn.exec_driver_sql(
        "SELECT position('pr_create' in pg_get_constraintdef(oid)) > 0, convalidated "
        f"FROM pg_constraint WHERE conname = '{_CONSTRAINT_EXACT}'"
    ).one()
    return adoption, validated


def test_core_298_downgrade_restores_the_exact_check_around_adopted_rows(
    postgres_container,
) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            operation = _held_unbound_row(conn, "pr_create")
        _update_as_qa(url, operation, _adopt("123", "123"))
        with engine.connect() as conn:
            assert _exact_check(conn) == (True, True)

        # The other schema still runs core_298 code, so the adoption arm stays.
        command.downgrade(general, "core_297")
        with engine.connect() as conn:
            assert _exact_check(conn) == (True, True)

        # The last schema restores the core_267 rule; the adopted row keeps it NOT VALID.
        command.downgrade(switchboard, "core_297")
        with engine.connect() as conn:
            assert _exact_check(conn) == (False, False)
            assert _stage_of(url, operation) == "reconciled"

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert _exact_check(conn) == (True, True)
    finally:
        engine.dispose()


# --- core_300: the egress stamp that absence settles from ---

_STAMP_CONSTRAINT = "ck_qa_publication_operations_stage_stamp"


def _egress_stamp(db_url: str, operation_id: uuid.UUID):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.exec_driver_sql(
                f"SELECT egress_started_at, started_at FROM {_TABLE} WHERE id = '{operation_id}'"
            ).one()
    finally:
        engine.dispose()


def test_leaving_bound_stamps_the_egress_with_database_time(
    db_url: str, operation: uuid.UUID
) -> None:
    assert _egress_stamp(db_url, operation)[0] is None

    # A writer that predates core_300 does not set it; the guard anchors it anyway.
    _update_as_qa(db_url, operation, _EGRESS)

    stamped, started = _egress_stamp(db_url, operation)
    assert stamped is not None and stamped >= started


@pytest.mark.parametrize(
    "rewrite",
    ["egress_started_at = now() - interval '1 hour'", "egress_started_at = NULL"],
    ids=["backdated", "cleared"],
)
def test_egress_stamp_is_set_once(db_url: str, operation: uuid.UUID, rewrite: str) -> None:
    _update_as_qa(db_url, operation, f"{_EGRESS}, egress_started_at = now()")
    stamped, _ = _egress_stamp(db_url, operation)

    _refused(db_url, operation, rewrite, "egress_started_at is set-once")
    _refused(db_url, operation, f"{_AMBIGUOUS}, {rewrite}", "egress_started_at is set-once")

    assert _egress_stamp(db_url, operation)[0] == stamped
    assert _stage_of(db_url, operation) == "egress_started"


def test_bound_row_carries_no_egress_stamp(db_url: str, operation: uuid.UUID) -> None:
    _refused(db_url, operation, "egress_started_at = now()", _STAMP_CONSTRAINT)


def _stamp_objects(conn) -> tuple[int, int, bool]:
    column = conn.exec_driver_sql(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = 'qa_publication_operations' AND column_name = 'egress_started_at'"
    ).scalar_one()
    constraint = conn.exec_driver_sql(
        f"SELECT count(*) FROM pg_constraint WHERE conname = '{_STAMP_CONSTRAINT}'"
    ).scalar_one()
    guard = conn.exec_driver_sql(
        "SELECT bool_or(position('egress_started_at' in prosrc) > 0) FROM pg_proc "
        "WHERE proname = 'qa_publication_operations_guard'"
    ).scalar_one()
    return column, constraint, bool(guard)


def test_core_300_egress_stamp_survives_all_but_the_last_schema_downgrade(
    postgres_container,
) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    asyncio.run(run_migrations(url, chain="core", schema="switchboard"))
    general = _build_alembic_config(url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(url, chains=["core"], target_schema="switchboard")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            held = _held_unbound_row(conn, "pr_create")
        with engine.connect() as conn:
            assert _stamp_objects(conn) == (1, 1, True)

        # The other schema still runs core_300 code, so the column stays.
        command.downgrade(general, "core_299")
        with engine.connect() as conn:
            assert _stamp_objects(conn) == (1, 1, True)

        # The last schema leaves: the column goes and the core_268 guard still works.
        command.downgrade(switchboard, "core_299")
        with engine.connect() as conn:
            assert _stamp_objects(conn) == (0, 0, False)
        _update_as_qa(url, held, _ABSENT)
        assert _stage_of(url, held) == "reconciled"

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert _stamp_objects(conn) == (1, 1, True)
    finally:
        engine.dispose()


def _row_at(conn, stage_values: str) -> uuid.UUID:
    """A ``pr_create`` row inserted straight at ``stage_values`` (stage, order, result)."""
    attempt = _insert_attempt(conn)
    conn.exec_driver_sql("SET ROLE butler_qa_rw")
    operation_id = uuid.uuid4()
    conn.exec_driver_sql(
        f"""
        INSERT INTO {_TABLE}
            (id, attempt_id, kind, binding_digest, request_digest, stage, stage_order, result)
        VALUES ('{operation_id}', '{attempt}', 'pr_create', '{_DIGEST}', '{_DIGEST}',
                {stage_values})
        """
    )
    conn.exec_driver_sql("RESET ROLE")
    return operation_id


def test_core_300_backfills_only_rows_still_in_flight(postgres_container) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    config = _build_alembic_config(url, chains=["core"], target_schema="general")
    command.downgrade(config, "core_299")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            bound = _row_at(conn, "'bound', 1, NULL")
            in_flight = _row_at(conn, "'egress_started', 2, NULL")
            ambiguous = _row_at(conn, "'ambiguous', 3, 'ambiguous'")

        command.upgrade(config, "core@head")

        assert _egress_stamp(url, bound)[0] is None
        # The latest moment its egress can have begun: settles from the migration, not the bind.
        assert _egress_stamp(url, in_flight)[0] is not None
        assert _egress_stamp(url, ambiguous)[0] is None
    finally:
        engine.dispose()
