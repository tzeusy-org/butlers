"""v_qa_recent_failures: add trigger_source column for QA provenance.

Revision ID: core_071
Revises: core_067
Create Date: 2026-04-11 00:00:00.000000

Recreates ``public.v_qa_recent_failures`` to expose the ``trigger_source``
column from each butler's sessions table.  The QA staffer's
``SessionRecordsSource`` reads this column to populate
``QaFinding.source_session_trigger_source``, which drives the QA
self-recursion suppression barrier (Gate 0 in the dispatch engine).

No schema changes to persistent tables — this migration only replaces the
view definition.  All existing grants on the view are preserved by the
CREATE OR REPLACE semantics; the explicit re-grants below are defensive
(idempotent per guardrail 5).

View columns after this migration:
  source_butler             TEXT   — hardcoded per UNION term
  session_id                UUID   — sessions.id
  error                     TEXT   — sessions.error
  healing_fingerprint       TEXT   — sessions.healing_fingerprint
  started_at                TIMESTAMPTZ
  completed_at              TIMESTAMPTZ
  status                    TEXT   — 'error' | 'timeout' | 'crash'
  trigger_source            TEXT   — sessions.trigger_source (provenance)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_071"
down_revision = "core_067"
branch_labels = None
depends_on = None

# Butler schemas that have a sessions table (must match core_055).
_SESSION_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)

_QA_ROLE = "butler_qa_rw"

_OTHER_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_VIEW_FQN = "public.v_qa_recent_failures"


def _union_term(schema: str) -> str:
    """Generate one UNION term for the given butler schema including trigger_source."""
    return f"""
        SELECT
            '{schema}'::text                                              AS source_butler,
            s.id                                                          AS session_id,
            s.error,
            s.healing_fingerprint,
            s.started_at,
            s.completed_at,
            CASE
                WHEN s.error ILIKE '%timeout%' THEN 'timeout'
                WHEN s.error IS NOT NULL       THEN 'error'
                ELSE                                'crash'
            END                                                           AS status,
            s.trigger_source
        FROM {schema}.sessions s
        WHERE s.success = false
          AND s.completed_at IS NOT NULL"""


def _grant_best_effort(table_or_view_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table/view TO role; tolerates missing role/object."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_or_view_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_or_view_fqn} TO "{role}"';
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
    # Recreate the view with trigger_source added.
    # CREATE OR REPLACE preserves existing grants on PostgreSQL views.
    union_terms = "\n        UNION ALL".join(_union_term(s) for s in _SESSION_SCHEMAS)

    op.execute(f"""
        CREATE OR REPLACE VIEW {_VIEW_FQN} AS
        {union_terms}
    """)

    # Defensive re-grants (idempotent — preserves RFC 0010 guardrail 5).
    for role in (_QA_ROLE, *_OTHER_BUTLER_ROLES):
        _grant_best_effort(_VIEW_FQN, "SELECT", role)


def downgrade() -> None:
    # Restore the original view definition without trigger_source.
    union_terms_without_trigger = "\n        UNION ALL".join(
        f"""
        SELECT
            '{schema}'::text                                              AS source_butler,
            s.id                                                          AS session_id,
            s.error,
            s.healing_fingerprint,
            s.started_at,
            s.completed_at,
            CASE
                WHEN s.error ILIKE '%timeout%' THEN 'timeout'
                WHEN s.error IS NOT NULL       THEN 'error'
                ELSE                                'crash'
            END                                                           AS status
        FROM {schema}.sessions s
        WHERE s.success = false
          AND s.completed_at IS NOT NULL"""
        for schema in _SESSION_SCHEMAS
    )

    op.execute(f"""
        CREATE OR REPLACE VIEW {_VIEW_FQN} AS
        {union_terms_without_trigger}
    """)

    # Defensive re-grants after downgrade.
    for role in (_QA_ROLE, *_OTHER_BUTLER_ROLES):
        _grant_best_effort(_VIEW_FQN, "SELECT", role)
