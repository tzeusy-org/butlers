"""home_008 (arrival cards) and core_338 (resolve_home_arrival) (bu-jv1moa.116).

Statement-shape tests run anywhere; the constraint test needs real PostgreSQL.
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
_HOME_008 = _ROOT / "roster/home/migrations/008_arrival_cards.py"
_CORE_338 = _ROOT / "alembic/versions/core/core_338_home_arrival_definer.py"
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
    home = _load(_HOME_008, "home_008")
    core = _load(_CORE_338, "core_338")
    assert (home.revision, home.down_revision) == ("home_008", "home_007")
    assert core.revision == "core_338"


@pytest.mark.unit
def test_the_definer_is_pinned_boolean_and_public_execute_is_revoked_on_its_own() -> None:
    statements = [" ".join(s.split()) for s in _statements(_load(_CORE_338, "c338"), "upgrade")]
    function = next(s for s in statements if "FUNCTION public.resolve_home_arrival" in s)
    assert "SECURITY DEFINER" in function and "RETURNS boolean" in function
    assert "SET search_path = pg_catalog, pg_temp" in function
    assert "home.arrival_cards" in function
    assert "REVOKE ALL ON FUNCTION public.resolve_home_arrival(uuid) FROM PUBLIC" in statements


@pytest.mark.unit
def test_the_evidence_row_stores_no_lines_and_names_a_closed_reason_set() -> None:
    table = " ".join(
        next(
            s for s in _statements(_load(_HOME_008, "h008"), "upgrade") if "arrival_cards (" in s
        ).split()
    )
    assert "'withdrawn_changed'" in table and "'nothing_to_report'" in table
    assert "message" not in table and "label" not in table


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_arrival_cards_constrain_state_and_reason(db) -> None:
    pool = await db("core", "home", schemas={"home": "home"}, schema="home")
    edge = uuid.uuid4()
    insert = (
        "INSERT INTO arrival_cards (edge_event_id, confirmed_at, state, closed_reason, checked_at)"
        " VALUES ($1, now(), $2, $3, now())"
    )
    await pool.execute(insert, edge, "closed", "short_absence")
    for state, reason in (("open", "short_absence"), ("closed", None), ("closed", "bogus")):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(insert, uuid.uuid4(), state, reason)
