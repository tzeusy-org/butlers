"""Shared fixtures for module tests."""

from __future__ import annotations

import pytest


@pytest.fixture
async def approvals_pool(provisioned_postgres_pool):
    """Provision a fresh database with approvals tables and return a pool.

    WARNING: Schema defined here must be kept in sync with the actual Alembic
    migrations in src/butlers/modules/approvals/migrations/. If you modify the
    migration SQL, update this fixture accordingly to prevent schema drift.
    """
    async with provisioned_postgres_pool() as pool:
        # Run the approvals migrations to create the tables
        # Migration 001: Create pending_actions and approval_rules tables
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_summary TEXT,
                session_id UUID,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                approval_rule_id UUID,
                CONSTRAINT pending_actions_status_check
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed'))
            )
        """)

        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_rules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                arg_constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
                description TEXT NOT NULL,
                created_from UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                max_uses INTEGER,
                use_count INTEGER NOT NULL DEFAULT 0,
                active BOOLEAN NOT NULL DEFAULT true
            )
        """)

        # Migration 002: Create approval_events table with immutability trigger
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_events (
                event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID REFERENCES pending_actions(id),
                rule_id UUID REFERENCES approval_rules(id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT approval_events_link_check
                    CHECK (action_id IS NOT NULL OR rule_id IS NOT NULL),
                CONSTRAINT approval_events_type_check
                    CHECK (event_type IN (
                        'action_queued',
                        'action_auto_approved',
                        'action_approved',
                        'action_rejected',
                        'action_expired',
                        'action_execution_succeeded',
                        'action_execution_failed',
                        'rule_created',
                        'rule_revoked'
                    ))
            )
        """)

        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_action_id
                ON approval_events (action_id)
        """)
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_rule_id
                ON approval_events (rule_id)
        """)
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_occurred_at
                ON approval_events (occurred_at DESC)
        """)
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_approval_events_event_type
                ON approval_events (event_type)
        """)

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
