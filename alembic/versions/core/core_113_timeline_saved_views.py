"""timeline_saved_views: user-defined persistent saved views for the Timeline ledger.

Revision ID: core_113
Revises: core_112
Create Date: 2026-06-02 00:00:00.000000

Creates ``public.timeline_saved_views`` — a table for persisting user-defined
filter presets for the Timeline ledger (ingestion dispatch console).

Schema
------
id          UUID        PK, default gen_random_uuid()
name        TEXT        NOT NULL — user-visible label (≤ 100 chars)
filter_spec JSONB       NOT NULL — arbitrary filter state (statuses, channels, search, range)
created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()

Design decisions
----------------
- **Single-owner scope:** Butlers is a personal, single-owner assistant system.
  There is no multi-tenancy concept, so saved views are global (not per-user-ID).
  A future migration could add ``owner_id`` FK if needed.
- **filter_spec as JSONB:** Keeps the schema flexible. The API validates that
  ``filter_spec`` is a non-null JSON object; the specific keys (``statuses``,
  ``channels``, ``search``, ``range``) are frontend-driven and may evolve.
- In ``public`` schema so all butler runtime roles can read/write (consistent
  with other cross-butler shared tables like ``priority_contacts``).

Indexes
-------
idx_timeline_saved_views_created_at (created_at DESC) — default list order

Grants
------
SELECT, INSERT, UPDATE, DELETE on timeline_saved_views granted to all runtime roles.
"""

from __future__ import annotations

from alembic import op

revision = "core_113"
down_revision = "core_112"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
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
    # --- Table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.timeline_saved_views (
            id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            name        TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
            filter_spec JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Default list order: newest first
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_timeline_saved_views_created_at
        ON public.timeline_saved_views (created_at DESC)
    """)

    # --- Grants ---
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.timeline_saved_views", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_timeline_saved_views_created_at")
    op.execute("DROP TABLE IF EXISTS public.timeline_saved_views")
