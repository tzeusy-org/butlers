"""Add education to calendar.v_overlay_contributions.

Revision ID: core_385
Revises: core_384
Create Date: 2026-11-26 00:00:00.000000

The Education butler contributes one aggregated "N reviews due" overlay claim per day
(bu-5ii4gj.13 S5b), so its ``state`` table joins the read-only overlay view that core_140 created
for finance, health, relationship and travel. Same five RFC 0010 guardrails as core_140:
read-only UNION view, hardcoded ``butler`` literal per term, ``calendar/overlay/%`` key filter,
fail-open health check on the read path, and a migration-tracked, reversible SELECT grant on
``education.state`` to ``butler_calendar_rw``.

``upgrade`` rebuilds the view from the schemas that exist; ``downgrade`` restores core_140's four.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "core_385"
down_revision: str = "core_384"
branch_labels = None
depends_on = None

# core_140's contributors, then the whole set after this migration. Must match
# ``OVERLAY_CONTRIBUTORS`` in butlers.api.read_models.calendar_workspace.
_PREVIOUS_SCHEMAS: tuple[str, ...] = ("finance", "health", "relationship", "travel")
_SPECIALIST_SCHEMAS: tuple[str, ...] = ("education", *_PREVIOUS_SCHEMAS)
_NEW_SCHEMA = "education"

_CALENDAR_ROLE = "butler_calendar_rw"
_VIEW_FQN = "calendar.v_overlay_contributions"
_KEY_PREFIX = "calendar/overlay/%"


def _state_table_exists(schema_name: str) -> bool:
    bind = op.get_bind()
    relname = f"{schema_name}.state"
    return (
        bind.execute(sa.text("SELECT to_regclass(:relname)"), {"relname": relname}).scalar()
        is not None
    )


def _grant_best_effort(verb: str, schema: str) -> None:
    """GRANT/REVOKE SELECT on <schema>.state; tolerates a missing role, table or privilege."""
    preposition = "TO" if verb == "GRANT" else "FROM"
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.state') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_CALENDAR_ROLE}')
            THEN
                EXECUTE '{verb} SELECT ON TABLE {schema}.state {preposition} "{_CALENDAR_ROLE}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def _rebuild_view(schemas: tuple[str, ...]) -> None:
    available = tuple(s for s in schemas if _state_table_exists(s))
    if available:
        terms = "\n    UNION ALL\n    ".join(
            f"SELECT '{s}' AS butler, key, value FROM {s}.state WHERE key LIKE '{_KEY_PREFIX}'"
            for s in available
        )
    else:
        terms = "SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE"
    op.execute(f"CREATE OR REPLACE VIEW {_VIEW_FQN} AS\n    {terms}")


def upgrade() -> None:
    _grant_best_effort("GRANT", _NEW_SCHEMA)
    _rebuild_view(_SPECIALIST_SCHEMAS)


def downgrade() -> None:
    _rebuild_view(_PREVIOUS_SCHEMAS)
    _grant_best_effort("REVOKE", _NEW_SCHEMA)
