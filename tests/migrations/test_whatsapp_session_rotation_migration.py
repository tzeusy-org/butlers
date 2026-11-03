"""Regression coverage for WhatsApp session rotation schema repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

_MIGRATIONS = (
    Path(__file__).resolve().parents[2] / "src" / "butlers" / "modules" / "whatsapp" / "migrations"
)


def _migration_sql(module, direction: str = "upgrade") -> list[str]:
    statements: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = statements.append
    with patch.object(module, "op", mock_op):
        getattr(module, direction)()
    return statements


def test_fresh_schema_uses_active_row_uniqueness() -> None:
    migration = load_migration(_MIGRATIONS / "001_whatsapp_sessions.py", "001_whatsapp_sessions")
    sql = "\n".join(_migration_sql(migration))

    assert "phone_number  TEXT        NOT NULL UNIQUE" not in sql
    assert "uq_whatsapp_sessions_active_phone" in sql
    assert "WHERE active = true" in sql


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_schema_allows_history_but_only_one_active_session(
    provisioned_postgres_pool,
) -> None:
    migration = load_migration(
        _MIGRATIONS / "002_active_session_rotation.py", "002_active_session_rotation"
    )

    async with provisioned_postgres_pool() as pool, pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("CREATE SCHEMA messenger")
            await connection.execute("SET LOCAL search_path TO messenger, public")
            await connection.execute("""
                CREATE TABLE whatsapp_sessions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    phone_number TEXT NOT NULL UNIQUE,
                    device_id TEXT,
                    session_data JSONB,
                    paired_at TIMESTAMPTZ,
                    last_seen_at TIMESTAMPTZ,
                    active BOOLEAN NOT NULL DEFAULT true
                )
            """)
            await connection.execute("""
                INSERT INTO whatsapp_sessions (phone_number, device_id, active)
                VALUES ('+15551234567', 'old-device', true)
            """)

            for statement in _migration_sql(migration):
                await connection.execute(statement)

            await connection.execute("""
                UPDATE whatsapp_sessions
                   SET active = false
                 WHERE phone_number = '+15551234567' AND active = true
            """)
            await connection.execute("""
                INSERT INTO whatsapp_sessions (phone_number, device_id, active)
                VALUES ('+15551234567', 'new-device', true)
            """)

            rows = await connection.fetch("""
                SELECT device_id, active
                  FROM whatsapp_sessions
                 WHERE phone_number = '+15551234567'
                 ORDER BY device_id
            """)
            assert [(row["device_id"], row["active"]) for row in rows] == [
                ("new-device", True),
                ("old-device", False),
            ]

            with pytest.raises(asyncpg.UniqueViolationError):
                async with connection.transaction():
                    await connection.execute("""
                        INSERT INTO whatsapp_sessions (phone_number, device_id, active)
                        VALUES ('+15551234567', 'third-device', true)
                    """)
