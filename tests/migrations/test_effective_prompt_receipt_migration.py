"""Real-Postgres lifecycle for core_236 through core_238 prompt/purpose receipts."""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_236_effective_prompt_receipt.py"
)
_PURPOSE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_237_session_purpose_lane.py"
)
_EVIDENCE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_238_dispatch_purpose_lane_evidence.py"
)
_ATTEMPT_USAGE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_242_attempt_grained_token_usage.py"
)
_PREFLIGHT_PATH = Path(__file__).resolve().parents[2] / "src/butlers/migration_preflight.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_236", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_receipt_migrations_extend_the_live_core_head() -> None:
    receipt = _load_migration()
    purpose_spec = importlib.util.spec_from_file_location("core_237", _PURPOSE_MIGRATION_PATH)
    assert purpose_spec is not None and purpose_spec.loader is not None
    purpose = importlib.util.module_from_spec(purpose_spec)
    purpose_spec.loader.exec_module(purpose)

    assert (receipt.revision, receipt.down_revision) == ("core_236", "core_235")
    assert (purpose.revision, purpose.down_revision) == ("core_237", "core_236")
    evidence = _load_path("core_238", _EVIDENCE_MIGRATION_PATH)
    assert (evidence.revision, evidence.down_revision) == ("core_238", "core_237")
    attempt_usage = _load_path("core_242", _ATTEMPT_USAGE_MIGRATION_PATH)
    assert (attempt_usage.revision, attempt_usage.down_revision) == ("core_242", "core_241")


