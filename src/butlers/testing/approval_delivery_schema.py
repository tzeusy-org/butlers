"""Test-only installer for current approval-delivery DDL over minimal stand-ins."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch


async def install_approval_delivery_schema(pool) -> None:
    """Execute migration-owned DDL and explicitly enable admission for test fixtures."""
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
    for module_name in (
        "butlers.modules.approvals.migrations.015_delivery_intent_recovery",
        "butlers.modules.approvals.migrations.017_delivery_rollout_gate",
    ):
        migration = importlib.import_module(module_name)
        statements: list[str] = []
        mock_op = MagicMock()
        mock_op.execute.side_effect = statements.append
        with patch.object(migration, "op", mock_op):
            migration.upgrade()
        for statement in statements:
            await pool.execute(statement)
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
