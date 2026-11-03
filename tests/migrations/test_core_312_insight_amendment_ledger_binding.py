"""core_312: premise amendments bound to the resolved ledger, kept after cleanup (bu-q7vx1q.32).

The forged-call and evidence-survival behaviour is pinned in
tests/modules/test_insight_premise_binding.py; this file pins the revision's lifecycle on
real PostgreSQL (CI-only): upgrade drops the cascading key and admits ``orphaned``, and
downgrade restores the core_255 definer and cascade without losing amendment rows, and an
upgrade that cannot replace a definer owned by another role fails instead of passing.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_CORE = Path(__file__).resolve().parents[2] / "alembic/versions/core"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
_FK = (
    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.insight_amendments'::regclass "
    "AND contype = 'f'"
)
_BODY = "SELECT prosrc FROM pg_proc WHERE proname = 'enqueue_premise_amendments'"


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(f"_{filename[:8]}_amend", _CORE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_312():
    return _load("core_312_insight_amendment_ledger_binding.py")


def test_the_revision_follows_core_311_and_downgrade_restores_the_core_255_body() -> None:
    mod = _core_312()
    assert (mod.revision, mod.down_revision) == ("core_312", "core_311")
    assert mod.CORE_255_ENQUEUE_FN == _load("core_255_insight_premise_binding.py").CREATE_ENQUEUE_FN


@pytest.fixture
def databases(postgres_container):
    """(ordinary migration engine, privileged bootstrap engine) on one migrated database."""
    name = migration_db_name()
    engine = create_engine(create_migrated_test_db(postgres_container, name, chains=["core"]))
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
    yield engine, admin
    engine.dispose()
    admin.dispose()


@pytest.fixture
def engine(databases):
    return databases[0]


def _run(engine, step: str) -> None:
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(_core_312(), step)()


@_docker
@pytest.mark.integration
def test_downgrade_restores_cascade_and_trusting_body_then_upgrade_replays(engine) -> None:
    with engine.begin() as conn:
        assert conn.execute(text(_FK)).scalar() == 0
        assert "owner_conditions" in conn.execute(text(_BODY)).scalar()
        conn.execute(
            text(
                "INSERT INTO public.insight_amendments (candidate_id, episode_key, reason, state) "
                "VALUES (gen_random_uuid(), 'fp@t', 'premise_resolved', 'orphaned')"
            )
        )

    _run(engine, "downgrade")
    with engine.begin() as conn:
        assert conn.execute(text(_FK)).scalar() == 1
        assert "owner_conditions" not in conn.execute(text(_BODY)).scalar()
        # The orphan survives the restored (NOT VALID) key, folded to a core_255 state.
        assert conn.execute(text("SELECT state FROM public.insight_amendments")).scalar() == (
            "folded"
        )

    _run(engine, "upgrade")
    _run(engine, "upgrade")
    with engine.begin() as conn:
        assert conn.execute(text(_FK)).scalar() == 0
        assert "owner_conditions" in conn.execute(text(_BODY)).scalar()
        assert conn.execute(text("SELECT count(*) FROM public.insight_amendments")).scalar() == 1


@_docker
@pytest.mark.integration
def test_upgrade_fails_loudly_when_another_role_owns_the_definer(databases) -> None:
    engine, admin = databases
    _run(engine, "downgrade")
    with admin.begin() as conn:  # the bootstrap-owned shape bu-jjv2du describes
        conn.execute(
            text(
                "ALTER FUNCTION public.enqueue_premise_amendments(text, text, timestamptz, text) "
                "OWNER TO CURRENT_USER"
            )
        )

    with pytest.raises(DBAPIError, match="was not replaced"):
        _run(engine, "upgrade")
    with engine.begin() as conn:
        assert "owner_conditions" not in conn.execute(text(_BODY)).scalar()
