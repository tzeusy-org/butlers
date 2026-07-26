"""Tests for core_190 domain_event_deliveries: attempt_count + failed_permanent status.

Covers:
- Migration revision chain
- Upgrade adds attempt_count column and expands status CHECK constraint
- Downgrade: reclassifies failed_permanent rows to failed before narrowing constraint
- Downgrade integration: failed_permanent rows survive as 'failed' after downgrade
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark_unit = pytest.mark.unit
pytestmark_integration = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_186_PATH = _REPO_ROOT / "alembic" / "versions" / "core" / "core_186_domain_events.py"
_CORE_190_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_190_domain_event_delivery_retry.py"
)


def _load_migration(path: Path, mod_name: str):
    """Import migration module by file path."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestCore190RevisionChain:
    """Verify core_190 metadata."""

    @pytest.mark.unit
    def test_revision_chain(self) -> None:
        """core_190 -> core_189, no branch/depends."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        assert mod.revision == "core_190"
        assert mod.down_revision == "core_189"
        assert mod.branch_labels is None
        assert mod.depends_on is None


class TestCore190UpgradeSQLShape:
    """Verify upgrade() SQL structure."""

    def _collect_execute_calls(self, mod) -> list[str]:
        """Run upgrade() with op.execute mocked; return SQL strings."""
        calls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.upgrade()
        return calls

    @pytest.mark.unit
    def test_upgrade_adds_attempt_count(self) -> None:
        """upgrade() adds attempt_count column."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)
        joined = "\n".join(sqls)
        assert "ADD COLUMN IF NOT EXISTS attempt_count" in joined

    @pytest.mark.unit
    def test_upgrade_expands_status_constraint(self) -> None:
        """upgrade() adds 'failed_permanent' to status CHECK constraint."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)
        joined = "\n".join(sqls)
        assert "failed_permanent" in joined
        assert (
            "pending" in joined
            and "delivered" in joined
            and "conflict" in joined
            and "failed" in joined
        )

    @pytest.mark.unit
    def test_upgrade_creates_status_index(self) -> None:
        """upgrade() creates idx_domain_event_deliveries_status_updated_at."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)
        joined = "\n".join(sqls)
        assert "idx_domain_event_deliveries_status_updated_at" in joined


