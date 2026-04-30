"""Canonical DDL constants and helpers for butler-side fake sessions tables.

Single source of truth for the fake ``{schema}.sessions`` table used by the
Chronicler integration tests.  All three test scenarios that create a fake
butler sessions table import from here instead of duplicating the DDL inline.

Keep this in sync with the real ``core.sessions`` migration in
``src/butlers/migrations/versions/`` whenever columns are added or removed.
A future drift-detector test should compare BUTLER_SESSIONS_COLUMNS against the
production migration to enforce this automatically.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Butler-side sessions table DDL
# ---------------------------------------------------------------------------

#: Column list for the fake butler sessions table, exposed for drift checks.
BUTLER_SESSIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "prompt",
    "trigger_source",
    "model",
    "success",
    "error",
    "result",
    "tool_calls",
    "duration_ms",
    "request_id",
    "ingestion_event_id",
    "started_at",
    "completed_at",
)


def make_sessions_table_ddl(schema_name: str) -> str:
    """Return CREATE TABLE DDL for a fake butler sessions table.

    Args:
        schema_name: PostgreSQL schema name (will be double-quoted in the DDL).

    Returns:
        A ``CREATE TABLE IF NOT EXISTS`` statement for
        ``"{schema_name}".sessions`` with the canonical column set.

    Usage::

        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(make_sessions_table_ddl(schema))
    """
    return f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            model TEXT,
            success BOOLEAN,
            error TEXT,
            result TEXT,
            tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
            duration_ms INTEGER,
            request_id TEXT NOT NULL,
            ingestion_event_id UUID,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """
