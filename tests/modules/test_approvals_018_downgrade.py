"""approvals_018's downgrade protects the card-to-call binding it added (real PostgreSQL)."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _upgrade(db_url: str, target: str) -> None:
    command.upgrade(_build_alembic_config(db_url, chains=["approvals"]), target)


def _downgrade(db_url: str, target: str) -> None:
    command.downgrade(_build_alembic_config(db_url, chains=["approvals"]), target)


async def _present_card(db_url: str, *, args_digest: str | None) -> None:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=register_jsonb_codec)
    try:
        now = datetime.now(UTC)
        action_id, intent_id = uuid4(), uuid4()
        schema = await pool.fetchval("SELECT current_schema()")
        key = f"approval:{schema}:{action_id}"
        await pool.execute(
            "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, "
            "expires_at) VALUES ($1, 'email_send_message', $2, 'pending', $3, $4)",
            action_id,
            {"to": "dana@x.com"},
            now,
            now + timedelta(days=3),
        )
        await pool.execute(
            "INSERT INTO approval_delivery_intents (id, action_id, action_key, owning_schema, "
            "origin_butler, admission_mode) VALUES ($1, $2, $3, $4, 'general', 'single')",
            intent_id,
            action_id,
            key,
            schema,
        )
        await pool.execute(
            "INSERT INTO approval_delivery_presentations (intent_id, subject_key, subject_kind, "
            "presentation_mode, presentation_generation, presentation_key, state, not_before, "
            "next_attempt_at, args_digest) VALUES ($1, $2, 'action', 'single', 1, "
            "$2 || ':p:1', 'ready', $3, $3, $4)",
            intent_id,
            key,
            now,
            args_digest,
        )
    finally:
        await pool.close()


async def test_downgrade_refuses_while_a_presented_digest_exists(postgres_container) -> None:
    db_url = await asyncio.to_thread(create_migration_db, postgres_container, migration_db_name())
    await asyncio.to_thread(_upgrade, db_url, "approvals@head")
    await _present_card(db_url, args_digest="a" * 64)

    with pytest.raises(DBAPIError, match="Cannot downgrade approvals_018"):
        await asyncio.to_thread(_downgrade, db_url, "approvals@approvals_017")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert await pool.fetchval(
            "SELECT count(*) FROM approval_delivery_presentations WHERE args_digest IS NOT NULL"
        )
    finally:
        await pool.close()


async def test_downgrade_succeeds_when_no_presentation_carries_a_digest(
    postgres_container,
) -> None:
    db_url = await asyncio.to_thread(create_migration_db, postgres_container, migration_db_name())
    await asyncio.to_thread(_upgrade, db_url, "approvals@head")
    await _present_card(db_url, args_digest=None)

    await asyncio.to_thread(_downgrade, db_url, "approvals@approvals_017")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        assert not await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'approval_delivery_presentations' "
            "AND column_name = 'args_digest')"
        )
    finally:
        await pool.close()
