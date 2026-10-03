"""metric_baselines and metric_deviation_episodes: personal baselines.

Revision ID: core_258
Revises: core_257
Create Date: 2026-10-03 00:00:00.000000

bu-q7vx1q.15 S1. Two unqualified per-butler core tables, so the core chain fans
them out to every butler schema through the existing search_path replay (see
``core_220_sessions_friction.py``). Nothing goes in ``public``: a butler's
baselines are derived from its own data and each role sees only its own schema
plus ``public``, which keeps health baselines out of reach of other butlers.

``metric_baselines`` holds one band per ``(metric_key, bucket_value,
method_version)``: the median ``center`` and MAD-derived ``dispersion`` with the
honest denominator it came from (``n_observed`` of ``n_expected``) and the
instrument's ``measurability``. ``status`` says whether a band exists at all;
``center`` and ``dispersion`` are NULL unless it is ``ready``. ``method_version``
lets a v2 scorer run beside v1 in shadow. ``input_digest`` makes an unchanged
rerun an upsert no-op.

``metric_deviation_episodes`` is one row per run of consecutive deviating days.
``UNIQUE (metric_key, opened_on)`` and the partial unique index allowing a
single open episode per metric are the race backstop behind the job's advisory
lock. ``insight_proposed_at`` records that the candidate was handed to the
broker, so a crash between opening the episode and proposing it is retried
instead of lost.

Best-effort DML grants go to the schema's own runtime role only
(``butler_<schema>_rw``), so no other butler role holds a table privilege on
health baselines; the trusted-bootstrap replay (see ``core_196``) then leaves the
tables usable by their owner, as ``core_220`` does for its table.

Downgrade drops both tables; the stored bands and episodes are derived and are
recomputed from source facts by the next ``baseline_watch`` run (episode
history is lost).
"""

from __future__ import annotations

from alembic import op

revision = "core_258"
down_revision = "core_257"
branch_labels = None
depends_on = None

_TABLES = ("metric_baselines", "metric_deviation_episodes")


def _grant_to_schema_owner(table: str) -> None:
    """Grant DML on ``table`` to the runtime role of the schema it was created in.

    Unlike ``core_220``'s fan-out to every butler role, baselines are derived from
    one butler's own data (health baselines are health data), so only
    ``butler_<schema>_rw`` is granted. A schema with no matching role (dev stacks,
    ad hoc test schemas) is left alone, as is a session lacking the privilege to grant.
    """
    op.execute(f"""
        DO $$
        DECLARE
            runtime_role TEXT := 'butler_' || current_schema() || '_rw';
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = runtime_role) THEN
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I TO %I',
                    '{table}', runtime_role
                );
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS metric_baselines (
            metric_key      TEXT NOT NULL,
            bucket_value    TEXT NOT NULL,
            method_version  TEXT NOT NULL,
            status          TEXT NOT NULL,
            measurability   TEXT NOT NULL,
            unit            TEXT NOT NULL,
            center          DOUBLE PRECISION,
            dispersion      DOUBLE PRECISION,
            k_mad           DOUBLE PRECISION NOT NULL,
            n_observed      INTEGER NOT NULL,
            n_expected      INTEGER NOT NULL,
            coverage        DOUBLE PRECISION NOT NULL,
            input_digest    TEXT NOT NULL,
            computed_at     TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (metric_key, bucket_value, method_version),
            CONSTRAINT metric_baselines_status_check
                CHECK (status IN ('ready', 'insufficient_history', 'unmeasurable')),
            CONSTRAINT metric_baselines_measurability_check
                CHECK (measurability IN ('measurable', 'unmeasurable')),
            CONSTRAINT metric_baselines_ready_has_band_check
                CHECK ((status = 'ready') = (center IS NOT NULL AND dispersion IS NOT NULL)),
            CONSTRAINT metric_baselines_denominator_check
                CHECK (n_expected >= 0 AND n_observed >= 0 AND n_observed <= n_expected)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS metric_deviation_episodes (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            metric_key          TEXT NOT NULL,
            opened_on           DATE NOT NULL,
            closed_on           DATE,
            direction           TEXT NOT NULL,
            peak_mad            DOUBLE PRECISION NOT NULL,
            status              TEXT NOT NULL DEFAULT 'open',
            insight_proposed_at TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT metric_deviation_episodes_direction_check
                CHECK (direction IN ('above', 'below')),
            CONSTRAINT metric_deviation_episodes_status_check
                CHECK (status IN ('open', 'closed')),
            CONSTRAINT metric_deviation_episodes_closed_check
                CHECK ((status = 'closed') = (closed_on IS NOT NULL)),
            CONSTRAINT metric_deviation_episodes_metric_opened_key
                UNIQUE (metric_key, opened_on)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS metric_deviation_episodes_one_open
        ON metric_deviation_episodes (metric_key) WHERE status = 'open'
    """)
    for table in _TABLES:
        _grant_to_schema_owner(table)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS metric_deviation_episodes")
    op.execute("DROP TABLE IF EXISTS metric_baselines")
