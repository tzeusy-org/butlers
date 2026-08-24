"""Shared test DDL for the rel_034 evidence ledger and coverage receipts.

DB-backed relationship tests hand-roll their own schema rather than running
migrations, so every fixture whose test path reaches
``relationship_assert_fact()`` needs the rel_034 objects too — the writer
persists evidence and a coverage receipt in the SAME transaction as the fact,
by design, so a fixture missing those tables no longer exercises the writer at
all, it just errors.

Keeping the DDL here (rather than copy-pasted per fixture) means the test schema
and ``roster/relationship/migrations/034_fact_evidence_and_coverage.py`` drift in
exactly one place if they drift at all.

Not a conftest fixture on purpose: the fixtures that need it are spread across
files with unrelated pool fixtures, and an autouse hook would silently create
tables for tests that never asked for them.
"""

from __future__ import annotations

import asyncpg

#: Mirrors ``fact_evidence.py::_MAX_TEXT_CHARS`` / the migration's CHECK bound.
MAX_TEXT_CHARS = 512


async def apply_evidence_schema(pool: asyncpg.Pool | asyncpg.Connection) -> None:
    """Apply the rel_034 objects to a hand-rolled test schema.

    Idempotent, and safe to call after ``relationship.entity_facts`` already
    exists — the provenance columns are added with ``IF NOT EXISTS``.
    """
    await pool.execute(
        """
        ALTER TABLE relationship.entity_facts
            ADD COLUMN IF NOT EXISTS assert_origin     TEXT,
            ADD COLUMN IF NOT EXISTS assert_session_id UUID,
            ADD COLUMN IF NOT EXISTS assert_action_id  UUID
        """
    )
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS relationship.fact_evidence (
            id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            fact_id      UUID        NOT NULL
                             REFERENCES relationship.entity_facts(id) ON DELETE CASCADE,
            seq          INT         NOT NULL,
            kind         TEXT        NOT NULL CHECK (kind IN ('fact', 'entity', 'url', 'text')),
            ref          TEXT        NOT NULL
                             CHECK (char_length(ref) BETWEEN 1 AND {MAX_TEXT_CHARS}),
            note         TEXT        NOT NULL CHECK (char_length(note) <= {MAX_TEXT_CHARS}),
            src          TEXT        NOT NULL,
            origin       TEXT        NOT NULL CHECK (origin IN ('direct', 'approved')),
            session_id   UUID,
            action_id    UUID,
            carried_from UUID,
            recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await pool.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_evidence_ref
            ON relationship.fact_evidence (fact_id, kind, ref)
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fact_evidence_fact_seq
            ON relationship.fact_evidence (fact_id, seq)
        """
    )
    await pool.execute(
        """
        CREATE OR REPLACE FUNCTION relationship.reject_fact_evidence_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'relationship.fact_evidence is append-only; evidence row % cannot be rewritten',
                OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    await pool.execute(
        "DROP TRIGGER IF EXISTS trg_fact_evidence_immutable ON relationship.fact_evidence"
    )
    await pool.execute(
        """
        CREATE TRIGGER trg_fact_evidence_immutable
            BEFORE UPDATE ON relationship.fact_evidence
            FOR EACH ROW EXECUTE FUNCTION relationship.reject_fact_evidence_update()
        """
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.fact_coverage (
            id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            predicate   TEXT        NOT NULL,
            src         TEXT        NOT NULL,
            outcome     TEXT        NOT NULL
                            CHECK (outcome IN ('present', 'absent', 'unavailable')),
            observed_at TIMESTAMPTZ NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (subject, predicate, src)
        )
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fact_coverage_subject_predicate
            ON relationship.fact_coverage (subject, predicate)
        """
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.fact_approval_context (
            action_id   UUID        NOT NULL PRIMARY KEY,
            src         TEXT        NOT NULL,
            observed_at TIMESTAMPTZ,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
