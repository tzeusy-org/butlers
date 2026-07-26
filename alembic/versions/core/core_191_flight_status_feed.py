"""flight_status_feed_status: authoritative flight-delay poll status.

Revision ID: core_191
Revises: core_190
Create Date: 2026-07-26 00:00:00.000000

bu-8bnn9 (follow-up from PR #3589, bu-ep4ks.16 slice 1 — atmosphere feed).
Perception-tier slice 2: flight-status connector polling flight numbers Travel
already extracts, notifying on a schedule delta past a threshold.

Mirrors the ``public.atmosphere_feed_status`` (core_188) singleton-status
design so the honest degraded-mode envelope (CLAUDE.md "Degraded-Mode
Response Envelope") can distinguish "not configured" (no
``AVIATIONSTACK_API_KEY`` provisioned in ``butler_secrets``) from "configured
but the last poll failed" (``last_error`` set, ``consecutive_failures > 0``)
without needing a per-flight table scan. Per-flight poll results are written
onto ``travel.legs.metadata->'flight_status'`` (no new per-flight table) so
they surface through the existing ``trip_summary`` tool for free.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_191"
down_revision = "core_190"
branch_labels = None
depends_on = None

# Mirrors core_188's _ALL_RUNTIME_ROLES: every butler role that may read the
# shared flight-status feed (travel is the sole writer; general/dashboard are
# named follow-up consumers).
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
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
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
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.flight_status_feed_status (
            id                      SMALLINT PRIMARY KEY DEFAULT 1,
            configured              BOOLEAN NOT NULL DEFAULT false,
            last_attempt_at         TIMESTAMPTZ,
            last_success_at         TIMESTAMPTZ,
            last_error              TEXT,
            consecutive_failures    INTEGER NOT NULL DEFAULT 0,
            legs_checked            INTEGER NOT NULL DEFAULT 0,
            delays_detected         INTEGER NOT NULL DEFAULT 0,
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_flight_status_feed_status_singleton CHECK (id = 1)
        )
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.flight_status_feed_status", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.flight_status_feed_status CASCADE")
