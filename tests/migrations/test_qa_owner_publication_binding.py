"""Shared target bindings survive core migration replay and refused rollback."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = pytest.mark.integration


def test_replay_and_rollback_preserve_bound_export(postgres_container):
    url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(url, chains=["core"], target_schema="general")
    second = _build_alembic_config(url, chains=["core"], target_schema="travel")
    command.upgrade(first, "core@head")
    attempt = uuid.uuid4()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO public.healing_attempts
                    (id, fingerprint, butler_name, status, severity, exception_type, call_site)
                VALUES (:id, :fingerprint, 'qa', 'failed', 2, 'Error', 'a:b')
            """),
                {"id": attempt, "fingerprint": uuid.uuid4().hex},
            )
            conn.exec_driver_sql("SET ROLE butler_qa_rw")
            conn.execute(
                text("""
                INSERT INTO public.qa_publication_exports
                    (attempt_id, base_sha, head_sha, commits, objects, digest,
                     target_repository, target_base_ref, target_branch)
                VALUES (:id, :base, :head, ARRAY[:head], ''::bytea, :digest, 'acme/butlers', 'main', 'qa/a')
            """),
                {"id": attempt, "base": "b" * 40, "head": "c" * 40, "digest": "d" * 64},
            )
            conn.exec_driver_sql("RESET ROLE")
        command.upgrade(second, "core@head")
        command.upgrade(second, "core@head")
        with engine.begin() as conn:
            # Bootstrap can widen grants; the trigger must still protect the target.
            conn.exec_driver_sql(
                "GRANT UPDATE (target_repository) ON public.qa_publication_exports TO butler_qa_rw"
            )
        with pytest.raises(DBAPIError, match="target is immutable"):
            with engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL ROLE butler_qa_rw")
                conn.execute(
                    text(
                        "UPDATE public.qa_publication_exports SET target_repository='other/repo' WHERE attempt_id=:id"
                    ),
                    {"id": attempt},
                )
        with pytest.raises(RuntimeError, match="durable QA publication bindings"):
            command.downgrade(first, "core_375")
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT target_repository, target_base_ref, target_branch FROM public.qa_publication_exports WHERE attempt_id=:id"
                ),
                {"id": attempt},
            ).one()
            assert tuple(row) == ("acme/butlers", "main", "qa/a")
    finally:
        engine.dispose()
