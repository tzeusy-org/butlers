"""Regression tests for the narrow core_178 audit failure-outcome repair."""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_178_audit_log_failed_outcome_backfill.py"
)

_PROVISION_AUDIT_LOG = """
CREATE TABLE public.audit_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    note       TEXT,
    ip         INET,
    request_id UUID,
    metadata   JSONB,
    result     TEXT,
    error      TEXT
);
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_178", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool, fn_name: str) -> None:
    """Replay the exact SQL emitted by a migration against real Postgres."""
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


@pytest.mark.unit
def test_migration_revision_chain_and_narrow_guard() -> None:
    mod = _load_migration()

    assert mod.revision == "core_178"
    assert mod.down_revision == "core_177"
    assert mod.branch_labels is None
    assert mod.depends_on is None
    assert "action = 'failed'" in mod.REPAIR_FAILED_OUTCOMES_SQL
    assert "result IS NULL" in mod.REPAIR_FAILED_OUTCOMES_SQL
    assert "SET result = 'error'" in mod.REPAIR_FAILED_OUTCOMES_SQL


@pytest.mark.unit
def test_migration_never_rewrites_unrelated_historical_columns() -> None:
    src = _MIGRATION_PATH.read_text()
    for column in ("ts", "actor", "action", "target", "note", "error", "metadata"):
        assert f"SET {column}" not in src


@pytest.mark.unit
def test_downgrade_does_not_mutate_append_only_history() -> None:
    mod = _load_migration()
    fake_op = MagicMock()
    with patch.object(mod, "op", fake_op):
        mod.downgrade()
    fake_op.execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_noops_when_audit_log_is_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _apply(pool, "upgrade")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_repairs_only_missing_failed_outcomes(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        ts = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
        repaired_id = await pool.fetchval(
            """
            INSERT INTO public.audit_log (ts, actor, action, target, note, result, error)
            VALUES ($1, $2, 'failed', $3, $4, NULL, $5)
            RETURNING id
            """,
            ts,
            "owner",
            "u:spotify",
            "Probe failed: invalid token",
            "invalid token",
        )
        already_error_id = await pool.fetchval(
            """
            INSERT INTO public.audit_log (actor, action, note, result, error)
            VALUES ('owner', 'failed', 'already classified', 'error', 'existing diagnostic')
            RETURNING id
            """
        )
        non_failure_id = await pool.fetchval(
            """
            INSERT INTO public.audit_log (actor, action, note, result)
            VALUES ('owner', 'verified', 'success still unclassified historically', NULL)
            RETURNING id
            """
        )

        await _apply(pool, "upgrade")

        repaired = await pool.fetchrow(
            "SELECT ts, actor, action, target, note, result, error FROM public.audit_log WHERE id = $1",
            repaired_id,
        )
        assert dict(repaired) == {
            "ts": ts,
            "actor": "owner",
            "action": "failed",
            "target": "u:spotify",
            "note": "Probe failed: invalid token",
            "result": "error",
            "error": "invalid token",
        }
        assert await pool.fetchrow(
            "SELECT action, result, error FROM public.audit_log WHERE id = $1", already_error_id
        ) == ("failed", "error", "existing diagnostic")
        assert await pool.fetchrow(
            "SELECT action, result FROM public.audit_log WHERE id = $1", non_failure_id
        ) == ("verified", None)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_idempotent_for_repaired_rows(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        row_id = await pool.fetchval(
            "INSERT INTO public.audit_log (actor, action) VALUES ('owner', 'failed') RETURNING id"
        )

        await _apply(pool, "upgrade")
        first = await pool.fetchrow(
            "SELECT result, xmin::text AS xmin FROM public.audit_log WHERE id = $1", row_id
        )
        await _apply(pool, "upgrade")
        second = await pool.fetchrow(
            "SELECT result, xmin::text AS xmin FROM public.audit_log WHERE id = $1", row_id
        )

        assert first["result"] == "error"
        assert second["result"] == "error"
        assert second["xmin"] == first["xmin"]
