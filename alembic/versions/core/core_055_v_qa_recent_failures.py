"""v_qa_recent_failures: create public.v_qa_recent_failures read-only SQL view.

Revision ID: core_055
Revises: core_054
Create Date: 2026-04-05 00:00:00.000000

Sanctioned cross-schema exception per RFC 0010.

Creates a read-only UNION view across all butler ``sessions`` tables, filtered
to error/timeout/crash statuses. The QA staffer's ``SessionRecordsSource``
queries this view instead of accessing butler schemas directly. This preserves
the cross-schema isolation guardrail at the application level while providing
a controlled, auditable access path for QA infrastructure.

RFC 0010 five guardrails applied:

  1. Read-only SQL view — UNION view; PostgreSQL structurally prevents INSERT/
     UPDATE/DELETE on UNION views. No application-level bypass possible.

  2. Explicit source attribution — each UNION term hardcodes a ``source_butler``
     string literal (not derived from session data). Provenance is set by the
     view definition.

  3. Date-filtered — filtered to rows where ``completed_at >= now() - interval
     X``. The consumer passes a lookback interval; the view itself is not
     additionally filtered (the consumer is responsible for the date predicate,
     but the view only surfaces session rows — not full state-store data).

  4. Health-check validated — the SessionRecordsSource validates view
     accessibility before processing rows (catches revoked grants early).

  5. Migration-based grants — cross-schema SELECT grants on per-butler
     ``sessions`` tables are created here, in a versioned Alembic migration,
     tracked in VCS and reversible on downgrade.

View columns:
  source_butler   TEXT   — butler schema name (hardcoded per UNION term)
  session_id      UUID   — sessions.id
  error           TEXT   — sessions.error
  healing_fingerprint TEXT — sessions.healing_fingerprint
  started_at      TIMESTAMPTZ — sessions.started_at
  completed_at    TIMESTAMPTZ — sessions.completed_at
  status          TEXT   — derived: 'error' | 'timeout' | 'crash'
                            error  : success=false AND error IS NOT NULL
                            timeout: success=false AND error ILIKE '%timeout%'
                            crash  : success=false AND error IS NULL

The view is created in the ``public`` schema (accessible to all butler roles).
Explicit per-schema SELECT grants are issued to ``butler_qa_rw`` (the QA
staffer's runtime role) on each butler's ``sessions`` table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_055"
down_revision = "core_054"
branch_labels = None
depends_on = None

# Butler schemas that have a sessions table (all role schemas from core_001).
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

# The QA staffer runtime role that needs cross-schema SELECT on sessions.
_QA_ROLE = "butler_qa_rw"

# All other butler roles that may read the view for observability.
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
    """Generate one UNION term for the given butler schema."""
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
            END                                                           AS status
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


def _grant_cross_schema_select(schema: str, role: str) -> None:
    """Grant SELECT on <schema>.sessions to role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.sessions') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT SELECT ON TABLE {schema}.sessions TO "{role}"';
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


def _revoke_cross_schema_select(schema: str, role: str) -> None:
    """Revoke SELECT on <schema>.sessions from role; tolerates errors."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.sessions') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'REVOKE SELECT ON TABLE {schema}.sessions FROM "{role}"';
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


def _grant_schema_usage_best_effort(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA <schema> TO role; tolerates missing role."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _revoke_schema_usage_best_effort(schema: str, role: str) -> None:
    """REVOKE USAGE ON SCHEMA <schema> FROM role; tolerates missing role."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'REVOKE USAGE ON SCHEMA "{schema}" FROM "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ----------------------------------------------------------------------- #
    # 0. GRANT USAGE ON SCHEMA public to butler_qa_rw.
    #    butler_qa_rw is not in _ROLE_SCHEMAS in core_001 and does not receive
    #    this grant automatically.  Without it the role cannot resolve
    #    public.v_qa_recent_failures even after SELECT is granted on the view.
    # ----------------------------------------------------------------------- #
    _grant_schema_usage_best_effort("public", _QA_ROLE)

    # ----------------------------------------------------------------------- #
    # 1. Cross-schema SELECT grants to butler_qa_rw on each butler's sessions.
    #    RFC 0010 guardrail 5: grants are migration-tracked and reversible.
    # ----------------------------------------------------------------------- #
    for schema in _SESSION_SCHEMAS:
        _grant_cross_schema_select(schema, _QA_ROLE)

    # ----------------------------------------------------------------------- #
    # 1.5 Create minimal sessions tables in any schema that doesn't yet have
    #     one.  When the core chain runs before per-butler chains (e.g. in
    #     test environments or on a fresh DB), the butler schemas exist but
    #     their sessions tables have not been created yet.  We create a
    #     compatibility stub with the columns required by the UNION view and
    #     by the indexes created in core_001 (request_id, ingestion_event_id).
    #     IF NOT EXISTS makes this a no-op once the real table is present.
    # ----------------------------------------------------------------------- #
    for schema in _SESSION_SCHEMAS:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema}.sessions (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                prompt              TEXT NOT NULL DEFAULT '',
                trigger_source      TEXT NOT NULL DEFAULT '',
                model               TEXT,
                success             BOOLEAN,
                error               TEXT,
                result              TEXT,
                tool_calls          JSONB NOT NULL DEFAULT '[]'::jsonb,
                duration_ms         INTEGER,
                trace_id            TEXT,
                request_id          TEXT NOT NULL DEFAULT '',
                cost                JSONB,
                input_tokens        INTEGER,
                output_tokens       INTEGER,
                parent_session_id   UUID,
                ingestion_event_id  UUID,
                complexity          TEXT DEFAULT 'medium',
                resolution_source   TEXT DEFAULT 'toml_fallback',
                healing_fingerprint TEXT,
                started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at        TIMESTAMPTZ
            )
        """)

    # ----------------------------------------------------------------------- #
    # 2. Create the UNION view.
    #    RFC 0010 guardrail 1: UNION view — structurally read-only.
    #    RFC 0010 guardrail 2: source_butler is hardcoded per UNION term.
    #    RFC 0010 guardrail 3: consumer applies date filter (lookback window).
    # ----------------------------------------------------------------------- #
    union_terms = "\n        UNION ALL".join(_union_term(s) for s in _SESSION_SCHEMAS)

    op.execute(f"DROP VIEW IF EXISTS {_VIEW_FQN} CASCADE")
    op.execute(f"""
        CREATE VIEW {_VIEW_FQN} AS
        {union_terms}
    """)

    # ----------------------------------------------------------------------- #
    # 3. Grant SELECT on the view to all butler roles.
    #    The QA staffer reads it; other butlers may read it for observability.
    # ----------------------------------------------------------------------- #
    for role in (_QA_ROLE, *_OTHER_BUTLER_ROLES):
        _grant_best_effort(_VIEW_FQN, "SELECT", role)


def downgrade() -> None:
    # Drop the view first (it depends on cross-schema SELECT grants).
    op.execute(f"DROP VIEW IF EXISTS {_VIEW_FQN}")

    # Revoke cross-schema SELECT grants granted during upgrade.
    for schema in _SESSION_SCHEMAS:
        _revoke_cross_schema_select(schema, _QA_ROLE)

    # Revoke public schema USAGE granted during upgrade.
    _revoke_schema_usage_best_effort("public", _QA_ROLE)
