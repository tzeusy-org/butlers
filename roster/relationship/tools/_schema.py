"""Schema helpers for relationship tools.

These helpers allow tools to operate across incremental schema migrations
without hard-coding a single table shape.
"""

from __future__ import annotations

import asyncpg


async def table_columns(pool: asyncpg.Pool, table: str) -> set[str]:
    """Return the set of column names for a table, resolved via the connection's search_path."""
    rows = await pool.fetch(
        """
        SELECT a.attname AS column_name
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass($1)
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def has_column(pool: asyncpg.Pool, table: str, column: str) -> bool:
    """Return True if `table.column` exists."""
    return column in await table_columns(pool, table)


async def has_table(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if a table exists in the connection's search_path."""
    return await pool.fetchval(
        "SELECT to_regclass($1) IS NOT NULL",
        table,
    )


def contact_name_expr(columns: set[str], alias: str = "c") -> str:
    """Return SQL expression that resolves a readable contact name."""
    if "name" in columns:
        return f"{alias}.name"
    if {"first_name", "last_name"}.issubset(columns):
        return (
            f"NULLIF(TRIM(COALESCE({alias}.first_name, '') || ' ' || "
            f"COALESCE({alias}.last_name, '')), '')"
        )
    if "first_name" in columns:
        return f"{alias}.first_name"
    if "nickname" in columns:
        return f"{alias}.nickname"
    return f"{alias}.id::text"
