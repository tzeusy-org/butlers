"""core_273: premise amendments bound to the resolved ledger, kept after cleanup (bu-q7vx1q.32).

The unit tests read the SQL the migration emits; the integration tests run that same SQL
against a migrated database (Docker; CI-only).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.core.definer_search_path import is_pinned

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "alembic/versions/core/core_273_insight_amendment_ledger_binding.py"
_CORE_255 = _ROOT / "alembic/versions/core/core_255_insight_premise_binding.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emitted(fn_name: str) -> str:
    mod = _load(_MIGRATION, "core_273")
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return "\n".join(call.args[0] for call in mock_op.execute.call_args_list)


@pytest.mark.unit
def test_revision_chain_is_a_single_step() -> None:
    mod = _load(_MIGRATION, "core_273")

    assert (mod.revision, mod.down_revision) == ("core_273", "core_268")
    assert mod.branch_labels is None and mod.depends_on is None


@pytest.mark.unit
def test_the_new_definer_verifies_the_resolved_ledger_row() -> None:
    body = _load(_MIGRATION, "core_273")._NEW_ENQUEUE_FN

    assert "FROM public.owner_conditions oc" in body
    assert "oc.source = p_source" in body
    assert "oc.fingerprint = p_fingerprint" in body
    assert "oc.state = 'resolved'" in body
    assert "oc.resolved_at = p_resolved_at" in body
    # The ledger row's own value, never the caller's, keys the episode.
    assert "oc.fingerprint || '@' || oc.resolved_at::text" in body
    assert "p_resolved_at::text" not in body


@pytest.mark.unit
def test_the_new_definer_keeps_its_signature_and_security_posture() -> None:
    old = _load(_CORE_255, "core_255")
    new = _load(_MIGRATION, "core_273")

    def header(sql: str) -> str:
        return sql[: sql.index("$fn$")]

    assert header(new._NEW_ENQUEUE_FN) == header(old.CREATE_ENQUEUE_FN)
    assert "SECURITY DEFINER" in new._NEW_ENQUEUE_FN
    assert is_pinned(["search_path=pg_catalog, pg_temp"])
    assert "SET search_path = pg_catalog, pg_temp" in new._NEW_ENQUEUE_FN


@pytest.mark.unit
def test_the_migration_adds_no_grant() -> None:
    assert "GRANT" not in _emitted("upgrade")


@pytest.mark.unit
def test_upgrade_drops_the_cascading_foreign_key_and_keeps_the_column_constraints() -> None:
    sql = _emitted("upgrade")

    assert "DROP CONSTRAINT IF EXISTS insight_amendments_candidate_id_fkey" in sql
    assert "NOT NULL" not in sql and "UNIQUE" not in sql


@pytest.mark.unit
def test_downgrade_restores_the_core_255_body_verbatim_and_a_cascading_key_not_valid() -> None:
    sql = _emitted("downgrade")
    old = _load(_CORE_255, "core_255").CREATE_ENQUEUE_FN

    assert old.strip() in sql.replace("''", "'")
    assert "ON DELETE CASCADE NOT VALID" in sql
    assert sql.index("DROP CONSTRAINT") < sql.index("ADD CONSTRAINT")


@pytest.mark.unit
async def test_the_fixture_ddl_mirrors_the_migration_with_no_candidate_foreign_key() -> None:
    from unittest.mock import AsyncMock

    from butlers.tools.switchboard.insight.broker import create_insight_tables

    pool = AsyncMock()
    await create_insight_tables(pool)

    ddl = next(
        call.args[0]
        for call in pool.execute.await_args_list
        if "CREATE TABLE IF NOT EXISTS insight_amendments" in call.args[0]
    )
    assert "REFERENCES" not in ddl and "CASCADE" not in ddl
    assert "candidate_id UUID NOT NULL," in ddl and "UNIQUE (candidate_id, episode_key)" in ddl


@pytest.fixture
async def pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        yield p
    finally:
        await p.close()


_FK_SQL = (
    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.insight_amendments'::regclass "
    "AND contype = 'f'"
)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
async def test_a_migrated_database_has_no_foreign_key_and_a_pinned_verifying_definer(pool) -> None:
    assert await pool.fetchval(_FK_SQL) == 0
    config = await pool.fetchval(
        "SELECT proconfig FROM pg_proc WHERE proname = 'enqueue_premise_amendments'"
    )
    assert is_pinned(config)
    source = await pool.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'enqueue_premise_amendments'"
    )
    assert "public.owner_conditions" in source


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
async def test_downgrade_then_upgrade_round_trips_and_keeps_orphaned_evidence(pool) -> None:
    await pool.execute(
        "INSERT INTO public.insight_amendments (candidate_id, episode_key, reason) "
        "VALUES (gen_random_uuid(), 'fp@t', 'premise_resolved')"
    )

    await pool.execute(_emitted("downgrade"))
    assert await pool.fetchval(_FK_SQL) == 1
    source = await pool.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'enqueue_premise_amendments'"
    )
    assert "owner_conditions" not in source
    assert await pool.fetchval("SELECT count(*) FROM public.insight_amendments") == 1

    await pool.execute(_emitted("upgrade"))
    assert await pool.fetchval(_FK_SQL) == 0
    assert await pool.fetchval("SELECT count(*) FROM public.insight_amendments") == 1
