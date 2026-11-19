"""Insight outcome ledger and class-efficacy table, observe-only (bu-s11n0s.15 S1).

Revision ID: core_353
Revises: core_350 (rechain onto core_352 once #5914 and #5915 land)
Create Date: 2026-11-19 00:00:00.000000

openspec change ``insight-outcome-efficacy``, REQ-proactive-insight-engine-037. Did a premised
nudge change anything? Switchboard enrolls each terminal premise-bound insight candidate with its
exposure (delivered, coalesced, withdrawn, held by budget or context, held by an endpoint custody
hold, shadowed by a sibling, failed or unverified), and a daily sweep records whether the
premise resolved, when, and how precisely it knows. Nothing reads the ledger to change delivery
in this slice.

- ``public.insight_outcomes``: one row per enrolled candidate, keyed by the candidate id with no
  foreign key, so the observation outlives the candidate retention pruner. The outcome is written
  once (``resolved``, ``persisted`` or ``unmeasurable``) and a settled row is never reopened.
- ``public.insight_class_efficacy``: the per-class, per-priority-band daily verdict rollup that
  slice 2 computes. Created here so slice 2 adds no migration; nothing writes it yet.

Only the Switchboard runtime role is granted anything. ``scripts/init-db.sql`` re-grants
public-table DML to every runtime role on a bootstrap replay, so row level security (ENABLE, not
FORCE, the core_241/core_323 posture: the executor that owns the tables, used by migrations and
backups, keeps its bypass) is the durable fence. Every statement runs as the executor.

Installed once across the per-schema core chains with core_347's polled install lock and
yielding transaction: a later schema's run reads the catalog only. Only the last schema still at
core_353 drops the tables; the downgrade tolerates them already being gone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from time import monotonic, sleep

from sqlalchemy.exc import DBAPIError

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_353"
down_revision = "core_350"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_353:insight_outcomes', 0))"
_SWITCHBOARD = "butler_switchboard_rw"
_TABLES = ("insight_outcomes", "insight_class_efficacy")

_DDL = """
CREATE TABLE IF NOT EXISTS public.insight_outcomes (
    candidate_id UUID PRIMARY KEY,
    origin_butler TEXT NOT NULL CHECK (btrim(origin_butler) <> ''),
    category TEXT NOT NULL CHECK (btrim(category) <> ''),
    dedup_family TEXT NOT NULL CHECK (btrim(dedup_family) <> ''),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 100),
    premise JSONB NOT NULL CHECK (jsonb_typeof(premise) = 'object'),
    exposure TEXT NOT NULL CHECK (exposure IN (
        'delivered', 'coalesced', 'withdrawn', 'held_budget', 'held_context', 'held_custody',
        'held_unverified', 'shadowed', 'failed'
    )),
    proposed_at TIMESTAMPTZ NOT NULL,
    exposed_at TIMESTAMPTZ,
    window_end TIMESTAMPTZ NOT NULL,
    outcome TEXT CHECK (outcome IN ('resolved', 'persisted', 'unmeasurable')),
    resolved_at TIMESTAMPTZ,
    resolved_precision TEXT CHECK (resolved_precision IN ('exact', 'observed_by')),
    resolution_reason TEXT CHECK (resolution_reason IS NULL OR btrim(resolution_reason) <> ''),
    unknown_checks INTEGER NOT NULL DEFAULT 0 CHECK (unknown_checks >= 0),
    last_checked_at TIMESTAMPTZ,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    settled_at TIMESTAMPTZ,
    CONSTRAINT insight_outcomes_window CHECK (window_end > proposed_at),
    CONSTRAINT insight_outcomes_exposed
        CHECK ((exposure IN ('delivered', 'coalesced')) = (exposed_at IS NOT NULL)),
    CONSTRAINT insight_outcomes_settled CHECK ((outcome IS NULL) = (settled_at IS NULL)),
    CONSTRAINT insight_outcomes_resolution CHECK (
        (outcome IS NOT DISTINCT FROM 'resolved') = (
            resolved_at IS NOT NULL AND resolved_precision IS NOT NULL
            AND resolution_reason IS NOT NULL
        )
    )
);
CREATE INDEX IF NOT EXISTS insight_outcomes_open_idx
    ON public.insight_outcomes (window_end) WHERE outcome IS NULL;
