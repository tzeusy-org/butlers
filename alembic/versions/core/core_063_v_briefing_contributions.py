"""Create general.v_briefing_contributions cross-schema view.

Revision ID: core_063
Revises: core_062
Create Date: 2026-04-09 00:00:00.000000

Creates a read-only SQL view ``general.v_briefing_contributions`` that unions
briefing contribution rows from each specialist butler's ``state`` table.
This is a sanctioned exception to schema isolation (RFC 0006) — the view
is read-only, uses an explicit ``butler`` discriminator column, and grants
are migration-based for auditability.

The view is consumed by the ``collect_briefing_contributions`` deterministic
job on the General butler, which aggregates specialist contributions into a
combined daily briefing payload.

Columns exposed:
  butler  TEXT   — string literal identifying the source schema
  key     TEXT   — state key (filtered to ``briefing/daily/%``)
  value   JSONB  — contribution payload
"""

from __future__ import annotations

from alembic import op

revision: str = "core_063"
down_revision: str = "core_062"
branch_labels = None
depends_on = None

# Specialist schemas whose state tables contribute to the briefing view.
# Must match SPECIALIST_BUTLERS in src/butlers/jobs/briefing.py.
_SPECIALIST_SCHEMAS: tuple[str, ...] = (
    "education",
    "finance",
    "health",
    "home",
    "lifestyle",
    "relationship",
    "travel",
)

_GENERAL_ROLE = "butler_general_rw"


def upgrade() -> None:
    # -- Step 1: Grant SELECT on each specialist schema's state table ---------
    for schema in _SPECIALIST_SCHEMAS:
        op.execute(
            f"GRANT SELECT ON {schema}.state TO {_GENERAL_ROLE}"  # noqa: S608
        )

    # -- Step 2: Create the cross-schema view ---------------------------------
    union_terms = "\n    UNION ALL\n    ".join(
        f"SELECT '{schema}' AS butler, key, value "
        f"FROM {schema}.state "
        f"WHERE key LIKE 'briefing/daily/%'"
        for schema in _SPECIALIST_SCHEMAS
    )
    op.execute(f"""
        CREATE OR REPLACE VIEW general.v_briefing_contributions AS
        {union_terms}
    """)


def downgrade() -> None:
    # -- Step 1: Drop the view ------------------------------------------------
    op.execute("DROP VIEW IF EXISTS general.v_briefing_contributions")

    # -- Step 2: Revoke cross-schema grants -----------------------------------
    for schema in _SPECIALIST_SCHEMAS:
        op.execute(
            f"REVOKE SELECT ON {schema}.state FROM {_GENERAL_ROLE}"  # noqa: S608
        )
