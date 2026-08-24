"""Evidence ledger and predicate coverage receipts for entity_facts.

Revision ID: rel_034
Revises: rel_033
Create Date: 2026-08-25 00:00:00.000000

Phase: evidence-bearing relationship graph (bu-6jv4m.9).

``relationship.entity_facts`` records *that* a triple is believed, never *why*.
Typed evidence supplied to ``relationship_assert_fact()`` was validated and then
forwarded only to the ``pending_actions`` dossier, so it evaporated the moment a
fact became active.  Reads could also not tell "nobody ever looked" apart from
"we looked and there is nothing there": both render as an empty container.

This migration adds the two stores that close those gaps, plus the per-row
assertion provenance the ledger hangs off.

``relationship.entity_facts`` — assertion provenance (new columns)
------------------------------------------------------------------
assert_origin     TEXT NULL  'direct' | 'approved' — how the row became active
assert_session_id UUID NULL  runtime session that authored the assertion
assert_action_id  UUID NULL  pending_actions row approved to allow the write

These are per-row and set once at insert time; supersession inserts a NEW row
rather than rewriting one, so they are immutable in practice.  They are NULL for
rows written before this migration (unknown provenance is recorded as unknown,
never back-filled with a guess).

``relationship.fact_evidence`` — immutable typed evidence ledger
----------------------------------------------------------------
One row per cited reference supporting one ``entity_facts`` row.  Append-only: a
BEFORE UPDATE trigger rejects every in-place rewrite.  DELETE is deliberately
NOT blocked so ``ON DELETE CASCADE`` from ``entity_facts`` (and transitively from
``public.entities``) still works — a cascade removes the justification along with
the fact it justified, which is not a rewrite of history.

``ref``/``note`` are bounded (512 chars) because this table stores *references
into* sources, never copies of source content.  The bound is a structural
guarantee, not a formatting preference: a 512-char ceiling cannot hold a message
body.

``relationship.fact_coverage`` — predicate coverage receipts
-------------------------------------------------------------
One row per (subject, predicate, src): the most recent outcome a given source
observed when it looked for that predicate on that subject.  ``outcome`` is one
of ``present`` (source found a value), ``absent`` (source looked and there was
nothing), or ``unavailable`` (source could not be consulted).  Reads compose
these receipts into ``present | absent_proven | unknown | unavailable``; the
absence of any receipt composes to ``unknown``, never to ``absent_proven``.

Grants
------
Both tables follow ``entity_facts``: full DML to ``butler_relationship_rw`` only.
``connector_writer`` gets SELECT on ``fact_coverage`` for the same reason rel_033
granted it SELECT on ``entity_facts`` (policy lookups need to know whether a
missing value is proven-absent or merely unknown); it gets NO access to the
evidence ledger, which is owner-facing provenance rather than routing data.
"""

from __future__ import annotations

from alembic import op

revision = "rel_034"
down_revision = "rel_033"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_CONNECTOR_ROLE = "connector_writer"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

