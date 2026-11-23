"""retire_transient_weight_facts

Revision ID: health_008
Revises: health_007
Create Date: 2026-11-22 00:00:00.000000

bu-swkpoo.4.  The Home Assistant wellness classifier promoted every ``device_class=weight``
sensor, including scale sensors whose entity id marks them as a transient reading (a
``..._weight_non_stabilized`` sensor reports while the user steps on or off the scale).  Those
readings were stored as ``measurement_weight`` facts (implausible junk values plus a duplicate of every
stabilized weigh-in) and skewed weekly means.  The connector no longer promotes them.

This migration retires the ones already stored: ``measurement_weight`` facts whose
``metadata.source_entity_id`` contains a transient token (``non_stabilized``, ``unstabilized``,
``non_stable``, ``unstable``; case-insensitive, the same tokens as the connector's
``TRANSIENT_ENTITY_TOKENS``) and whose validity is ``active`` or ``fading`` become ``retracted``.
``retracted`` is the memory module's "this was never true" state (what ``memory_forget``
writes), unlike ``expired`` (was true, aged out) and ``superseded`` (replaced by a newer fact).

``health.measurements_public`` is trigger-maintained (``trg_mcp_health_fact``, health_007): an
UPDATE deletes the fact's projected rows and rewrites them only while validity is ``active``, so
the retracted rows leave the projection in the same statement.  The memory catalog is disowned
in the same transaction through ``public.invalidate_memory_projection`` when it exists, as
``cascade_fact_retraction`` does for raw-SQL retractions.

Guarded with ``to_regclass`` (no ``facts`` table is a no-op) and idempotent (retracted rows no
longer match).  The downgrade does not restore the junk readings.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_008"
down_revision = "health_007"
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
            WITH updated AS (
                UPDATE facts
                SET validity = 'retracted'
                WHERE predicate = 'measurement_weight'
                  AND validity IN ('active', 'fading')
                  AND metadata->>'source_entity_id' ILIKE ANY (ARRAY[
                      '%non\_stabilized%', '%unstabilized%', '%non\_stable%', '%unstable%'
                  ])
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
    # The data change is not reversed: the retired readings were junk (as in health_004/006).
    pass
