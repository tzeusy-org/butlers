"""Create calendar.v_prep_contributions cross-schema view.

Revision ID: core_142
Revises: core_141
Create Date: 2026-06-22 00:00:00.000000

Creates a read-only SQL view ``calendar.v_prep_contributions`` that unions
calendar meeting-prep contribution rows from each contributing specialist
butler's ``state`` table.  This is the prep-rail analogue of
``calendar.v_overlay_contributions`` (migration ``core_140``) and a sanctioned
exception to schema isolation (RFC 0006), reusing the RFC 0010 Cross-Butler
Briefing Exception under RFC-0020's accepted criteria.

The view is consumed by the calendar workspace meeting-prep rail read endpoint
(``GET /api/calendar/workspace/prep/{event_id}``), which projects precomputed
per-event prep envelopes into the prep-rail payload without any LLM session or
on-demand cross-schema fan-out (RFC-0020 Decision: no-LLM variant).

The five RFC 0010 guardrails are encoded structurally, exactly as in core_140:

  1. Read-only — UNION view; PostgreSQL structurally forbids INSERT/UPDATE/DELETE.
  2. Hardcoded source — each UNION term sets ``butler`` as a string literal, not
     derived from the JSON payload.
  3. Key-filtered — each term filters ``key LIKE 'calendar/prep/%'``, bounding
     access to prep keys only rather than the whole ``state`` table.
  4. Health-checkable — the read path validates view accessibility and fails open.
  5. Migration-tracked grants — cross-schema SELECT grants to ``butler_calendar_rw``
     are created here, in a versioned migration, and reversed on downgrade.

Columns exposed:
  butler  TEXT   — string literal identifying the source schema
  key     TEXT   — state key (filtered to ``calendar/prep/%``)
  value   JSONB  — prep contribution envelope
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "core_142"
down_revision: str = "core_141"
branch_labels = None
depends_on = None

# Specialist schemas whose state tables contribute to the prep view.
# Relationship contributes attendees / relationship notes / last-met; the
# email/message-context owners (messenger, travel) may contribute the
# message-context panel via the same key convention. The optional-schema guard
# keeps the view valid when any of these schemas is absent.
_SPECIALIST_SCHEMAS: tuple[str, ...] = (
    "messenger",
    "relationship",
    "travel",
)

# Schema that hosts the read-only view and the calendar reader role that may
# query it.  Provisioned best-effort here (mirrors core_140).
_CALENDAR_SCHEMA = "calendar"
_CALENDAR_ROLE = "butler_calendar_rw"

_VIEW_FQN = f"{_CALENDAR_SCHEMA}.v_prep_contributions"

# Key prefix bounding cross-schema access to prep contributions only.
_KEY_PREFIX = "calendar/prep/%"


def _ensure_role_exists(role_name: str) -> None:
    """Create role if it doesn't exist (best-effort, matches core_140 pattern)."""
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                EXECUTE 'CREATE ROLE {role_name} LOGIN';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
    """)


def _state_table_exists(schema_name: str) -> bool:
    bind = op.get_bind()
    relname = f"{schema_name}.state"
    return (
        bind.execute(sa.text("SELECT to_regclass(:relname)"), {"relname": relname}).scalar()
        is not None
    )


def _grant_cross_schema_select(schema: str, role: str) -> None:
    """Grant SELECT on <schema>.state to role; tolerates missing role/table."""
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.state') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT SELECT ON TABLE {schema}.state TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def _revoke_cross_schema_select(schema: str, role: str) -> None:
    """Revoke SELECT on <schema>.state from role; tolerates errors."""
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.state') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'REVOKE SELECT ON TABLE {schema}.state FROM "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def _grant_object_best_effort(object_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table/view TO role; tolerates missing role/object."""
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{object_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {object_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def _grant_schema_usage_best_effort(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA <schema> TO role; tolerates missing role."""
    op.execute(f"""
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
    """)


def _revoke_schema_usage_best_effort(schema: str, role: str) -> None:
    """REVOKE USAGE ON SCHEMA <schema> FROM role; tolerates missing role."""
    op.execute(f"""
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
    """)


def upgrade() -> None:
    # -- Step 0: Ensure the calendar reader role + schema exist ---------------
    _ensure_role_exists(_CALENDAR_ROLE)
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{_CALENDAR_SCHEMA}"')
    _grant_schema_usage_best_effort(_CALENDAR_SCHEMA, _CALENDAR_ROLE)

    available_schemas = tuple(
        schema for schema in _SPECIALIST_SCHEMAS if _state_table_exists(schema)
    )

    # -- Step 1: Grant SELECT on each specialist schema's state table ---------
    #    RFC 0010 guardrail 5: grants are migration-tracked and reversible.
    for schema in available_schemas:
        _grant_cross_schema_select(schema, _CALENDAR_ROLE)

    # -- Step 2: Create the cross-schema view ---------------------------------
    #    RFC 0010 guardrail 1: UNION view — structurally read-only.
    #    RFC 0010 guardrail 2: butler is a hardcoded literal per term.
    #    RFC 0010 guardrail 3: key filter bounds access to prep keys only.
    if available_schemas:
        union_terms = "\n    UNION ALL\n    ".join(
            f"SELECT '{schema}' AS butler, key, value "
            f"FROM {schema}.state "
            f"WHERE key LIKE '{_KEY_PREFIX}'"
            for schema in available_schemas
        )
    else:
        # NULL-returning stub so the view still creates on fresh/core-only DBs.
        union_terms = (
            "SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE"
        )
    op.execute(f"""
        CREATE OR REPLACE VIEW {_VIEW_FQN} AS
        {union_terms}
    """)

    # -- Step 3: Grant SELECT on the view to the calendar reader role ---------
    _grant_object_best_effort(_VIEW_FQN, "SELECT", _CALENDAR_ROLE)


def downgrade() -> None:
    # -- Step 1: Drop the view ------------------------------------------------
    op.execute(f"DROP VIEW IF EXISTS {_VIEW_FQN}")

    # -- Step 2: Revoke cross-schema grants -----------------------------------
    for schema in _SPECIALIST_SCHEMAS:
        _revoke_cross_schema_select(schema, _CALENDAR_ROLE)

    # -- Step 3: Revoke calendar schema USAGE granted during upgrade ----------
    _revoke_schema_usage_best_effort(_CALENDAR_SCHEMA, _CALENDAR_ROLE)
