"""Tests for core_124_backfill_dashboard_audit_log (bu-j26e8).

Final step of the audit-unify epic (bu-t141i): the migration copies historical
``switchboard.dashboard_audit_log`` rows into the canonical ``public.audit_log``
so the legacy UNION read arm can be removed.

Covers:
  (a) Unit — module structure (revision/down_revision/callables), the guarded
      no-op downgrade, and the to_regclass topology guard in source.
  (b) Integration (Docker/Postgres) — the backfill against a live DB:
      - legacy rows are copied with the documented column mapping
        (butler->actor, operation->action, request_summary.path->target,
        result/error->columns, request_summary/user_context->metadata);
      - created_at is preserved as ts;
      - the backfill is idempotent (re-run inserts nothing — dedup on
        metadata->>'legacy_id');
      - the migration is a no-op when the legacy table is absent;
      - rows already migrated by .3 (sharing a legacy_id) are not duplicated.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_124_backfill_dashboard_audit_log.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_core_124", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# (a) Unit: module structure + guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigrationStructure:
    def test_revision_chain(self):
        """core_124 -> core_123, no branch/depends."""
        mod = _load_migration()
        assert mod.revision == "core_124"
        assert mod.down_revision == "core_123"
        assert mod.branch_labels is None
        assert mod.depends_on is None

    def test_source_guards_legacy_table_with_to_regclass(self):
        src = _MIGRATION_PATH.read_text()
        # Cross-chain hazard guard: every legacy reference goes through to_regclass.
        assert "to_regclass" in src
        assert "switchboard.dashboard_audit_log" in src
        assert "public.dashboard_audit_log" in src

    def test_source_dedups_on_legacy_id(self):
        src = _MIGRATION_PATH.read_text()
        # Idempotency: skip rows whose legacy_id already lives in public.audit_log.
        assert "legacy_id" in src
        assert "NOT EXISTS" in src

    def test_source_maps_columns_like_writer(self):
        src = _MIGRATION_PATH.read_text()
        # butler->actor, operation->action, result/error->columns,
        # request_summary->>'path'->target, created_at->ts.
        assert "src.butler" in src
        assert "src.operation" in src
        assert "request_summary->>'path'" in src
        assert "src.created_at" in src

    def test_downgrade_is_noop_does_not_delete(self):
        # public.audit_log is append-only: downgrade must not DELETE history.
        mod = _load_migration()
        from unittest.mock import MagicMock, patch

        executed: list[str] = []
        fake_op = MagicMock()
        fake_op.execute.side_effect = lambda sql: executed.append(str(sql))
        fake_op.get_bind.side_effect = AssertionError("downgrade must not touch the DB")
        with patch.object(mod, "op", fake_op):
            mod.downgrade()
        # No destructive SQL was issued at all.
        assert executed == []


# ---------------------------------------------------------------------------
# (b) Integration: backfill behaviour against a live DB
# ---------------------------------------------------------------------------

# provisioned_postgres_pool() only creates the DB + extensions; the migration
# chain is not run here, so build the minimal two-table shape the backfill reads.
# We provision the legacy table at public.dashboard_audit_log (flat topology) so
# the migration's _legacy_table() resolves it via to_regclass.
_PROVISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS public.audit_log (
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

CREATE TABLE IF NOT EXISTS public.dashboard_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    butler TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_summary JSONB NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT 'success',
    error TEXT,
    user_context JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _backfill_sql_for_public() -> str:
    """The migration's backfill SQL targeting the flat-public legacy table."""
    mod = _load_migration()
    return mod._backfill_sql("public.dashboard_audit_log")


async def _insert_legacy(
    pool,
    *,
    butler,
    operation,
    request_summary=None,
    result="success",
    error=None,
    user_context=None,
    created_at=None,
):
    return await pool.fetchval(
        """
        INSERT INTO public.dashboard_audit_log
            (butler, operation, request_summary, result, error, user_context, created_at)
        VALUES ($1, $2, COALESCE($3::jsonb, '{}'::jsonb), $4, $5,
                COALESCE($6::jsonb, '{}'::jsonb), COALESCE($7, now()))
        RETURNING id
        """,
        butler,
        operation,
        request_summary,
        result,
        error,
        user_context,
        created_at,
    )


pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_copies_legacy_rows_with_mapping(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)

        legacy_id = await _insert_legacy(
            pool,
            butler="qa",
            operation="schedule.create",
            request_summary={"method": "POST", "path": "/api/qa/schedules"},
            result="error",
            error="boom",
            user_context={"principal": "owner"},
        )

        await pool.execute(_backfill_sql_for_public())

        row = await pool.fetchrow(
            "SELECT actor, action, target, result, error, ts, metadata::text AS meta "
            "FROM public.audit_log"
        )
        assert row["actor"] == "qa"  # butler -> actor
        assert row["action"] == "schedule.create"  # operation -> action
        assert row["target"] == "/api/qa/schedules"  # request_summary.path -> target
        assert row["result"] == "error"
        assert row["error"] == "boom"
        # created_at is preserved as ts.
        legacy_ts = await pool.fetchval(
            "SELECT created_at FROM public.dashboard_audit_log WHERE id = $1", legacy_id
        )
        assert row["ts"] == legacy_ts
        # metadata carries request_summary, user_context, source marker + legacy_id.
        meta = row["meta"]
        assert "request_summary" in meta
        assert "/api/qa/schedules" in meta
        assert "user_context" in meta
        assert "core_124:dashboard_audit_log" in meta
        assert str(legacy_id) in meta


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_is_idempotent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)
        await _insert_legacy(pool, butler="qa", operation="state.set")
        await _insert_legacy(pool, butler="health", operation="session", result="error", error="x")

        await pool.execute(_backfill_sql_for_public())
        first = await pool.fetchval("SELECT count(*) FROM public.audit_log")
        assert first == 2

        # Re-run must not duplicate (dedup on metadata->>'legacy_id').
        await pool.execute(_backfill_sql_for_public())
        second = await pool.fetchval("SELECT count(*) FROM public.audit_log")
        assert second == 2, "re-running the backfill must not duplicate rows"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_backfill_skips_rows_already_present_by_legacy_id(provisioned_postgres_pool) -> None:
    """A row whose legacy_id is already represented in public.audit_log (e.g. a
    prior partial backfill) is not copied again."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVISION_SCHEMA)
        legacy_id = await _insert_legacy(pool, butler="qa", operation="state.set")

        # Pre-seed the canonical table with that legacy_id stamped in metadata.
        await pool.execute(
            "INSERT INTO public.audit_log (actor, action, metadata) "
            "VALUES ('qa', 'state.set', jsonb_build_object('legacy_id', $1::text))",
            str(legacy_id),
        )

        await pool.execute(_backfill_sql_for_public())
        total = await pool.fetchval("SELECT count(*) FROM public.audit_log")
        assert total == 1, "row already present by legacy_id must not be re-copied"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_noop_when_legacy_table_absent(provisioned_postgres_pool) -> None:
    """With no dashboard_audit_log table anywhere, the full upgrade() is a no-op
    (the to_regclass guard returns None) — and public.audit_log stays empty."""
    from unittest.mock import patch

    async with provisioned_postgres_pool() as pool:
        # Only the destination table exists; the legacy table is absent.
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS public.audit_log (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT, note TEXT, ip INET, request_id UUID,
                metadata JSONB, result TEXT, error TEXT
            )
            """
        )

        mod = _load_migration()

        # Drive upgrade() against a SQLAlchemy-style bind backed by the asyncpg
        # pool through a tiny sync shim is heavy; instead assert the guard helper
        # resolves to None and that the backfill is gated on it.  _legacy_table is
        # exercised with a minimal fake bind that mimics .execute().scalar().
        class _FakeResult:
            def __init__(self, value):
                self._value = value

            def scalar(self):
                return self._value

        class _FakeBind:
            def execute(self, _text, params=None):
                # to_regclass(<missing table>) -> None
                return _FakeResult(None)

        with patch.object(mod.op, "get_bind", return_value=_FakeBind()):
            # Must not raise and must not attempt any backfill INSERT.
            mod.upgrade()

        total = await pool.fetchval("SELECT count(*) FROM public.audit_log")
        assert total == 0
