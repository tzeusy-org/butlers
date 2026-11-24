"""Health refill probe: count doses by medication subject across edits.

Revision ID: core_311
Revises: core_310
Create Date: 2026-11-03 00:00:00.000000

bu-uc6wh2.  ``public.resolve_health_medication_refill`` (core_272) counted only the
``took_dose`` facts whose ``metadata.medication_id`` is the *current* medication fact
id.  ``medication_update`` supersedes that fact (new id) on every edit, and a dose
keeps the id current when it was logged, so editing dosage, schedule or notes made
the probe forget every dose since the last fill and judge a due reminder not due.

The function now counts doses linked to any medication fact of the same ``subject``
(the stable key the probe already resolves by); ``quantity_updated_at`` still decides
which doses drew the current supply, so a refill resets the count.  This matches
``run_insight_scan`` (roster/health/jobs/health_jobs.py), so the reminder and its
send-time premise agree.  Signature, return type, SECURITY DEFINER, pinned
``search_path`` and grants are unchanged.  No dose rows are read differently or
rewritten.

Downgrade restores the core_272 body.  Like core_272, the statements are idempotent
because the core chain runs once per butler schema against these shared ``public``
objects, and the whole lifecycle is serialized under core_272's advisory lock key.
"""

from __future__ import annotations

from contextlib import contextmanager

from alembic import op

revision = "core_311"
down_revision = "core_310"
branch_labels = None
depends_on = None

_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_REFILL_SIG = "public.resolve_health_medication_refill(text, numeric, numeric)"
# The same key as core_272: both revisions replace one shared function.
_FUNCTION_DDL_LOCK_SQL = """
SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('butlers:core_272:resolve_health_medication_refill', 0::bigint)
)
"""

# Dose-to-medication match: core_272 matched the current fact id only.
_MATCH_CURRENT_ID = "d.metadata ->> 'medication_id' = v_id::text"
_MATCH_SUBJECT = """d.metadata ->> 'medication_id' IN (
          SELECT m.id::text
          FROM health.facts m
          WHERE m.predicate = 'medication'
            AND m.scope = 'health'
            AND m.subject = p_subject
      )"""

_REFILL_FN_TEMPLATE = """
CREATE OR REPLACE FUNCTION public.resolve_health_medication_refill(
    p_subject text,
    p_doses_per_day numeric,
    p_warning_days numeric
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_id uuid;
    v_validity text;
    v_meta jsonb;
    v_doses bigint;
BEGIN
    SELECT f.id, f.validity, f.metadata INTO v_id, v_validity, v_meta
    FROM health.facts f
    WHERE f.predicate = 'medication'
      AND f.scope = 'health'
      AND f.subject = p_subject
    ORDER BY (f.validity = 'active') DESC, f.created_at DESC
    LIMIT 1;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF NOT (v_validity = 'active' AND COALESCE((v_meta ->> 'active')::boolean, false)) THEN
        RETURN false;
    END IF;
    IF v_meta ->> 'quantity' IS NULL
       OR v_meta ->> 'quantity_updated_at' IS NULL
       OR p_doses_per_day IS NULL
       OR p_doses_per_day <= 0
       OR p_warning_days IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT count(*) INTO v_doses
    FROM health.facts d
    WHERE d.predicate = 'took_dose'
      AND d.scope = 'health'
      AND d.validity = 'active'
      AND __DOSE_MATCH__
      AND d.valid_at >= (v_meta ->> 'quantity_updated_at')::timestamptz
      AND COALESCE((d.metadata ->> 'skipped')::boolean, false) = false;
    RETURN GREATEST(0, ((v_meta ->> 'quantity')::numeric - v_doses) / p_doses_per_day)
           <= p_warning_days;
END;
$fn$;
"""

CREATE_REFILL_FN = _REFILL_FN_TEMPLATE.replace("__DOSE_MATCH__", _MATCH_SUBJECT)
CORE_272_REFILL_FN = _REFILL_FN_TEMPLATE.replace("__DOSE_MATCH__", _MATCH_CURRENT_ID)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str) -> None:
    op.execute(
        f"""
        DO $do$
        BEGIN
            EXECUTE {_quote_literal(statement)};
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $do$;
        """
    )


@contextmanager
def _serialized_function_ddl():
    """Serialize the shared function's replacement in the owning transaction (see core_272)."""
    connection = op.get_bind()
    previous_ms = connection.exec_driver_sql(
        "SELECT setting::bigint FROM pg_catalog.pg_settings WHERE name = 'lock_timeout'"
    ).scalar_one()
    bounded_ms = min(previous_ms, 5000) if previous_ms else 5000
    connection.exec_driver_sql(
        "SELECT pg_catalog.set_config('lock_timeout', %s, true)", (f"{bounded_ms}ms",)
    )
    op.execute(_FUNCTION_DDL_LOCK_SQL)
    yield
    # On a SQL error the outer rollback restores SET LOCAL. Issuing another SQL
    # statement in that aborted transaction would mask the original failure.
    connection.exec_driver_sql(
        "SELECT pg_catalog.set_config('lock_timeout', %s, true)", (f"{previous_ms}ms",)
    )


def _install(function_sql: str) -> None:
    with _serialized_function_ddl():
        _execute_best_effort(function_sql)
        _execute_best_effort(f"REVOKE ALL ON FUNCTION {_REFILL_SIG} FROM PUBLIC")
        op.execute(
            f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SWITCHBOARD_ROLE}') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION {_REFILL_SIG} TO {_SWITCHBOARD_ROLE}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_function THEN NULL;
        END
        $do$;
        """
        )


def upgrade() -> None:
    _install(CREATE_REFILL_FN)


def downgrade() -> None:
    _install(CORE_272_REFILL_FN)
