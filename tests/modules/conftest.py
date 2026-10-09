"""Shared fixtures for module tests."""

from __future__ import annotations

import pytest

from butlers.testing.migration import migrated_pool


@pytest.fixture
async def approvals_pool(postgres_container):
    """Complete real approvals relationships; no excluded sibling triggers."""
    async with migrated_pool(postgres_container, chains=["core", "approvals"]) as pool:
        # This fixture explicitly admits presentations; the real migration's
        # rollout default remains off, and all DDL comes from its real chain.
        await pool.execute(
            "UPDATE approval_delivery_rollout SET admission_enabled=true WHERE singleton"
        )
        yield pool
