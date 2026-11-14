"""core_337 (resolve_home_action_status) and the fix-status premise probe (bu-jv1moa.114).

Statement-shape tests run anywhere. The parity and privilege tests need real PostgreSQL and run
CI-first where Docker is unavailable.
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs import home_prepared_status as status
from tests.jobs.test_home_prepared_status import CASES, NOW

_ROOT = Path(__file__).resolve().parents[2]
_CORE_337 = _ROOT / "alembic/versions/core/core_337_home_action_status.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load():
    spec = importlib.util.spec_from_file_location("core_337", _CORE_337)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    module = _load()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    fake_op.get_bind.return_value.exec_driver_sql.return_value.scalars.return_value = []
    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return statements


@pytest.mark.unit
def test_the_revision_extends_core_336_vendor_doors() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == ("core_337", "core_336")


@pytest.mark.unit
def test_the_definer_is_pinned_closed_and_public_execute_is_revoked_on_its_own() -> None:
    statements = [" ".join(s.split()) for s in _statements("upgrade")]
    function = next(s for s in statements if "FUNCTION public.resolve_home_action_status" in s)
    assert "SECURITY DEFINER" in function and "RETURNS text" in function
    assert "SET search_path = pg_catalog, pg_temp" in function
    assert (
        "REVOKE ALL ON FUNCTION public.resolve_home_action_status(uuid) FROM PUBLIC" in statements
    )
    for forbidden in ("tool_args", "agent_summary", "evidence", "result", "failure_reason", "why"):
        assert forbidden not in function
    for word in status.STATUSES:
        assert f"'{word}'" in function


@pytest.mark.unit
def test_the_sql_cancel_actor_matches_the_python_constant() -> None:
    assert _load()._CANCEL_ACTOR == status.CANCEL_ACTOR


@pytest.mark.unit
async def test_the_probe_compares_the_shown_status_and_holds_unknown() -> None:
    from butlers.roster_imports import roster_module  # noqa: PLC0415

    premises = roster_module("switchboard", "tools/insight/premises")
    probe = premises.PREMISE_PROBES[("home", "fix_status_unchanged")]
    action = str(uuid.uuid4())

    def answering(answer):
        return MagicMock(fetchval=AsyncMock(return_value=answer))

    assert (
        await probe(
            answering("approved_not_run"), {"action_id": action, "status": "approved_not_run"}
        )
        is True
    )
    assert (
        await probe(answering("succeeded"), {"action_id": action, "status": "approved_not_run"})
        is False
    )
    assert (
        await probe(answering("unknown"), {"action_id": action, "status": "approved_not_run"})
        is None
    )
    assert await probe(answering(None), {"action_id": action, "status": "approved_not_run"}) is None
    for bad in (
        {"action_id": "x", "status": "approved_not_run"},
        {"action_id": action},
        {"action_id": action, "status": "unknown"},
        {"action_id": action, "status": "bogus"},
    ):
        unused = answering("succeeded")
        assert await probe(unused, bad) is None
        unused.fetchval.assert_not_awaited()


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_definer_and_the_python_resolver_agree_on_every_case(db) -> None:
    pool = await db(
        "core", "home", "approvals", schemas={"home": "home", "approvals": "home"}, schema="home"
    )
    for name, action, receipt, expected in CASES:
        if receipt == "weird":  # the ledger's check constraint refuses it; unit-covered
            continue
        action_id = uuid.uuid4()
        if action is not None and action["status"] != "mystery":  # the table refuses it
            await pool.execute(
                "INSERT INTO pending_actions (id, tool_name, tool_args, status, decided_by,"
                " expires_at) VALUES ($1, 'home_call_service', '{}'::jsonb, $2, $3, $4)",
                action_id,
                action["status"],
                action["decided_by"],
                # The table's clock is fixed; rebase its expiry offsets onto the real one.
                datetime.now(UTC) + (action["expires_at"] - NOW)
                if action["expires_at"] is not None
                else None,
            )
        if receipt is not None:
            await pool.execute(
                "INSERT INTO ha_command_log (domain, service, approval_id, status)"
                " VALUES ('light', 'turn_off', $1, $2)",
                action_id,
                receipt,
            )
        in_sql = await pool.fetchval("SELECT public.resolve_home_action_status($1)", action_id)
        in_python = await status.prepared_action_status(pool, action_id)
        assert in_sql == in_python, name
        assert in_sql == expected, name


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_only_the_switchboard_may_execute_and_the_answer_is_one_word(db) -> None:
    pool = await db(
        "core", "home", "approvals", schemas={"home": "home", "approvals": "home"}, schema="home"
    )
    action_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status)"
        " VALUES ($1, 'home_call_service', '{\"target\": \"secret\"}'::jsonb, 'pending')",
        action_id,
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE butler_switchboard_rw")
        answer = await conn.fetchval("SELECT public.resolve_home_action_status($1)", action_id)
        missing = await conn.fetchval("SELECT public.resolve_home_action_status($1)", uuid.uuid4())
    assert (answer, missing) == ("awaiting_approval", "unknown")
    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', "
        "'public.resolve_home_action_status(uuid)', 'EXECUTE')"
    )
