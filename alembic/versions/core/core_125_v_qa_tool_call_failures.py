"""v_qa_tool_call_failures: surface tool-call errors inside ANY session.

Revision ID: core_125
Revises: core_124
Create Date: 2026-06-14 00:00:00.000000

Sanctioned cross-schema exception per RFC 0010 (sibling of core_055's
``public.v_qa_recent_failures``).

Motivation
----------
``v_qa_recent_failures`` only surfaces sessions whose *outcome* failed
(``success = false``).  When a butler's LLM agent CATCHES a failing MCP tool
call, recovers, and the session still completes ``success = true``, the
underlying tool failure never reaches ``v_qa_recent_failures`` — it is
invisible to the QA staffer's ``session_records`` source.

PR #2285 (bu-03zn3) closed the log-scanner half: tool exceptions now emit a
structured error log line that the ``log_scanner`` source fingerprints.  This
view closes the per-session-record half so QA also has a DB-backed signal that
does not depend on log-file availability/rotation.

What it surfaces
----------------
One row per ``tool_calls`` JSONB element with ``outcome = 'error'`` across ALL
sessions (``success = true`` AND ``success = false``) in every butler schema.
Each row carries enough fields for the ``tool_call_failures`` QA source to
reconstruct the SAME fingerprint the ``log_scanner`` source computes for the
matching ``"MCP tool call failed (...)"`` structured log line, so the two
signals coalesce in the triage layer's source-agnostic fingerprint dedup
(no double-reporting).

The ``tool_calls`` element shape is written by
``butlers.core.tool_call_capture.capture_tool_call`` and persisted by
``session_complete`` (see ``src/butlers/core/sessions.py``):

    {"name": <tool>, "module": <module>, "outcome": "error",
     "error": "<ExcType>: <message>", "input_fingerprint": <sha256>, ...}

View columns
------------
  source_butler   TEXT        — butler schema name (hardcoded per UNION term)
  session_id      UUID        — sessions.id
  session_success BOOLEAN     — sessions.success (true for the catch-and-recover case)
  tool_name       TEXT        — tool_calls element ``name``
  module_name     TEXT        — tool_calls element ``module`` (nullable)
  error           TEXT        — tool_calls element ``error`` ("<ExcType>: <message>")
  trigger_source  TEXT        — sessions.trigger_source
  started_at      TIMESTAMPTZ — sessions.started_at
  completed_at    TIMESTAMPTZ — sessions.completed_at

RFC 0010 guardrails (mirrors core_055):
  1. Read-only — UNION view; PostgreSQL structurally rejects writes.
  2. Explicit source attribution — ``source_butler`` is a hardcoded literal.
  3. Date-filtered by the consumer (lookback window on ``completed_at``).
  4. Health-check validated by the consuming source before processing rows.
  5. Migration-based grants — SELECT on each ``<schema>.sessions`` is already
     granted to ``butler_qa_rw`` by core_055; this migration only adds the
     view + view-level SELECT grants.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_125"
down_revision = "core_124"
branch_labels = None
depends_on = None

# Butler schemas that have a sessions table (kept in sync with core_055).
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

# The QA staffer runtime role that reads the view.
_QA_ROLE = "butler_qa_rw"

# Other butler roles that may read the view for observability.
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

_VIEW_FQN = "public.v_qa_tool_call_failures"


def _union_term(schema: str) -> str:
    """Generate one UNION term that explodes error tool_calls for a schema.

    ``jsonb_array_elements`` is used via a LATERAL join so each error tool-call
    element becomes its own row.  ``WHERE tc->>'outcome' = 'error'`` keeps only
    failing calls; the session ``success`` flag is intentionally NOT filtered so
    the catch-and-recover (``success = true``) case is surfaced.
    """
    return f"""
        SELECT
            '{schema}'::text                  AS source_butler,
            s.id                              AS session_id,
            s.success                         AS session_success,
            tc->>'name'                       AS tool_name,
            tc->>'module'                     AS module_name,
            tc->>'error'                      AS error,
            s.trigger_source                  AS trigger_source,
            s.started_at                      AS started_at,
            s.completed_at                    AS completed_at
        FROM {schema}.sessions s
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE
                WHEN jsonb_typeof(s.tool_calls) = 'array' THEN s.tool_calls
                ELSE '[]'::jsonb
            END
        ) AS tc
        WHERE s.completed_at IS NOT NULL
          AND tc->>'outcome' = 'error'"""


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
    # Cross-schema SELECT on each <schema>.sessions is already granted to
    # butler_qa_rw by core_055; this view reuses those grants.
    union_terms = "\n        UNION ALL".join(_union_term(s) for s in _SESSION_SCHEMAS)

    op.execute(f"DROP VIEW IF EXISTS {_VIEW_FQN} CASCADE")
    op.execute(
        f"""
        CREATE VIEW {_VIEW_FQN} AS
        {union_terms}
    """
    )

    for role in (_QA_ROLE, *_OTHER_BUTLER_ROLES):
        _grant_best_effort(_VIEW_FQN, "SELECT", role)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_VIEW_FQN}")
