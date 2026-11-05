"""core_313: goal taint on shared D6 stores and goal lineage on domain events (bu-pkuny2).

Real PostgreSQL (CI-only): upgrade adds the nullable columns idempotently, the
downgrade of the last schema removes them, and an upgrade replayed after it
restores them.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from butlers.core.goal_lineage import taint_read
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_CORE = Path(__file__).resolve().parents[2] / "alembic/versions/core"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
_COLUMNS = """
    SELECT table_name || '.' || column_name FROM information_schema.columns
    WHERE table_schema = 'public' AND (
        (table_name IN ('captures', 'dashboard_messages', 'insight_candidates')
         AND column_name IN ('taint_goal_id', 'taint_invocation_id'))
        OR (table_name = 'domain_events' AND column_name IN
            ('goal_id', 'goal_invocation_id', 'goal_effect_id', 'goal_exposed')))
    ORDER BY 1
"""
_EXPECTED = [
    "captures.taint_goal_id",
    "captures.taint_invocation_id",
    "dashboard_messages.taint_goal_id",
    "dashboard_messages.taint_invocation_id",
    "domain_events.goal_effect_id",
    "domain_events.goal_exposed",
    "domain_events.goal_id",
    "domain_events.goal_invocation_id",
    "insight_candidates.taint_goal_id",
    "insight_candidates.taint_invocation_id",
]


def _core_313():
    path = _CORE / "core_313_goal_taint_and_event_lineage.py"
    spec = importlib.util.spec_from_file_location("_core_313_goal_taint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_revision_follows_core_312() -> None:
    mod = _core_313()
    assert (mod.revision, mod.down_revision) == ("core_313", "core_312")


@pytest.fixture
def engine(postgres_container):
    engine = create_engine(
        create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])
    )
    yield engine
    engine.dispose()


def _run(engine, step: str) -> None:
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(_core_313(), step)()


@_docker
@pytest.mark.integration
def test_columns_are_added_idempotently_and_removed_by_the_last_downgrade(engine) -> None:
    with engine.begin() as conn:
        assert list(conn.execute(text(_COLUMNS)).scalars()) == _EXPECTED
    _run(engine, "downgrade")
    with engine.begin() as conn:
        assert list(conn.execute(text(_COLUMNS)).scalars()) == []
    _run(engine, "upgrade")
    _run(engine, "upgrade")
    with engine.begin() as conn:
        assert list(conn.execute(text(_COLUMNS)).scalars()) == _EXPECTED


@_docker
@pytest.mark.integration
def test_readers_return_the_stamp_and_tolerate_a_schema_before_core_313(engine) -> None:
    goal = "0b6a3a52-8d53-4a52-9d55-2f4ad4a4a001"
    invocation = "0b6a3a52-8d53-4a52-9d55-2f4ad4a4a002"
    stamped = (
        f"SELECT {taint_read('r')} FROM (VALUES ('{goal}'::uuid, '{invocation}'::uuid))"
        " AS r(taint_goal_id, taint_invocation_id)"
    )
    with engine.begin() as conn:
        assert tuple(conn.execute(text(stamped)).one()) == (UUID(goal), UUID(invocation))
    _run(engine, "downgrade")
    with engine.begin() as conn:  # the columns are gone; the readers still run
        for table in ("dashboard_messages", "insight_candidates"):
            conn.execute(text(f"SELECT {taint_read('r')} FROM public.{table} r"))
        unstamped = f"SELECT {taint_read('r')} FROM (VALUES (1)) AS r(id)"
        assert tuple(conn.execute(text(unstamped)).one()) == (None, None)
