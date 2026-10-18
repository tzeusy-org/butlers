"""Health medication-refill premise probe: narrow definer lookup.

Revision ID: core_270
Revises: core_268
Create Date: 2026-10-18 00:00:00.000000

bu-q7vx1q.30.  A refill reminder asserts "this supply is running out".  The
Switchboard broker re-checks that fact at send through the registered
``health``/``medication_refill_still_due`` probe, which may not read the
``health`` schema directly.  This migration adds the one narrow lookup it uses,
mirroring ``public.resolve_finance_bill_status`` (core_255):

- ``public.resolve_health_medication_refill(subject, doses_per_day)``
  (SECURITY DEFINER, ``search_path = pg_catalog, pg_temp``): resolves the
  medication by its stable ``subject`` (an edit or refill supersedes the fact
  row and so changes its id), returns whether it is still active and the
  estimated days of supply left, counted exactly as the insight scan counts them
  (non-skipped ``took_dose`` facts since ``quantity_updated_at``).  No row means
  the medication was never recorded; the probe then answers unknown.
- ``REVOKE ... FROM PUBLIC``; EXECUTE is granted to the Switchboard role only.

The function is plpgsql so ``health.facts`` resolves at call time, and the
statements are idempotent because the core chain runs once per butler schema
against these shared ``public`` objects.
"""

from __future__ import annotations

from alembic import op

revision = "core_270"
down_revision = "core_268"
branch_labels = None
depends_on = None

_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_REFILL_SIG = "public.resolve_health_medication_refill(text, numeric)"

CREATE_REFILL_FN = """
CREATE OR REPLACE FUNCTION public.resolve_health_medication_refill(
    p_subject text,
    p_doses_per_day numeric
)
RETURNS TABLE(is_active boolean, days_remaining numeric)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    RETURN QUERY
    WITH med AS (
        SELECT f.id, f.validity, f.metadata
        FROM health.facts f
        WHERE f.predicate = 'medication'
          AND f.scope = 'health'
          AND f.subject = p_subject
        ORDER BY (f.validity = 'active') DESC, f.created_at DESC
        LIMIT 1
    ),
    doses AS (
        SELECT count(*) AS n
        FROM health.facts d, med m
        WHERE d.predicate = 'took_dose'
          AND d.scope = 'health'
          AND d.validity = 'active'
          AND d.metadata ->> 'medication_id' = m.id::text
          AND d.valid_at >= (m.metadata ->> 'quantity_updated_at')::timestamptz
          AND COALESCE((d.metadata ->> 'skipped')::boolean, false) = false
    )
    SELECT (m.validity = 'active'
            AND COALESCE((m.metadata ->> 'active')::boolean, false)),
           CASE
               WHEN m.metadata ->> 'quantity' IS NULL
                 OR m.metadata ->> 'quantity_updated_at' IS NULL
                 OR p_doses_per_day IS NULL
                 OR p_doses_per_day <= 0
               THEN NULL
               ELSE GREATEST(0, ((m.metadata ->> 'quantity')::numeric - doses.n)
                                / p_doses_per_day)
           END
    FROM med m, doses;
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


def upgrade() -> None:
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
    _execute_best_effort(
        "DROP FUNCTION IF EXISTS public.resolve_health_medication_refill(text, numeric)"
    )
