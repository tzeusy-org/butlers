"""retract_implausible_weight_facts

Revision ID: health_009
Revises: health_008
Create Date: 2026-11-22 00:00:00.000000

bu-swkpoo.11.  A shared scale reports every household member's weigh-in through the same
stabilized Home Assistant sensor, so another person stepping on it landed in the owner's weight
series (two readings roughly 20 kg below the owner's baseline).  The ingest now holds such a
reading as an unconfirmed ``candidate`` fact (``wellness_ingest.WEIGHT_MAX_BASELINE_DEVIATION``).

This migration retracts the ones already asserted: an ``active`` ``measurement_weight`` fact
whose ``metadata.source`` is ``home_assistant`` and whose value deviates by more than 15% from
the median of the *other* active ``measurement_weight`` facts of the same entity within 15 days
either side (a 30-day window around the reading), provided at least 3 such facts exist.  The
15%, 15-day and 3-reading figures mirror the ingest constants and are frozen here on purpose: a
migration must not change meaning when a constant is retuned.

``retracted`` is the memory module's "this was never true" state, as in health_008.  The
projection triggers (``health.measurements_public``, health_007) drop the retracted rows in the
same statement, and the memory catalog is disowned through ``public.invalidate_memory_projection``
when it exists.  Every candidate is chosen against one snapshot of the table, so the outcome does
not depend on row order.

Guarded with ``to_regclass`` (no ``facts`` table is a no-op), touches only Home Assistant sourced
weight facts, and a rerun finds nothing further to retract.  The downgrade does not restore the
readings.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_009"
down_revision = "health_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        DO $$
        DECLARE
            retired uuid[];
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;
            WITH outliers AS (
                SELECT f.id
                FROM facts f
                CROSS JOIN LATERAL (
                    SELECT count(*) AS n,
                           percentile_cont(0.5) WITHIN GROUP (
                               ORDER BY (o.metadata->>'value')::float8
                           ) AS median
                    FROM facts o
                    WHERE o.predicate = 'measurement_weight'
                      AND o.id <> f.id
                      AND o.validity = 'active'
                      AND o.entity_id IS NOT DISTINCT FROM f.entity_id
                      AND jsonb_typeof(o.metadata->'value') = 'number'
                      AND o.valid_at BETWEEN f.valid_at - interval '15 days'
                                         AND f.valid_at + interval '15 days'
                ) b
                WHERE f.predicate = 'measurement_weight'
                  AND f.validity = 'active'
                  AND f.metadata->>'source' = 'home_assistant'
                  AND f.valid_at IS NOT NULL
                  AND jsonb_typeof(f.metadata->'value') = 'number'
                  AND b.n >= 3
                  AND b.median > 0
                  AND abs((f.metadata->>'value')::float8 - b.median) / b.median > 0.15
            ),
            updated AS (
                UPDATE facts
                SET validity = 'retracted'
                WHERE id IN (SELECT id FROM outliers)
                  AND validity = 'active'
                RETURNING id
            )
            SELECT COALESCE(array_agg(id), ARRAY[]::uuid[]) INTO retired FROM updated;
            IF cardinality(retired) > 0 AND to_regprocedure(
                'public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)'
            ) IS NOT NULL THEN
                PERFORM public.invalidate_memory_projection(
                    current_schema(), 'facts', retired, now(), TRUE, FALSE
                );
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    # The data change is not reversed: the retracted readings were another person's weigh-ins.
    pass
