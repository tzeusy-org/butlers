"""Regression tests for core_169 audit_log metadata repair (bu-hmdqz.4).

Covers:
  (a) Unit -- module structure (revision/down_revision), source guards.
  (b) Integration (Docker/Postgres) -- the batched repair against a live DB:
      - string-typed metadata double-encoding an object is decoded back to
        that object;
      - a string-typed metadata that is not valid JSON is wrapped losslessly
        under `_raw` instead of raising;
      - object-typed and null-typed metadata are left untouched;
      - the batching loop (small batch size forced via monkeypatched SQL)
        repairs rows across multiple id-range batches;
      - re-running the migration is idempotent (no-op on an already-repaired
        table);
      - the migration no-ops cleanly when public.audit_log is absent;
      - dead audit-derived dismissed_issues rows (old composite key format)
        are deleted, while a reachability ack survives.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_169_audit_log_metadata_repair.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_169", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _apply(pool, fn_name: str) -> None:
    """Capture the migration's op.execute() SQL and run it on the real pool.

    Mirrors tests/migrations/test_healing_breaker_reset_backfill_migration.py:
    core_169's upgrade()/downgrade() only ever call op.execute() with a
    complete SQL string (never op.get_bind()), so mocking `op` to capture
    each call's literal SQL and replaying it against a real asyncpg pool
    exercises the exact same statements the real migration would issue.
    """
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    for sql in sqls:
        await pool.execute(sql)


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

_PROVISION_DISMISSED_ISSUES = """
CREATE TABLE public.dismissed_issues (
    issue_key    TEXT PRIMARY KEY,
    dismissed_by TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NULL
);
"""


async def _insert_row(pool, *, metadata_sql: str, actor: str = "general") -> int:
    """Insert an audit_log row with a raw metadata expression (not a bind
    param) so we can insert a JSONB *string* scalar directly, e.g.
    `to_jsonb('{"a": 1}'::text)`."""
    return await pool.fetchval(
        f"""
        INSERT INTO public.audit_log (actor, action, result, metadata)
        VALUES ($1, 'test_action', 'error', {metadata_sql})
        RETURNING id
        """,
        actor,
    )


@pytest.mark.unit
def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_169"
    assert mod.down_revision == "core_168"
    assert mod.branch_labels is None
    assert mod.depends_on is None


@pytest.mark.unit
def test_downgrade_issues_no_sql() -> None:
    """Non-reversible repair: downgrade must not touch the DB at all."""
    mod = _load_migration()
    fake_op = MagicMock()
    with patch.object(mod, "op", fake_op):
        mod.downgrade()
    fake_op.execute.assert_not_called()


@pytest.mark.unit
def test_source_never_touches_historical_columns() -> None:
    """The repair must only ever SET metadata -- never ts/actor/action/target/
    result/error, which are the actual historical record."""
    src = _MIGRATION_PATH.read_text()
    assert "SET metadata" in src
    for column in ("SET ts", "SET actor", "SET action", "SET target", "SET result", "SET error"):
        assert column not in src


@pytest.mark.unit
def test_source_cleanup_does_not_touch_reachability_acks() -> None:
    mod = _load_migration()
    assert "audit_error_group:%::%" in mod.CLEANUP_DEAD_ACKS_SQL
    assert "scheduled_task_failure:%::%" in mod.CLEANUP_DEAD_ACKS_SQL
    # The DELETE predicate itself must never reference the reachability
    # lane's key shape -- only the docstring (prose, not SQL) may mention it.
    assert "unreachable" not in mod.CLEANUP_DEAD_ACKS_SQL


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_noop_when_audit_log_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _apply(pool, "upgrade")  # must not raise


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_object_typed_metadata_is_untouched(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        row_id = await _insert_row(pool, metadata_sql='\'{"path": "/api/x"}\'::jsonb')

        await _apply(pool, "upgrade")

        meta = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)
        assert meta == {"path": "/api/x"}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_null_metadata_is_untouched(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        row_id = await _insert_row(pool, metadata_sql="NULL")

        await _apply(pool, "upgrade")

        meta = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)
        assert meta is None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_string_typed_metadata_double_encoding_an_object_is_decoded(
    provisioned_postgres_pool,
) -> None:
    """The poisoned shape: metadata holds the JSON *text* of an object."""
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        # to_jsonb() of a plain text value produces a jsonb STRING scalar
        # whose content is that text -- exactly the poisoned shape.
        row_id = await _insert_row(
            pool, metadata_sql="""to_jsonb('{"path": "/api/x", "n": 1}'::text)"""
        )

        typeof_before = await pool.fetchval(
            "SELECT jsonb_typeof(metadata) FROM public.audit_log WHERE id = $1", row_id
        )
        assert typeof_before == "string"

        await _apply(pool, "upgrade")

        meta = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)
        assert meta == {"path": "/api/x", "n": 1}
        typeof_after = await pool.fetchval(
            "SELECT jsonb_typeof(metadata) FROM public.audit_log WHERE id = $1", row_id
        )
        assert typeof_after == "object"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_string_typed_metadata_that_is_not_json_is_wrapped(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        row_id = await _insert_row(pool, metadata_sql="to_jsonb('actor started session'::text)")

        await _apply(pool, "upgrade")

        meta = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)
        assert meta == {"_raw": "actor started session"}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_repair_spans_multiple_batches(provisioned_postgres_pool) -> None:
    """The migration's fixed batch size is 5000; insert enough poisoned rows
    to span several MUCH smaller batches by monkeypatching the batch size in
    the captured SQL, so this test stays fast while still proving the
    WHILE-loop batching logic (not just a single-statement UPDATE) repairs
    every row across the full id range."""
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)

        n_rows = 25
        row_ids: list[int] = []
        for i in range(n_rows):
            row_ids.append(
                await _insert_row(
                    pool, metadata_sql=f"to_jsonb('{{\"i\": {i}}}'::text)", actor=f"butler{i}"
                )
            )

        mod = _load_migration()
        # Shrink the batch size so 25 rows definitely span multiple batches.
        small_batch_sql = mod.REPAIR_METADATA_SQL.replace(
            "v_batch_size CONSTANT BIGINT := 5000;", "v_batch_size CONSTANT BIGINT := 3;"
        )
        assert small_batch_sql != mod.REPAIR_METADATA_SQL

        await pool.execute(mod.CREATE_TRY_PARSE_JSONB_SQL)
        await pool.execute(small_batch_sql)

        remaining = await pool.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE jsonb_typeof(metadata) = 'string'"
        )
        assert remaining == 0

        for i, row_id in enumerate(row_ids):
            meta = await pool.fetchval(
                "SELECT metadata FROM public.audit_log WHERE id = $1", row_id
            )
            assert meta == {"i": i}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_repair_is_idempotent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        row_id = await _insert_row(pool, metadata_sql="""to_jsonb('{"a": 1}'::text)""")

        await _apply(pool, "upgrade")
        first = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)

        # Second run must find nothing left to repair and leave the row alone.
        await _apply(pool, "upgrade")
        second = await pool.fetchval("SELECT metadata FROM public.audit_log WHERE id = $1", row_id)

        assert first == second == {"a": 1}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_dead_audit_derived_acks_deleted_reachability_ack_survives(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        await pool.execute(_PROVISION_DISMISSED_ISSUES)

        await pool.execute(
            "INSERT INTO public.dismissed_issues (issue_key, dismissed_by) VALUES ($1, 'owner')",
            "audit_error_group:runtimeerror-boom::general",
        )
        await pool.execute(
            "INSERT INTO public.dismissed_issues (issue_key, dismissed_by) VALUES ($1, 'owner')",
            "scheduled_task_failure:daily-sync::calendar",
        )
        await pool.execute(
            "INSERT INTO public.dismissed_issues (issue_key, dismissed_by) VALUES ($1, 'owner')",
            "unreachable::general",
        )

        await _apply(pool, "upgrade")

        surviving = {
            r["issue_key"]
            for r in await pool.fetch("SELECT issue_key FROM public.dismissed_issues")
        }
        assert surviving == {"unreachable::general"}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_is_noop_when_dismissed_issues_absent(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await pool.execute(_PROVISION_AUDIT_LOG)
        # No public.dismissed_issues table at all -- must not raise.
        await _apply(pool, "upgrade")
