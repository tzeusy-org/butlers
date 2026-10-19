"""core_273 downgrade only restores the shared definer and key when the last schema leaves.

``public.enqueue_premise_amendments`` and ``public.insight_amendments`` are shared ``public``
objects; the core chain runs once per butler schema. A downgrade of one schema must not reopen
the forgeable core_255 definer or the evidence-erasing cascade for the schemas still at
core_273 (the core_246/266/268 installation-count pattern).
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "alembic/versions/core/core_273_insight_amendment_ledger_binding.py"


def _load():
    spec = importlib.util.spec_from_file_location("core_273", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_downgrade(installed: list[bool]) -> tuple[str, list[str]]:
    """Run ``downgrade`` with one schema per entry of ``installed`` (True = still at core_273)."""
    module = _load()
    mock_op = MagicMock()
    bind = mock_op.get_bind.return_value
    schema_names = [f"schema_{i}" for i in range(len(installed))]
    bind.dialect.identifier_preparer.quote.side_effect = lambda name: f'"{name}"'
    probes: list[str] = []
    answers = iter(installed)

    def exec_driver_sql(sql: str):
        result = MagicMock()
        if "pg_class" in sql:
            result.scalars.return_value = iter(schema_names)
        else:
            probes.append(sql)
            result.scalar.return_value = next(answers)
        return result

    bind.exec_driver_sql.side_effect = exec_driver_sql
    with patch.object(module, "op", mock_op):
        module.downgrade()
    return "\n".join(call.args[0] for call in mock_op.execute.call_args_list), probes


@pytest.mark.unit
def test_a_downgrade_while_another_schema_is_at_core_273_changes_nothing_shared() -> None:
    sql, _ = _run_downgrade([True, True])

    assert "enqueue_premise_amendments" not in sql
    assert "ADD CONSTRAINT" not in sql and "DROP CONSTRAINT" not in sql
    assert "pg_advisory_xact_lock" in sql


@pytest.mark.unit
def test_the_last_schema_to_leave_restores_the_core_255_body_and_the_cascading_key() -> None:
    sql, _ = _run_downgrade([True, False])

    assert "enqueue_premise_amendments" in sql
    assert "ON DELETE CASCADE NOT VALID" in sql


@pytest.mark.unit
def test_a_single_schema_downgrade_restores() -> None:
    sql, _ = _run_downgrade([True])

    assert "ON DELETE CASCADE NOT VALID" in sql


@pytest.mark.unit
def test_the_count_includes_core_273_itself_and_later_revisions() -> None:
    _, probes = _run_downgrade([True, False])

    assert probes and all(">= 273" in probe for probe in probes)


@pytest.mark.unit
def test_schemas_without_a_core_revision_do_not_count() -> None:
    _, probes = _run_downgrade([False, True])

    assert all("^core_[0-9]+$" in probe for probe in probes)


def _shared_state(conn) -> tuple[bool, int, bool]:
    source = conn.execute(
        text("SELECT prosrc FROM pg_proc WHERE proname = 'enqueue_premise_amendments'")
    ).scalar_one()
    foreign_keys = conn.execute(
        text(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'public.insight_amendments'::regclass AND contype = 'f'"
        )
    ).scalar_one()
    cascade_not_valid = bool(
        conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'public.insight_amendments'::regclass AND contype = 'f' "
                "AND confdeltype = 'c' AND NOT convalidated"
            )
        ).scalar_one()
    )
    return "public.owner_conditions" in source, int(foreign_keys), cascade_not_valid


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
def test_core_273_shared_objects_survive_all_but_the_last_schema_downgrade(
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
            assert _shared_state(conn) == (True, 0, False)

        # The other schema still runs core_273 code: the bound definer and no key stay.
        command.downgrade(general, "core_268")
        with engine.connect() as conn:
            assert _shared_state(conn) == (True, 0, False)

        # The last schema leaves: the core_255 body and the NOT VALID cascade come back.
        command.downgrade(switchboard, "core_268")
        with engine.connect() as conn:
            assert _shared_state(conn) == (False, 1, True)

        command.upgrade(general, "core@head")
        command.upgrade(switchboard, "core@head")
        with engine.connect() as conn:
            assert _shared_state(conn) == (True, 0, False)
    finally:
        engine.dispose()