@pytest.mark.asyncio(loop_scope="session")
async def test_attempt_usage_migration_is_additive_closed_and_reversible(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            """
            CREATE TABLE public.model_dispatch_attempts (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
            );
            CREATE TABLE public.token_usage_ledger (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        legacy_id = await pool.fetchval(
            "INSERT INTO public.token_usage_ledger (input_tokens, output_tokens) "
            "VALUES (5, 3) RETURNING id"
        )
        attempt_id = await pool.fetchval(
            "INSERT INTO public.model_dispatch_attempts DEFAULT VALUES RETURNING id"
        )

        module = _load_path("core_242", _ATTEMPT_USAGE_MIGRATION_PATH)
        statements: list[str] = []
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with patch.object(module, "op", mocked_op):
            module.upgrade()
        for statement in statements:
            await pool.execute(statement)

        legacy = await pool.fetchrow(
            "SELECT attempt_id, usage_source FROM public.token_usage_ledger WHERE id = $1",
            legacy_id,
        )
        assert tuple(legacy) == (None, "measured")

        unmeasurable_id = await pool.fetchval(
            """
            INSERT INTO public.token_usage_ledger (
                input_tokens, output_tokens, cached_input_tokens,
                cache_creation_tokens, attempt_id, usage_source
            ) VALUES (NULL, NULL, NULL, NULL, $1, 'unmeasurable')
            RETURNING id
            """,
            attempt_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO public.token_usage_ledger (usage_source) VALUES ('guessed')"
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO public.token_usage_ledger "
                "(input_tokens, output_tokens, usage_source) VALUES (1, 2, 'unmeasurable')"
            )

        statements.clear()
        with patch.object(module, "op", mocked_op):
            module.downgrade()
        with pytest.raises(asyncpg.RaiseError, match="unmeasurable usage evidence exists"):
            for statement in statements:
                await pool.execute(statement)

        await pool.execute("DELETE FROM public.token_usage_ledger WHERE id = $1", unmeasurable_id)
        for statement in statements:
            await pool.execute(statement)
        columns = await pool.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'token_usage_ledger'"
        )
        by_name = {row["column_name"]: row["is_nullable"] for row in columns}
        assert "attempt_id" not in by_name
        assert "usage_source" not in by_name
        assert by_name["input_tokens"] == "NO"


async def _run_migration(pool, direction: str) -> None:
    statements: list[str] = []
    module = _load_migration()
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with (
        patch.object(module, "op", mocked_op),
        patch.object(module, "preflight_runtime_attention_downgrade"),
    ):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_receipt_columns_are_additive_atomic_and_rollback_safe(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
        legacy_id = uuid.uuid4()
        await pool.execute("INSERT INTO sessions (id) VALUES ($1)", legacy_id)

        await _run_migration(pool, "upgrade")
        legacy = await pool.fetchrow(
            "SELECT effective_system_prompt, prompt_digest, prompt_provenance "
            "FROM sessions WHERE id = $1",
            legacy_id,
        )
        assert legacy is not None
        assert tuple(legacy) == (None, None, None)

        receipt_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO sessions "
            "(id, effective_system_prompt, prompt_digest, prompt_provenance) "
            "VALUES ($1, 'exact prompt', $2, $3::jsonb)",
            receipt_id,
            "a" * 64,
            [
                {
                    "source": "roster:synthetic/CLAUDE.md",
                    "status": "present",
                    "bytes": 12,
                    "sha": "b" * 64,
                }
            ],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO sessions (id, effective_system_prompt) VALUES ($1, 'partial')",
                uuid.uuid4(),
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO sessions "
                "(id, effective_system_prompt, prompt_digest, prompt_provenance) "
                "VALUES ($1, 'partial', NULL, '[]'::jsonb)",
                uuid.uuid4(),
            )
        assert await pool.fetchval(
            """
            SELECT convalidated
              FROM pg_constraint
             WHERE conrelid = 'sessions'::regclass
               AND conname = 'ck_sessions_effective_prompt_receipt_complete'
            """
        )

        with pytest.raises(asyncpg.RaiseError, match="effective prompt receipts exist"):
            await _run_migration(pool, "downgrade")

        await pool.execute(
            "UPDATE sessions SET effective_system_prompt = NULL, "
            "prompt_digest = NULL, prompt_provenance = NULL WHERE id = $1",
            receipt_id,
        )
        await _run_migration(pool, "downgrade")
        columns = await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'sessions'"
        )
        assert {row["column_name"] for row in columns} == {"id"}
        assert await pool.fetchval("SELECT count(*) FROM sessions") == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_purpose_lane_is_closed_additive_and_rollback_safe(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
        legacy_id = uuid.uuid4()
        await pool.execute("INSERT INTO sessions (id) VALUES ($1)", legacy_id)

        statements: list[str] = []
        spec = importlib.util.spec_from_file_location("core_237", _PURPOSE_MIGRATION_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.upgrade()
        for statement in statements:
            await pool.execute(statement)

        assert (
            await pool.fetchval("SELECT purpose_lane FROM sessions WHERE id = $1", legacy_id)
            is None
        )
        private_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO sessions (id, purpose_lane) VALUES ($1, 'private_content')",
            private_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO sessions (id, purpose_lane) VALUES ($1, 'other')",
                uuid.uuid4(),
            )

        statements.clear()
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.downgrade()
        with pytest.raises(asyncpg.RaiseError, match="session purpose evidence exists"):
            for statement in statements:
                await pool.execute(statement)

        await pool.execute(
            "UPDATE sessions SET purpose_lane = NULL WHERE id = $1",
            private_id,
        )
        for statement in statements:
            await pool.execute(statement)
        assert not await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'sessions'
                   AND column_name = 'purpose_lane'
            )
            """
        )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("migration_name", "migration_path", "constraint"),
    [
        ("core_236", _MIGRATION_PATH, "ck_sessions_effective_prompt_receipt_complete"),
        ("core_237", _PURPOSE_MIGRATION_PATH, "ck_sessions_purpose_lane"),
    ],
)
async def test_session_constraint_validation_does_not_require_access_exclusive_scan_lock(
    provisioned_postgres_pool,
    migration_name: str,
    migration_path: Path,
    constraint: str,
) -> None:
    """Validation remains compatible with an active row writer."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=2) as pool:
        await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
        module = _load_path(migration_name, migration_path)
        statements: list[str] = []
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with patch.object(module, "op", mocked_op):
            module.upgrade()

        await pool.execute(statements[0])
        await pool.execute(statements[1])
        assert await pool.fetchval(
            """
            SELECT NOT convalidated
             FROM pg_constraint
             WHERE conrelid = 'sessions'::regclass
               AND conname = $1
            """,
            constraint,
        )

        async with pool.acquire() as writer:
            transaction = writer.transaction()
            await transaction.start()
            try:
                await writer.execute("LOCK TABLE sessions IN ROW EXCLUSIVE MODE")
                await asyncio.wait_for(pool.execute(statements[2]), timeout=1.0)
            finally:
                await transaction.rollback()

        assert await pool.fetchval(
            """
            SELECT convalidated
             FROM pg_constraint
             WHERE conrelid = 'sessions'::regclass
               AND conname = $1
            """,
            constraint,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_and_usage_evidence_store_only_closed_purpose_lanes(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE TABLE public.model_dispatch_attempts (id BIGSERIAL PRIMARY KEY)")
        await pool.execute("CREATE TABLE public.token_usage_ledger (id BIGSERIAL PRIMARY KEY)")
        module = _load_path("core_238", _EVIDENCE_MIGRATION_PATH)
        statements: list[str] = []
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.upgrade()
        for statement in statements:
            await pool.execute(statement)

        for table in ("model_dispatch_attempts", "token_usage_ledger"):
            await pool.execute(
                f"INSERT INTO public.{table} (purpose_lane) VALUES ('private_content')"
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(f"INSERT INTO public.{table} (purpose_lane) VALUES ('other')")
            assert await pool.fetchval(
                """
                SELECT convalidated
                  FROM pg_constraint
                 WHERE conrelid = $1::regclass
                   AND conname = $2
                """,
                f"public.{table}",
                f"ck_{table}_purpose_lane",
            )

        statements.clear()
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.downgrade()
        with pytest.raises(asyncpg.RaiseError, match="purpose-lane evidence exists"):
            await pool.execute(statements[0])


def test_shared_preflight_delegates_complete_core_198_durable_evidence_predicate() -> None:
    """A trusted bootstrap with durable evidence must remain a refusal."""
    module = _load_path("migration_preflight", _PREFLIGHT_PATH)
    bind = MagicMock()
    operation = MagicMock()
    operation.get_bind.return_value = bind
    environment_context = MagicMock()
    protected = MagicMock()
    protected.protected_rollback_preflight_passes.return_value = False

    with (
        patch.object(module, "_downgrade_crosses_runtime_attention", return_value=True),
        patch.object(module, "_runtime_attention_migration", return_value=protected),
        pytest.raises(RuntimeError, match="protected core_198 rollback preflight failed"),
    ):
        module.preflight_runtime_attention_downgrade(operation, environment_context)

    protected.protected_rollback_preflight_passes.assert_called_once_with(bind)
