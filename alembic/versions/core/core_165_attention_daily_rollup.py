"""attention_daily_rollup: durable daily owner-engagement signal past the 30-day purge.

Revision ID: core_165
Revises: core_164
Create Date: 2026-07-11 02:30:00.000000

bu-tdd4k.5 (epic bu-tdd4k "proactivity spine", slice 5/5) — per the
2026-07-10 JARVIS pursuit dossier: the 60-minute engagement proxy
(``check_and_update_engagement``, called on every Switchboard ingress) used
to mark ``public.insight_engagement`` rows engaged for ANY ingress, including
connector/automated traffic — so the vision-mandated disengagement ratchet
(``check_total_disengagement_auto_off``) could never fire, since connector
noise permanently impersonated owner engagement. That call is now gated on
owner-authored ingress (see ``src/butlers/modules/pipeline.py``).

Creates ``public.attention_daily_rollup``, one row per UTC day, so the
engagement signal survives ``insight_engagement``'s 30-day raw-event purge:

- ``owner_ingress_count`` is incremented on every ingress request the
  Switchboard resolves to the owner (``src/butlers/core/attention_ledger.py``
  ``record_owner_ingress_rollup``).
- ``insights_delivered``/``insights_engaged`` are upserted by the insight
  broker's cleanup sweep (``cleanup_old_rows``) immediately before it deletes
  the corresponding day's ``insight_engagement`` rows, so
  ``check_total_disengagement_auto_off`` can fall back to the rollup for any
  day already purged from the raw table.
"""

from __future__ import annotations

from alembic import op

revision = "core_165"
down_revision = "core_164"
branch_labels = None
depends_on = None

# Same writer set as core_160 (public.attention_ledger): every butler role
# writes owner-ingress/insight-rollup rows through the switchboard pipeline
# and broker today, but granting broadly avoids a follow-up migration the
# first time another butler or a dashboard reader needs it.
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
        CREATE TABLE IF NOT EXISTS public.attention_daily_rollup (
            day                  DATE PRIMARY KEY,
            owner_ingress_count  INTEGER NOT NULL DEFAULT 0,
            insights_delivered   INTEGER NOT NULL DEFAULT 0,
            insights_engaged     INTEGER NOT NULL DEFAULT 0,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Disengagement-ratchet reads walk backward from today over a small
    # window (14 days) — the primary key already serves that access pattern,
    # but a descending index keeps "most recent rollup rows" cheap as the
    # table grows across months.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_attention_daily_rollup_day_desc
        ON public.attention_daily_rollup (day DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.attention_daily_rollup", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.attention_daily_rollup CASCADE")