class TestCore190DowngradeSQLShape:
    """Verify downgrade() SQL structure."""

    def _collect_execute_calls(self, mod) -> list[str]:
        """Run downgrade() with op.execute mocked; return SQL strings."""
        calls: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: calls.append(sql)
        with patch.object(mod, "op", mock_op):
            mod.downgrade()
        return calls

    @pytest.mark.unit
    def test_downgrade_has_update_before_constraint_change(self) -> None:
        """downgrade() reclassifies failed_permanent to failed BEFORE constraint change."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)

        # Find positions of UPDATE and DROP CONSTRAINT
        update_pos = next(
            (i for i, sql in enumerate(sqls) if "UPDATE" in sql and "failed_permanent" in sql),
            None,
        )
        drop_constraint_pos = next(
            (i for i, sql in enumerate(sqls) if "DROP CONSTRAINT" in sql),
            None,
        )

        assert update_pos is not None, "downgrade() must have UPDATE for failed_permanent rows"
        assert drop_constraint_pos is not None, "downgrade() must have DROP CONSTRAINT"
        assert update_pos < drop_constraint_pos, "UPDATE must come before DROP CONSTRAINT"

    @pytest.mark.unit
    def test_downgrade_drops_index(self) -> None:
        """downgrade() drops idx_domain_event_deliveries_status_updated_at."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)
        joined = "\n".join(sqls)
        assert "DROP INDEX IF EXISTS idx_domain_event_deliveries_status_updated_at" in joined

    @pytest.mark.unit
    def test_downgrade_drops_attempt_count_column(self) -> None:
        """downgrade() drops attempt_count column."""
        mod = _load_migration(_CORE_190_PATH, "core_190")
        sqls = self._collect_execute_calls(mod)
        joined = "\n".join(sqls)
        assert "DROP COLUMN IF EXISTS attempt_count" in joined


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCore190DowngradeIntegration:
    """Test downgrade with real data: failed_permanent rows survive as 'failed'."""

    @pytest.fixture
    async def pool_with_tables(self, provisioned_postgres_pool):
        """Provision a pool and create domain_event_deliveries table via core_186."""
        async with provisioned_postgres_pool() as pool:
            # Create the tables from core_186 (the base state before core_190)
            mod_186 = _load_migration(_CORE_186_PATH, "core_186")
            sqls_186: list[str] = []
            mock_op = MagicMock()
            mock_op.execute.side_effect = lambda sql: sqls_186.append(sql)
            with patch.object(mod_186, "op", mock_op):
                mod_186.upgrade()
            for sql in sqls_186:
                try:
                    await pool.execute(sql)
                except asyncpg.DuplicateTableError:
                    # Tables may already exist, that's fine
                    pass
                except Exception:
                    # Ignore FK/grant errors in test
                    pass
            yield pool

    async def _run_upgrade_190(self, pool) -> None:
        """Run core_190 upgrade against the pool."""
        mod_190 = _load_migration(_CORE_190_PATH, "core_190")
        sqls_190: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls_190.append(sql)
        with patch.object(mod_190, "op", mock_op):
            mod_190.upgrade()
        for sql in sqls_190:
            await pool.execute(sql)

    async def _run_downgrade_190(self, pool) -> None:
        """Run core_190 downgrade against the pool."""
        mod_190 = _load_migration(_CORE_190_PATH, "core_190")
        sqls_190: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: sqls_190.append(sql)
        with patch.object(mod_190, "op", mock_op):
            mod_190.downgrade()
        for sql in sqls_190:
            await pool.execute(sql)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_downgrade_reclassifies_failed_permanent_to_failed(
        self, pool_with_tables: asyncpg.Pool
    ) -> None:
        """Downgrade converts failed_permanent rows to failed; table still valid after downgrade."""
        pool = pool_with_tables

        # Create a test event
        event_id = await pool.fetchval(
            "INSERT INTO public.domain_events (event_type, source_butler) VALUES ($1, $2) RETURNING id",
            "test.event",
            "test_butler",
        )
        assert event_id is not None

        # Run core_190 upgrade
        await self._run_upgrade_190(pool)

        # Insert a delivery with status='failed_permanent' (only valid after upgrade)
        delivery_id = await pool.fetchval(
            """INSERT INTO public.domain_event_deliveries
               (event_id, subscriber_butler, status, attempt_count)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            event_id,
            "finance",
            "failed_permanent",
            5,
        )
        assert delivery_id is not None

        # Verify the row exists with failed_permanent status
        row_before = await pool.fetchrow(
            "SELECT status, attempt_count FROM public.domain_event_deliveries WHERE id = $1",
            delivery_id,
        )
        assert row_before["status"] == "failed_permanent"
        assert row_before["attempt_count"] == 5

        # Run downgrade
        await self._run_downgrade_190(pool)

        # Verify the row still exists but status='failed' (reclassified)
        row_after = await pool.fetchrow(
            "SELECT status FROM public.domain_event_deliveries WHERE id = $1",
            delivery_id,
        )
        assert row_after is not None
        assert row_after["status"] == "failed"

        # Verify attempt_count column is gone (dropped by downgrade)
        # We cannot select it anymore
        with pytest.raises(asyncpg.UndefinedColumnError):
            await pool.fetchval(
                "SELECT attempt_count FROM public.domain_event_deliveries WHERE id = $1",
                delivery_id,
            )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_downgrade_leaves_other_status_unchanged(
        self, pool_with_tables: asyncpg.Pool
    ) -> None:
        """Downgrade leaves pending/delivered/conflict/failed rows unchanged."""
        pool = pool_with_tables

        # Create a test event
        event_id = await pool.fetchval(
            "INSERT INTO public.domain_events (event_type, source_butler) VALUES ($1, $2) RETURNING id",
            "test.event",
            "test_butler",
        )
        assert event_id is not None

        # Run core_190 upgrade
        await self._run_upgrade_190(pool)

        # Insert multiple deliveries with different statuses
        statuses = ["pending", "delivered", "conflict", "failed"]
        delivery_ids = {}
        for status in statuses:
            delivery_id = await pool.fetchval(
                """INSERT INTO public.domain_event_deliveries
                   (event_id, subscriber_butler, status)
                   VALUES ($1, $2, $3) RETURNING id""",
                event_id,
                f"butler_{status}",
                status,
            )
            delivery_ids[status] = delivery_id

        # Run downgrade
        await self._run_downgrade_190(pool)

        # Verify all statuses remain unchanged
        for status, delivery_id in delivery_ids.items():
            row = await pool.fetchrow(
                "SELECT status FROM public.domain_event_deliveries WHERE id = $1",
                delivery_id,
            )
            assert row["status"] == status, f"Status {status} should remain unchanged"
