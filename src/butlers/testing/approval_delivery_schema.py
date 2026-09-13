"""Test-only installer for exact approvals_015 DDL over minimal stand-ins."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch


async def install_approval_delivery_schema(pool) -> None:
    """Execute the migration's own statements without duplicating its DDL."""
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS public.approvals_policy (
            id INTEGER PRIMARY KEY DEFAULT 1,
            quiet_start_hour INTEGER,
            quiet_end_hour INTEGER,
            timezone TEXT NOT NULL DEFAULT 'UTC'
        );
        INSERT INTO public.approvals_policy (id) VALUES (1) ON CONFLICT (id) DO NOTHING
        """
    )
    migration = importlib.import_module(
        "butlers.modules.approvals.migrations.015_delivery_intent_recovery"
    )
    statements: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = statements.append
    with patch.object(migration, "op", mock_op):
        migration.upgrade()
    for statement in statements:
        await pool.execute(statement)
