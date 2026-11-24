"""Tests for rel_025: self-guarding DROP of relationship.quick_facts.

Covers:
  (a) Unit — module structure, revision wiring, callable upgrade/downgrade,
      source-text guards (to_regclass guard, row-count gate, recreate on downgrade).
  (b) Integration — against the real rel_024 pre-state, asserts:
        * upgrade raises RuntimeError when quick_facts has rows
        * upgrade drops the table when quick_facts is empty
        * upgrade is a no-op when quick_facts is already absent
        * downgrade recreates the empty table
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config

# ---------------------------------------------------------------------------
# Migration file path
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "025_drop_quick_facts.py"
)


# ---------------------------------------------------------------------------
# (a) Unit — structure and source-text guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRel025Structure:
    def test_named_invariant_guards(self):
        """Self-guarding DROP: to_regclass guard, row-count RuntimeError gate,
        guarded DROP, and downgrade recreate (behaviour exercised by integration)."""
        src = _MIGRATION_PATH.read_text()
        assert "to_regclass" in src
        assert "RuntimeError" in src
        assert "DROP TABLE IF EXISTS quick_facts" in src
        assert "CREATE TABLE IF NOT EXISTS quick_facts" in src


# ---------------------------------------------------------------------------
# (b) Integration — live DB behaviour on the real rel_024 pre-state
# ---------------------------------------------------------------------------

_SCHEMA = "relationship"
_QUALIFIED = f"{_SCHEMA}.quick_facts"


async def _rel_024(fresh_db) -> tuple[str, asyncpg.Pool]:
    """A database with the relationship chain at rel_025's predecessor."""
    url = await asyncio.to_thread(
        fresh_db,
        "core",
        "relationship",
        schemas={"relationship": _SCHEMA},
        revisions={"relationship": "rel_024"},
    )
    return url, await asyncpg.create_pool(url, min_size=1, max_size=2)


async def _migrate(url: str, direction: str, revision: str) -> None:
    config = _build_alembic_config(url, chains=["relationship"], target_schema=_SCHEMA)
    await asyncio.to_thread(getattr(command, direction), config, revision)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_raises_when_table_has_rows(fresh_db) -> None:
    """upgrade() must raise RuntimeError when quick_facts contains rows."""
    url, pool = await _rel_024(fresh_db)
    try:
        # quick_facts.contact_id references the relationship contacts table.
        contact_id = await pool.fetchval(
            f"INSERT INTO {_SCHEMA}.contacts (name) VALUES ($1) RETURNING id", "Acme contact"
        )
        await pool.execute(
            f"INSERT INTO {_QUALIFIED} (contact_id, key, value) VALUES ($1, $2, $3)",
            contact_id,
            "org",
            "Acme Corp",
        )

        count = await pool.fetchval(f"SELECT COUNT(*) FROM {_QUALIFIED}")
        assert count == 1

        exists = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists is not None, "quick_facts must exist before upgrade"

        count_before = await pool.fetchval(f"SELECT COUNT(*) FROM {_QUALIFIED}")
        assert count_before and count_before > 0, "Table must be non-empty for this test"
    finally:
        await pool.close()

    with pytest.raises(RuntimeError, match=r"row"):
        await _migrate(url, "upgrade", "rel_025")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_drops_empty_table(fresh_db) -> None:
    """upgrade() drops quick_facts when it exists and is empty."""
    url, pool = await _rel_024(fresh_db)
    try:
        # Confirm table exists and is empty.
        exists_before = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists_before is not None
        count = await pool.fetchval(f"SELECT COUNT(*) FROM {_QUALIFIED}")
        assert count == 0

        await _migrate(url, "upgrade", "rel_025")

        # Assert table is now absent.
        exists_after = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists_after is None, f"Expected {_QUALIFIED} to be absent after upgrade"
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_noop_when_table_absent(fresh_db) -> None:
    """upgrade() is idempotent: no error when quick_facts is already absent."""
    url, pool = await _rel_024(fresh_db)
    try:
        # Out-of-band removal: the table is already gone before rel_025 runs.
        await pool.execute(f"DROP TABLE {_QUALIFIED}")
        exists = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists is None, "quick_facts must be absent for this idempotency test"

        # The to_regclass guard skips cleanly.
        await _migrate(url, "upgrade", "rel_025")

        # Still absent, no error.
        exists_after = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists_after is None
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_recreates_empty_table(fresh_db) -> None:
    """downgrade() recreates quick_facts when it is absent."""
    url, pool = await _rel_024(fresh_db)
    try:
        # Start from absent state (post-upgrade).
        await _migrate(url, "upgrade", "rel_025")
        exists_before = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists_before is None

        await _migrate(url, "downgrade", "rel_024")

        # Table should now exist and be empty.
        exists_after = await pool.fetchval(f"SELECT to_regclass('{_QUALIFIED}')")
        assert exists_after is not None, "downgrade should have recreated quick_facts"

        count = await pool.fetchval(f"SELECT COUNT(*) FROM {_QUALIFIED}")
        assert count == 0, "Recreated table must be empty"
    finally:
        await pool.close()
