"""Shared fixtures for module tests."""

from __future__ import annotations

import pytest

from butlers.testing.schema_standins import APPROVAL_EVENTS, APPROVAL_RULES, PENDING_ACTIONS


@pytest.fixture
async def approvals_pool(provisioned_postgres_pool):
    """Provision a fresh database with approvals tables and return a pool.

    Table shapes and indexes come from :mod:`butlers.testing.schema_standins`,
    which the parity guard diffs against
    ``src/butlers/modules/approvals/migrations/``.  The self-contained
    append-only trigger is part of ``APPROVAL_EVENTS.ddl()`` as well, so every
    approvals fixture and the parity guard execute the same schema-qualified
    definition.  Referential-integrity triggers remain deliberately excluded
    because they require sibling tables.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute(PENDING_ACTIONS.ddl())
        await pool.execute(APPROVAL_RULES.ddl())
        await pool.execute(APPROVAL_EVENTS.ddl())
        yield pool
