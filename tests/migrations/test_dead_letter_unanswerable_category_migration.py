"""PostgreSQL-backed round-trip for dead_letter_queue's 'unanswerable' category (sw_033)."""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "033_dead_letter_unanswerable_category.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_033", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _apply(pool, operation: str) -> None:
    module = _load_migration()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    for statement in statements:
        await pool.execute(statement)


async def _create_pre_migration_table(pool) -> None:
    await pool.execute(
        """
        CREATE TABLE dead_letter_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            original_request_id UUID NOT NULL,
            source_table TEXT NOT NULL,
            failure_reason TEXT NOT NULL,
            failure_category TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_retry_at TIMESTAMPTZ,
            original_payload JSONB NOT NULL,
            request_context JSONB NOT NULL,
            error_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            replay_eligible BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_failure_category CHECK (
                failure_category IN (
                    'timeout',
                    'retry_exhausted',
                    'circuit_open',
                    'policy_violation',
                    'validation_error',
                    'downstream_failure',
                    'unknown'
                )
            )
        )
        """
    )


def _row(**overrides) -> dict:
    defaults = {
        "id": uuid.uuid4(),
        "original_request_id": uuid.uuid4(),
        "source_table": "message_inbox",
        "failure_reason": "test",
        "failure_category": "unanswerable",
        "original_payload": "{}",
        "request_context": "{}",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_accepts_unanswerable_and_rejects_unknown_category(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await _create_pre_migration_table(pool)
        await _apply(pool, "upgrade")

        row = _row()
        await pool.execute(
            """
            INSERT INTO dead_letter_queue
                (id, original_request_id, source_table, failure_reason,
                 failure_category, original_payload, request_context)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            row["id"],
            row["original_request_id"],
            row["source_table"],
            row["failure_reason"],
            row["failure_category"],
            row["original_payload"],
            row["request_context"],
        )
        stored = await pool.fetchval(
            "SELECT failure_category FROM dead_letter_queue WHERE id = $1", row["id"]
        )
        assert stored == "unanswerable"

        # Every pre-existing category still validates.
        for category in (
            "timeout",
            "retry_exhausted",
            "circuit_open",
            "policy_violation",
            "validation_error",
            "downstream_failure",
            "unknown",
        ):
            other = _row(failure_category=category)
            await pool.execute(
                """
                INSERT INTO dead_letter_queue
                    (id, original_request_id, source_table, failure_reason,
                     failure_category, original_payload, request_context)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                """,
                other["id"],
                other["original_request_id"],
                other["source_table"],
                other["failure_reason"],
                other["failure_category"],
                other["original_payload"],
                other["request_context"],
            )

        with pytest.raises(asyncpg.CheckViolationError):
            bad = _row(failure_category="caller-invented")
            await pool.execute(
                """
                INSERT INTO dead_letter_queue
                    (id, original_request_id, source_table, failure_reason,
                     failure_category, original_payload, request_context)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                """,
                bad["id"],
                bad["original_request_id"],
                bad["source_table"],
                bad["failure_reason"],
                bad["failure_category"],
                bad["original_payload"],
                bad["request_context"],
            )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_restores_the_original_vocabulary(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await _create_pre_migration_table(pool)
        await _apply(pool, "upgrade")
        existing = _row(failure_category="unanswerable")
        await pool.execute(
            """
            INSERT INTO dead_letter_queue
                (id, original_request_id, source_table, failure_reason,
                 failure_category, original_payload, request_context)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
            """,
            existing["id"],
            existing["original_request_id"],
            existing["source_table"],
            existing["failure_reason"],
            existing["failure_category"],
            existing["original_payload"],
            existing["request_context"],
        )
        await _apply(pool, "downgrade")

        assert (
            await pool.fetchval(
                "SELECT failure_category FROM dead_letter_queue WHERE id = $1",
                existing["id"],
            )
            == "unknown"
        )

        with pytest.raises(asyncpg.CheckViolationError):
            row = _row(failure_category="unanswerable")
            await pool.execute(
                """
                INSERT INTO dead_letter_queue
                    (id, original_request_id, source_table, failure_reason,
                     failure_category, original_payload, request_context)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                """,
                row["id"],
                row["original_request_id"],
                row["source_table"],
                row["failure_reason"],
                row["failure_category"],
                row["original_payload"],
                row["request_context"],
            )
