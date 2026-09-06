"""Reclassify existing condition/symptom/medication facts as confidential.

Revision ID: core_222
Revises: core_221
Create Date: 2026-09-06 00:00:00.000000

bu-2jtfw.3: the health butler's condition_add/symptom_log/medication_add/
medication_log_dose write paths never passed a ``sensitivity`` argument to
``store_fact``, so every one of these facts landed at ``store_fact``'s own
default of ``'normal'`` -- the least-restrictive tier, NOT excluded from the
fleet-shared ``public.memory_catalog`` write-behind
(``storage.py::_is_catalog_write_excluded`` only ever excluded ``'pii'``/
``'confidential'``). Live symptom: ``GET /api/memory/catalog/search?query=
aortic+valve+regurgitation`` returned a health-scoped fact to any caller
authorized at the default ``normal`` catalog ceiling.

The write paths are fixed going forward (see
``roster/health/tools/conditions.py``/``medications.py``, which now pass
``sensitivity='confidential'`` explicitly). This migration is the one-off
backfill for facts already written under the old behavior:

1. Reclassify canonical ``<schema>.facts`` rows: any row with
   ``scope = 'health'`` and ``predicate IN ('condition', 'symptom',
   'medication', 'took_dose')`` whose ``sensitivity`` is not already
   ``'confidential'`` is updated in place. Iterates every schema that has a
   ``facts`` table (dynamic, via ``information_schema``), guarded with
   ``to_regclass`` -- no schema is hardcoded.
2. Purge already-cataloged rows sourced from those facts: unlike core_183's
   purge (which recovers NULL-sensitivity catalog rows), these catalog rows
   correctly recorded ``sensitivity = 'normal'`` at write time -- the source
   fact itself was simply under-classified. Deleting by recorded sensitivity
   alone would miss them, so this pass joins back to the (now corrected)
   canonical fact by ``(source_schema, source_table, source_id)`` and deletes
   any catalog row whose source fact matches the reclassified predicate set,
   regardless of what sensitivity value the catalog row itself recorded.

Idempotent (a second run updates/deletes nothing further) and guarded with
``to_regclass`` at every level, matching core_183's pattern.
"""

from __future__ import annotations

from alembic import op

revision = "core_222"
down_revision = "core_221"
branch_labels = None
depends_on = None

# Predicates the health butler's diagnosis/treatment write paths use.
# Measurements (measurement_weight, etc.) are deliberately excluded -- they
# remain fleet-discoverable trend data at the 'normal' default. Fixed at
# migration-write time, so it is inlined as a plain SQL array literal below
# rather than threaded through format()'s %L (no user input reaches this
# migration). Quotes are doubled because this literal is embedded inside a
# single-quoted string that is itself the argument to format() -- an inner
# SQL string literal, unrelated to the outer DO $$ ... $$ dollar-quoting.
_HEALTH_CONFIDENTIAL_PREDICATES_SQL_ARRAY = (
    "ARRAY[''condition'', ''symptom'', ''medication'', ''took_dose'']"
)

RECLASSIFY_HEALTH_WRITE_SENSITIVITY_SQL = f"""
DO $$
DECLARE
    facts_schema RECORD;
    catalog_schema RECORD;
BEGIN
    -- Pass 1: reclassify canonical facts rows in every schema that has one.
    FOR facts_schema IN
        SELECT DISTINCT table_schema
        FROM information_schema.tables
        WHERE table_name = 'facts'
    LOOP
        IF to_regclass(format('%I.facts', facts_schema.table_schema)) IS NOT NULL THEN
            EXECUTE format(
                'UPDATE %I.facts '
                'SET sensitivity = ''confidential'' '
                'WHERE scope = ''health'' '
                '  AND predicate = ANY({_HEALTH_CONFIDENTIAL_PREDICATES_SQL_ARRAY}) '
                '  AND COALESCE(sensitivity, ''normal'') != ''confidential''',
                facts_schema.table_schema
            );
        END IF;
    END LOOP;

    IF to_regclass('public.memory_catalog') IS NULL THEN
        RETURN;
    END IF;

    -- Pass 2: purge catalog rows sourced from those (now-reclassified) facts,
    -- regardless of what sensitivity the catalog row itself recorded --
    -- the source fact's predicate is the ground truth here, not the
    -- catalog's already-written (under-classified) sensitivity column.
    FOR catalog_schema IN
        SELECT DISTINCT source_schema
        FROM public.memory_catalog
        WHERE source_table = 'facts'
    LOOP
        IF to_regclass(format('%I.facts', catalog_schema.source_schema)) IS NOT NULL THEN
            EXECUTE format(
                'DELETE FROM public.memory_catalog mc '
                'WHERE mc.source_schema = %L '
                '  AND mc.source_table = ''facts'' '
                '  AND EXISTS ('
                '      SELECT 1 FROM %I.facts f '
                '      WHERE f.id = mc.source_id '
                '        AND f.scope = ''health'' '
                '        AND f.predicate = ANY({_HEALTH_CONFIDENTIAL_PREDICATES_SQL_ARRAY})'
                '  )',
                catalog_schema.source_schema, catalog_schema.source_schema
            );
        END IF;
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    """Reclassify health condition/symptom/medication facts as confidential."""
    op.execute(RECLASSIFY_HEALTH_WRITE_SENSITIVITY_SQL)


def downgrade() -> None:
    """Do not un-reclassify or resurrect purged catalog rows."""
