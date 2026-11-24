"""Shared fixtures for module tests."""

from __future__ import annotations

import pytest


@pytest.fixture
async def approvals_pool(db):
    """The real core + approvals chains on a fresh clone, with delivery admission on.

    ``approvals_017`` ships admission disabled; these tests exercise the enabled
    path, as the former stand-in fixture did.
    """
    pool = await db("core", "approvals")
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
    return pool