#: Maximum characters for an evidence ``ref``/``note``. Mirrors
#: ``roster/relationship/tools/fact_evidence.py::_MAX_TEXT_CHARS`` — the writer
#: rejects over-long values before the database ever sees them; the CHECK is the
#: backstop that keeps a direct-SQL repair honest too.
_MAX_TEXT_CHARS = 512


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_best_effort(statement: str, role: str) -> None:
    """Run a GRANT/REVOKE only when *role* exists; tolerate older databases."""
    role_exists = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {role_exists} THEN
                {statement};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    # --- Assertion provenance on the fact row itself -----------------------
    op.execute(
        """
        ALTER TABLE relationship.entity_facts
            ADD COLUMN IF NOT EXISTS assert_origin     TEXT,
            ADD COLUMN IF NOT EXISTS assert_session_id UUID,
            ADD COLUMN IF NOT EXISTS assert_action_id  UUID
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE relationship.entity_facts
                ADD CONSTRAINT ck_ef_assert_origin
                CHECK (assert_origin IS NULL OR assert_origin IN ('direct', 'approved'));
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )

    # --- Immutable typed evidence ledger -----------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS relationship.fact_evidence (
            id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            fact_id      UUID        NOT NULL
                             REFERENCES relationship.entity_facts(id) ON DELETE CASCADE,
            seq          INT         NOT NULL,
            kind         TEXT        NOT NULL
                             CHECK (kind IN ('fact', 'entity', 'url', 'text')),
            ref          TEXT        NOT NULL
                             CHECK (char_length(ref) BETWEEN 1 AND {_MAX_TEXT_CHARS}),
            note         TEXT        NOT NULL
                             CHECK (char_length(note) <= {_MAX_TEXT_CHARS}),
            src          TEXT        NOT NULL,
            origin       TEXT        NOT NULL CHECK (origin IN ('direct', 'approved')),
            session_id   UUID,
            action_id    UUID,
            carried_from UUID,
            recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # One ledger row per (fact, kind, ref): re-citing the same reference on the
    # same fact is the same evidence, not a second piece of it. This is what
    # makes append-on-every-assert bounded instead of unbounded.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_evidence_ref
            ON relationship.fact_evidence (fact_id, kind, ref)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fact_evidence_fact_seq
            ON relationship.fact_evidence (fact_id, seq)
        """
    )
    op.execute(
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
    op.execute("DROP TRIGGER IF EXISTS trg_fact_evidence_immutable ON relationship.fact_evidence")
    op.execute(
        """
        CREATE TRIGGER trg_fact_evidence_immutable
            BEFORE UPDATE ON relationship.fact_evidence
            FOR EACH ROW EXECUTE FUNCTION relationship.reject_fact_evidence_update()
        """
    )

    # --- Predicate coverage receipts ---------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.fact_coverage (
            id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            subject     UUID        NOT NULL
                            REFERENCES public.entities(id) ON DELETE CASCADE,
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fact_coverage_subject_predicate
            ON relationship.fact_coverage (subject, predicate)
        """
    )

    # --- Approval replay context --------------------------------------------
    # The asserting source and observation time of a PARKED fact write are
    # server-written provenance, not caller input, so they are deliberately kept
    # OUT of ``pending_actions.tool_args``: approval dispatch replays tool_args
    # by splatting it into the MCP tool, and every key there is therefore a
    # parameter an LLM session could also supply. ``src`` in particular gates the
    # owner carve-out (bu-vj46x), so it must never become a tool parameter.
    # Storing it here instead keeps the replay surface minimal and lets
    # ``_resolve_approved_action`` read the source from a row only the server
    # ever wrote.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.fact_approval_context (
            action_id   UUID        NOT NULL PRIMARY KEY,
            src         TEXT        NOT NULL,
            observed_at TIMESTAMPTZ,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Repair in-flight approvals parked by the previous code path. Those rows
    # carry ``src``/``observed_at`` in tool_args and no ``approval_action_id``,
    # a shape the tool signature cannot accept -- approving one fails with an
    # unexpected-keyword TypeError surfaced as "No reachable butler to dispatch
    # action". Move the provenance into the context table, drop the two keys,
    # and stamp the action id so the replay knows it is executing an approval.
    # Guarded on the table existing: the approvals module owns pending_actions
    # and its migrations may not have run yet on a fresh schema.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('relationship.pending_actions') IS NULL THEN
                RETURN;
            END IF;

            INSERT INTO relationship.fact_approval_context (action_id, src, observed_at)
            SELECT pa.id,
                   pa.tool_args->>'src',
                   NULLIF(pa.tool_args->>'observed_at', '')::timestamptz
            FROM relationship.pending_actions pa
            WHERE pa.tool_name = 'relationship_assert_fact'
              AND pa.status IN ('pending', 'approved')
              AND jsonb_typeof(pa.tool_args) = 'object'
              AND pa.tool_args ? 'src'
              AND COALESCE(pa.tool_args->>'src', '') <> ''
            ON CONFLICT (action_id) DO NOTHING;

            UPDATE relationship.pending_actions
            SET tool_args = (tool_args - 'src' - 'observed_at')
                            || jsonb_build_object('approval_action_id', id::text)
            WHERE tool_name = 'relationship_assert_fact'
              AND status IN ('pending', 'approved')
              -- A malformed (non-object) tool_args row is already
              -- undispatchable; skip it rather than fail the whole migration
              -- on "cannot delete from scalar".
              AND jsonb_typeof(tool_args) = 'object';
        END
        $$;
        """
    )

    # --- Grants -------------------------------------------------------------
    for table in (
        "relationship.fact_evidence",
        "relationship.fact_coverage",
        "relationship.fact_approval_context",
    ):
        _grant_best_effort(
            f'GRANT {_TABLE_PRIVILEGES} ON TABLE {table} TO "{_RELATIONSHIP_ROLE}"',
            _RELATIONSHIP_ROLE,
        )
    _grant_best_effort(
        f'GRANT SELECT ON TABLE relationship.fact_coverage TO "{_CONNECTOR_ROLE}"',
        _CONNECTOR_ROLE,
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.fact_approval_context")
    op.execute("DROP TABLE IF EXISTS relationship.fact_coverage")
    op.execute("DROP TRIGGER IF EXISTS trg_fact_evidence_immutable ON relationship.fact_evidence")
    op.execute("DROP TABLE IF EXISTS relationship.fact_evidence")
    op.execute("DROP FUNCTION IF EXISTS relationship.reject_fact_evidence_update()")
    op.execute(
        """
        ALTER TABLE relationship.entity_facts
            DROP CONSTRAINT IF EXISTS ck_ef_assert_origin,
            DROP COLUMN IF EXISTS assert_origin,
            DROP COLUMN IF EXISTS assert_session_id,
            DROP COLUMN IF EXISTS assert_action_id
        """
    )
