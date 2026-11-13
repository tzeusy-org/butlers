"""home_006 (departure watch and cards) and core_335 (resolve_home_departure) (bu-jv1moa.8 PR-B).

The statement-shape tests run anywhere. The constraint and downgrade tests need real
PostgreSQL and run CI-first where Docker is unavailable.
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOME_006 = _ROOT / "roster/home/migrations/006_departure_watch.py"
_CORE_335 = _ROOT / "alembic/versions/core/core_335_home_departure_definer.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(module, direction: str) -> list[str]:
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    fake_op.get_bind.return_value.exec_driver_sql.return_value.scalars.return_value = []
    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return statements


@pytest.mark.unit
def test_the_revisions_extend_their_chains() -> None:
    home = _load(_HOME_006, "home_006")
    core = _load(_CORE_335, "core_335")

    assert (home.revision, home.down_revision) == ("home_006", "home_005")
    assert (core.revision, core.down_revision) == ("core_335", "core_334")


@pytest.mark.unit
def test_the_definer_is_pinned_and_public_execute_is_revoked_on_its_own() -> None:
    statements = [" ".join(s.split()) for s in _statements(_load(_CORE_335, "c335"), "upgrade")]
    function = next(s for s in statements if "FUNCTION public.resolve_home_departure" in s)

    assert "SECURITY DEFINER" in function
    assert "SET search_path = pg_catalog, pg_temp" in function
    assert "home.departure_cards" in function
    assert "RETURNS boolean" in function
    assert "REVOKE ALL ON FUNCTION public.resolve_home_departure(uuid) FROM PUBLIC" in statements


@_docker
@pytest.mark.integration
async def test_departure_tables_constrain_labels_states_and_downgrade(
    provisioned_postgres_pool,
) -> None:
    module = _load(_HOME_006, "home_006_pg")
    async with provisioned_postgres_pool(schema="public") as pool:
        for statement in _statements(module, "upgrade"):
            await pool.execute(statement)
        await pool.execute(
            "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
            "VALUES ('binary_sensor.front_door', 'Front door', ARRAY['off'])"
        )
        for label, states in (("   ", ["off"]), ("x" * 81, ["off"]), ("Door", [])):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
                    "VALUES ('lock.back', $1, $2)",
                    label,
                    states,
                )
        for state, reason in (("open", "late"), ("closed", None), ("ajar", None)):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "INSERT INTO departure_cards "
                    "(edge_event_id, confirmed_at, state, closed_reason, checked_at) "
                    "VALUES ($1, now(), $2, $3, now())",
                    uuid.uuid4(),
                    state,
                    reason,
                )

        for statement in _statements(module, "downgrade"):
            await pool.execute(statement)
        assert await pool.fetchval("SELECT to_regclass('departure_watch')") is None
        assert await pool.fetchval("SELECT to_regclass('departure_cards')") is None
