"""Tests for the consolidation_runs audit table + write-on-completion (bu-awo8k.1).

Covers:
  (a) Unit — core_119 migration structure, revision wiring, the additive-only
      (no-drop) guarantee, read-only SELECT grant, and source-text guards.
  (b) Unit — write-on-completion logic in run_consolidation:
        - _record_consolidation_run emits the expected INSERT and is best-effort.
        - run_consolidation writes one audit row per successfully consolidated
          (tenant, butler) group, with the group's counts.
  (c) Integration — public.consolidation_runs exists with the contracted
      columns and SELECT is granted to butler runtime roles after the
      migration SQL runs against a live DB.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest

from butlers.modules.memory import consolidation as consolidation_module

# ---------------------------------------------------------------------------
# Migration file path / loader
# ---------------------------------------------------------------------------

_CORE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "core"
    / "core_119_consolidation_runs.py"
)


def _load_core():
    spec = importlib.util.spec_from_file_location("core_119", _CORE_MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit — migration structure and source-text guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCore119Structure:
    def test_migration_wiring(self):
        """core_119 exists, declares its revision/down_revision chain, and is callable."""
        assert _CORE_MIGRATION_PATH.exists(), f"Migration file not found: {_CORE_MIGRATION_PATH}"
        mod = _load_core()
        assert mod.revision == "core_119"
        assert mod.down_revision == "core_118"  # chains from the current core head
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_creates_table_with_all_contract_columns(self):
        src = _CORE_MIGRATION_PATH.read_text()
        assert "CREATE TABLE IF NOT EXISTS public.consolidation_runs" in src
        for column in (
            "id",
            "butler",
            "consolidated_at",
            "episodes_processed",
            "facts_produced",
            "facts_updated",
            "rules_created",
            "confirmations_made",
            "errors",
        ):
            assert column in src, f"contract column {column!r} missing from migration"

    def test_grant_is_read_only_select(self):
        mod = _load_core()
        # Brief: butler roles get read-only SELECT only — never write privileges.
        assert mod._TABLE_PRIVILEGES == "SELECT"

    def test_grants_select_to_butler_roles(self):
        mod = _load_core()
        # Spot-check a couple of butler runtime roles are in the grant set.
        assert "butler_general_rw" in mod._ALL_RUNTIME_ROLES
        assert "butler_relationship_rw" in mod._ALL_RUNTIME_ROLES

    def test_additive_only_no_drops_in_upgrade(self):
        # ADDITIVE-ONLY: upgrade() must not DROP or ALTER any existing table.
        src = _CORE_MIGRATION_PATH.read_text()
        upgrade_src = src.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        assert "DROP TABLE" not in upgrade_src
        assert "ALTER TABLE" not in upgrade_src
        assert "DROP COLUMN" not in upgrade_src

    def test_grant_uses_best_effort_role_guard(self):
        src = _CORE_MIGRATION_PATH.read_text()
        # Tolerates DBs missing some roles, like sibling public-table migrations.
        assert "pg_roles WHERE rolname" in src

    def test_downgrade_drops_only_the_new_table(self):
        src = _CORE_MIGRATION_PATH.read_text()
        downgrade_src = src.split("def downgrade()", 1)[1]
        assert "DROP TABLE IF EXISTS public.consolidation_runs" in downgrade_src
        # Must not touch any pre-existing memory table.
        assert "episodes" not in downgrade_src
        assert "facts" not in downgrade_src


# ---------------------------------------------------------------------------
# Fakes for the write-on-completion logic
# ---------------------------------------------------------------------------


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAcquire:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """Connection used during the FOR UPDATE SKIP LOCKED claim phase."""

    def __init__(self, claim_rows: list[dict]):
        self._claim_rows = claim_rows
        self.executes: list[tuple] = []

    def transaction(self):
        return _FakeTransaction()

    async def fetch(self, *args, **kwargs):
        return self._claim_rows

    async def execute(self, *args, **kwargs):
        self.executes.append(args)
        return "UPDATE 0"


class _FakePool:
    """Records every execute() call so audit-row INSERTs can be asserted."""

    def __init__(self, claim_rows: list[dict], *, execute_should_raise: bool = False):
        self._conn = _FakeConn(claim_rows)
        self.executes: list[tuple] = []
        self.fetch_results: list[list[dict]] = []
        self._execute_should_raise = execute_should_raise

    def acquire(self):
        return _FakeAcquire(self._conn)

    async def fetch(self, *args, **kwargs):
        # existing-facts / existing-rules dedup context fetches — empty is fine.
        return []

    async def execute(self, query: str, *args, **kwargs):
        self.executes.append((query, args))
        if self._execute_should_raise:
            raise RuntimeError("simulated audit-write failure")
        return "INSERT 0 1"

    def consolidation_run_inserts(self) -> list[tuple]:
        return [
            (query, args)
            for (query, args) in self.executes
            if "INSERT INTO public.consolidation_runs" in query
        ]


class _FakeSpawnerResult:
    def __init__(self):
        self.success = True
        self.output = "{}"
        self.error = None


class _FakeSpawner:
    async def trigger(self, *, prompt: str, trigger_source: str):
        return _FakeSpawnerResult()


# ---------------------------------------------------------------------------
# (b) Unit — _record_consolidation_run
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordConsolidationRun:
    async def test_emits_insert_with_expected_columns_and_params(self):
        pool = _FakePool(claim_rows=[])
        await consolidation_module._record_consolidation_run(
            pool,
            butler="general",
            episodes_processed=4,
            facts_produced=3,
            facts_updated=2,
            rules_created=1,
            confirmations_made=5,
            errors=0,
        )
        inserts = pool.consolidation_run_inserts()
        assert len(inserts) == 1
        query, args = inserts[0]
        for column in (
            "butler",
            "episodes_processed",
            "facts_produced",
            "facts_updated",
            "rules_created",
            "confirmations_made",
            "errors",
        ):
            assert column in query
        # Params are positional in column order.
        assert args == ("general", 4, 3, 2, 1, 5, 0)

    async def test_best_effort_swallows_db_errors(self):
        pool = _FakePool(claim_rows=[], execute_should_raise=True)
        # Must not raise even when the INSERT fails (table not yet migrated, etc.).
        await consolidation_module._record_consolidation_run(
            pool,
            butler="general",
            episodes_processed=1,
            facts_produced=0,
            facts_updated=0,
            rules_created=0,
            confirmations_made=0,
            errors=0,
        )


# ---------------------------------------------------------------------------
# (b) Unit — run_consolidation writes one audit row per successful group
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunConsolidationWritesAuditRow:
    async def test_one_audit_row_per_successful_group_with_counts(self, monkeypatch):
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "general",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        exec_result = {
            "facts_created": 3,
            "facts_updated": 2,
            "rules_created": 1,
            "confirmations_made": 4,
            "episodes_consolidated": 1,
            "episode_ttl_days": None,
            "errors": [],
        }

        async def _fake_execute_consolidation(**kwargs: Any) -> dict[str, Any]:
            return exec_result

        monkeypatch.setattr(
            consolidation_module, "execute_consolidation", _fake_execute_consolidation
        )
        monkeypatch.setattr(
            consolidation_module,
            "parse_consolidation_output",
            lambda output: type("P", (), {"parse_errors": []})(),
        )
        monkeypatch.setattr(
            consolidation_module, "build_consolidation_prompt", lambda **kwargs: "prompt"
        )

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=_FakeSpawner(),
        )

        inserts = pool.consolidation_run_inserts()
        assert len(inserts) == 1, "expected exactly one audit row for one successful group"
        _query, args = inserts[0]
        # (butler, episodes_processed, facts_produced, facts_updated,
        #  rules_created, confirmations_made, errors)
        assert args == ("general", 1, 3, 2, 1, 4, 0)
        # Aggregate stats still returned as before.
        assert stats["facts_created"] == 3
        assert stats["groups_consolidated"] == 1

    async def test_no_audit_row_when_no_spawner(self, monkeypatch):
        # Without a spawner, no group is consolidated → no audit row written.
        claim_rows = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "butler": "general",
                "content": "c",
                "importance": 5.0,
                "metadata": {},
                "created_at": None,
                "tenant_id": "t1",
                "consolidation_attempts": 0,
            }
        ]
        pool = _FakePool(claim_rows=claim_rows)

        stats = await consolidation_module.run_consolidation(
            pool,
            embedding_engine=None,
            cc_spawner=None,
        )
        assert pool.consolidation_run_inserts() == []
        assert stats["groups_consolidated"] == 0


# ---------------------------------------------------------------------------
# (c) Integration — table + columns + grant after migration SQL runs
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_consolidation_runs_table_after_migration(provisioned_postgres_pool) -> None:
    """Table exists with the contracted columns and SELECT is granted to a butler role."""
    mod = _load_core()
    async with provisioned_postgres_pool() as pool:
        # Apply the migration's upgrade SQL directly (mirrors upgrade()).
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS public.consolidation_runs (
                id                 BIGSERIAL PRIMARY KEY,
                butler             TEXT NOT NULL,
                consolidated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                episodes_processed INT NOT NULL DEFAULT 0,
                facts_produced     INT NOT NULL DEFAULT 0,
                facts_updated      INT NOT NULL DEFAULT 0,
                rules_created      INT NOT NULL DEFAULT 0,
                confirmations_made INT NOT NULL DEFAULT 0,
                errors             INT NOT NULL DEFAULT 0
            )
            """
        )
        await pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_consolidation_runs_butler_consolidated_at
            ON public.consolidation_runs (butler, consolidated_at DESC)
            """
        )

        # Table exists.
        assert await pool.fetchval("SELECT to_regclass('public.consolidation_runs')") is not None

        # All contracted columns present with expected types.
        rows = await pool.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'consolidation_runs'
            """
        )
        columns = {r["column_name"]: r["data_type"] for r in rows}
        for expected in (
            "id",
            "butler",
            "consolidated_at",
            "episodes_processed",
            "facts_produced",
            "facts_updated",
            "rules_created",
            "confirmations_made",
            "errors",
        ):
            assert expected in columns, f"missing column {expected!r}"
        assert columns["butler"] == "text"
        assert columns["consolidated_at"] == "timestamp with time zone"
        assert columns["episodes_processed"] == "integer"

        # A row round-trips with defaults.
        await pool.execute(
            "INSERT INTO public.consolidation_runs (butler, facts_produced) VALUES ($1, $2)",
            "general",
            7,
        )
        row = await pool.fetchrow(
            "SELECT butler, facts_produced, episodes_processed, errors "
            "FROM public.consolidation_runs WHERE butler = 'general'"
        )
        assert row["facts_produced"] == 7
        assert row["episodes_processed"] == 0
        assert row["errors"] == 0

        # Grant read-only SELECT to a butler role if it exists, then confirm
        # the privilege landed (and that no write privilege was granted).
        role = "butler_general_rw"
        role_exists = await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        )
        if role_exists:
            existing_roles = {
                r["rolname"]
                for r in await pool.fetch(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY($1)",
                    list(mod._ALL_RUNTIME_ROLES),
                )
            }
            for r in mod._ALL_RUNTIME_ROLES:
                if r in existing_roles:
                    await pool.execute(
                        f'GRANT {mod._TABLE_PRIVILEGES} ON TABLE public.consolidation_runs TO "{r}"'
                    )
            can_select = await pool.fetchval(
                "SELECT has_table_privilege($1, 'public.consolidation_runs', 'SELECT')",
                role,
            )
            can_insert = await pool.fetchval(
                "SELECT has_table_privilege($1, 'public.consolidation_runs', 'INSERT')",
                role,
            )
            assert can_select is True
            assert can_insert is False, "audit table must be read-only for butler roles"
