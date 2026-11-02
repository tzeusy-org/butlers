"""Health medication-refill premise probe: narrow definer lookup.

Revision ID: core_272
Revises: core_271
Create Date: 2026-10-18 00:00:00.000000

bu-q7vx1q.30.  A refill reminder asserts "this supply is running out".  The
Switchboard broker re-checks that fact at send through the registered
``health``/``medication_refill_still_due`` probe, which may not read the
``health`` schema directly.  This migration adds the one narrow lookup it uses,
mirroring ``public.resolve_finance_bill_status`` (core_255):

- ``public.resolve_health_medication_refill(subject, doses_per_day, warning_days)``
  (SECURITY DEFINER, ``search_path = pg_catalog, pg_temp``): resolves the
  medication by its stable ``subject`` (an edit or refill supersedes the fact
  row and so changes its id) and returns ONE nullable boolean: true while it is
  active and its estimated supply (counted as the insight scan counts it,
  non-skipped ``took_dose`` facts since ``quantity_updated_at``) is within
  ``warning_days``; false when stopped or refilled past the window; NULL when
  unknown (never recorded, or no supply quantity).  It deliberately returns no
  count or day figure directly; because ``doses_per_day`` and ``warning_days`` are
  caller-supplied, a caller holding EXECUTE can still bisect ``warning_days`` to recover a
  named medication's remaining supply.  EXECUTE is therefore granted to the Switchboard role
  only, and bounding the answer to a single verdict needs server-owned parameters
  (follow-up bu-r0izuo).
- ``REVOKE ... FROM PUBLIC``; EXECUTE is granted to the Switchboard role only.

The function is plpgsql so ``health.facts`` resolves at call time, and the
statements are idempotent because the core chain runs once per butler schema
against these shared ``public`` objects.
"""

from __future__ import annotations

from contextlib import contextmanager

from alembic import op

revision = "core_272"
down_revision = "core_271"
branch_labels = None
depends_on = None

_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_REFILL_SIG = "public.resolve_health_medication_refill(text, numeric, numeric)"
_FUNCTION_DDL_LOCK_SQL = """
SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('butlers:core_272:resolve_health_medication_refill', 0::bigint)
)
"""

CREATE_REFILL_FN = """
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
      AND d.metadata ->> 'medication_id' = v_id::text
      AND d.valid_at >= (v_meta ->> 'quantity_updated_at')::timestamptz
      AND COALESCE((d.metadata ->> 'skipped')::boolean, false) = false;
    RETURN GREATEST(0, ((v_meta ->> 'quantity')::numeric - v_doses) / p_doses_per_day)
           <= p_warning_days;
END;
$fn$;
"""


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
    """Serialize this shared function's complete lifecycle in the owning transaction.

    Earlier core locks cannot survive intervening autocommit revisions. Keep this
    lock until the caller commits or rolls back, including the ACL statements.
    """
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


def upgrade() -> None:
    with _serialized_function_ddl():
        _execute_best_effort(CREATE_REFILL_FN)
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


def downgrade() -> None:
    with _serialized_function_ddl():
        _execute_best_effort(
            "DROP FUNCTION IF EXISTS "
            "public.resolve_health_medication_refill(text, numeric, numeric)"
        )