CREATE INDEX IF NOT EXISTS insight_outcomes_class_idx
    ON public.insight_outcomes (origin_butler, category, proposed_at DESC);

CREATE TABLE IF NOT EXISTS public.insight_class_efficacy (
    origin_butler TEXT NOT NULL CHECK (btrim(origin_butler) <> ''),
    category TEXT NOT NULL CHECK (btrim(category) <> ''),
    priority_band TEXT NOT NULL CHECK (priority_band IN ('low', 'normal', 'urgent')),
    bucket_date DATE NOT NULL,
    n_treated INTEGER NOT NULL CHECK (n_treated >= 0),
    n_holdout INTEGER NOT NULL CHECK (n_holdout >= 0),
    treated_resolved INTEGER NOT NULL CHECK (treated_resolved BETWEEN 0 AND n_treated),
    holdout_resolved INTEGER NOT NULL CHECK (holdout_resolved BETWEEN 0 AND n_holdout),
    treated_median_resolve_s DOUBLE PRECISION,
    holdout_median_resolve_s DOUBLE PRECISION,
    verdict TEXT NOT NULL
        CHECK (verdict IN ('effective', 'self_resolving', 'ignored', 'unmeasured')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    computed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (origin_butler, category, priority_band, bucket_date)
);
"""


def _core_347():
    """core_347's module: its install lock, sibling-yield test and timeouts are reused."""
    path = Path(__file__).with_name("core_347_custody_command_engine.py")
    spec = importlib.util.spec_from_file_location("_core_347_custody_command_engine", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _for_existing_role(role: str, statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{statement}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def _fence() -> None:
    """Switchboard alone reads and writes both tables; RLS survives an init-db replay."""
    tables = ", ".join(f"public.{t}" for t in _TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM PUBLIC")
    for role in (*butler_roles(), "connector_writer"):
        # Switchboard too: init-db default privileges hand it blanket DML (with DELETE)
        # before the exact grant below.
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM {role}")
    _for_existing_role(
        _SWITCHBOARD, f"GRANT SELECT, INSERT, UPDATE ON TABLE {tables} TO {_SWITCHBOARD}"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_switchboard ON public.{table}")
        op.execute(
            f"""
            CREATE POLICY {table}_switchboard ON public.{table}
                FOR ALL TO PUBLIC
                USING (current_user = '{_SWITCHBOARD}')
                WITH CHECK (current_user = '{_SWITCHBOARD}')
            """
        )


def _installed(bind) -> bool:
    """Whether an earlier schema's run committed the tables and their fence (catalog reads)."""
    return bool(
        bind.exec_driver_sql(
            """
            SELECT to_regclass('public.insight_outcomes') IS NOT NULL
               AND to_regclass('public.insight_class_efficacy') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM pg_policy
                    WHERE polrelid = to_regclass('public.insight_class_efficacy')
                      AND polname = 'insight_class_efficacy_switchboard'
               )
            """
        ).scalar()
    )


def _install() -> None:
    op.execute(_LOCK)
    op.execute(_DDL)
    _fence()


def _install_yielding(previous, connection) -> None:
    """Install in one explicit transaction that never waits long while holding anything."""
    deadline = monotonic() + previous._INSTALL_LOCK_WAIT_SECONDS
    while True:
        connection.exec_driver_sql("BEGIN")
        try:
            connection.exec_driver_sql(
                f"SET LOCAL lock_timeout = '{previous._INSTALL_LOCK_TIMEOUT}'"
            )
            if _installed(connection):
                connection.exec_driver_sql("ROLLBACK")
                return
            _install()
        except DBAPIError as error:
            connection.exec_driver_sql("ROLLBACK")
            if not previous._yields_to_a_sibling(error) or monotonic() >= deadline:
                raise
            sleep(0.2)
            continue
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        connection.exec_driver_sql("COMMIT")
        return


def _installation_count() -> int:
    """Count schema-local core chains still at core_353 or later."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 353
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    # Every later schema's run lands here: a catalog read only, no lock, no catalog write.
    if _installed(op.get_bind()):
        return
    previous = _core_347()
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous._acquire_install_lock(connection)
        try:
            _install_yielding(previous, connection)
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({previous._INSTALL_LOCK})")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    # Observations only: no other table references either, so dropping loses no dependent row.
    op.execute("DROP TABLE IF EXISTS public.insight_class_efficacy")
    op.execute("DROP TABLE IF EXISTS public.insight_outcomes")
