"""core_367 (bu-ejow5p.3): ``qa_patrols`` accepts ``halted`` and records its ``halt_reason``.

Real-Postgres contract; Docker-only like its migration siblings.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


def _run(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement))
            return [tuple(row) for row in result.fetchall()] if result.returns_rows else None
    finally:
        engine.dispose()


def test_halted_is_accepted_with_a_reason_and_other_values_are_still_refused(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, "core_367")

    _run(
        db_url,
        "INSERT INTO public.qa_patrols (status, halt_reason) VALUES ('halted', 'circuit_breaker')",
    )
    assert _run(db_url, "SELECT status, halt_reason FROM public.qa_patrols") == [
        ("halted", "circuit_breaker")
    ]
    with pytest.raises(Exception, match="ck_qa_patrols_status"):
        _run(db_url, "INSERT INTO public.qa_patrols (status) VALUES ('stalled')")


def test_downgrade_rereads_halted_rows_as_suppressed_and_a_rerun_is_clean(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(config, "core_367")
    _run(
        db_url,
        "INSERT INTO public.qa_patrols (status, halt_reason) VALUES ('halted', 'no_model')",
    )

    command.downgrade(config, "core_362")
    assert _run(db_url, "SELECT status FROM public.qa_patrols") == [("suppressed",)]
    assert _run(
        db_url,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'qa_patrols' AND column_name = 'halt_reason'",
    ) == [(0,)]

    command.upgrade(config, "core_367")
    assert _run(db_url, "SELECT status FROM public.qa_patrols") == [("suppressed",)]


def _status_check(db_url: str) -> str:
    return _run(
        db_url,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'public.qa_patrols'::regclass AND conname = 'ck_qa_patrols_status'",
    )[0][0]


def test_two_schemas_share_the_status_and_only_the_last_downgrade_reverts_it(
    postgres_container,
) -> None:
    """core_367 runs once per schema-local core chain over the one shared ``qa_patrols``."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_367")
    command.upgrade(second, "core_367")  # replays over the widened check and the column
    _run(
        db_url, "INSERT INTO public.qa_patrols (status, halt_reason) VALUES ('halted', 'no_model')"
    )
    assert "'halted'" in _status_check(db_url)

    command.downgrade(first, "core_362")
    assert "'halted'" in _status_check(db_url), "another schema still depends on the status"
    assert _run(db_url, "SELECT status FROM public.qa_patrols") == [("halted",)]

    command.downgrade(second, "core_362")
    assert "'halted'" not in _status_check(db_url)
    assert _run(db_url, "SELECT status FROM public.qa_patrols") == [("suppressed",)]
