"""Shared fixtures for module tests."""

from __future__ import annotations

import pytest

from butlers.testing.schema_standins import APPROVAL_EVENTS, APPROVAL_RULES, PENDING_ACTIONS


@pytest.fixture
async def approvals_pool(provisioned_postgres_pool):
    """Provision a fresh database with approvals tables and return a pool.

    Table shapes and indexes come from :mod:`butlers.testing.schema_standins`,
    which the parity guard diffs against
    ``src/butlers/modules/approvals/migrations/``.  Only the append-only
    trigger stays local: a stand-in deliberately does not mirror triggers, and
    this fixture's immutability tests need it.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute(PENDING_ACTIONS.ddl())
        await pool.execute(APPROVAL_RULES.ddl())
        await pool.execute(APPROVAL_EVENTS.ddl())

        await pool.execute("""
            CREATE OR REPLACE FUNCTION prevent_approval_events_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'approval_events is append-only: % is not allowed', TG_OP;
            END;
            $$;
        """)

        await pool.execute("""
            DROP TRIGGER IF EXISTS trg_approval_events_immutable ON approval_events
        """)
        await pool.execute("""
            CREATE TRIGGER trg_approval_events_immutable
            BEFORE UPDATE OR DELETE ON approval_events
            FOR EACH ROW
            EXECUTE FUNCTION prevent_approval_events_mutation()
        """)

        yield pool
