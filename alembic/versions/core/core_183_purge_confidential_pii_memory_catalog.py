"""Purge already-written confidential/pii rows from public.memory_catalog.

Revision ID: core_183
Revises: core_182
Create Date: 2026-07-25 00:00:00.000000

bu-6gsmh: owner ruling: EXCLUDE (defense-in-depth). Confidential/pii facts
and rules were, until now, written to ``public.memory_catalog`` (a
cross-butler-readable discovery index) and only filtered out at read time via
the ``max_sensitivity`` authorization ceiling in
``butlers/modules/memory/search.py``. The write-behind path
(``butlers/modules/memory/storage.py::_upsert_catalog`` and its two backfill
counterparts) now refuses to write those rows at all going forward -- see
``CATALOG_WRITE_EXCLUDED_SENSITIVITIES`` in that module.

This migration is the one-off backfill purge: it DELETEs the rows that were
already written under the old (write-everything, filter-on-read) behavior.
``public.memory_catalog`` is a discovery index, not a canonical store (the
canonical fact/rule rows in each butler's own schema are entirely untouched
by this migration) -- deleting a stale catalog row only removes it from
cross-butler discovery, exactly like the existing GC/disownment cascades in
``_mark_catalog_stale`` (this migration runs with elevated migration
privileges, unlike butler runtime roles, which intentionally hold no DELETE
grant on this table per core_009).

The purge has TWO passes, because a real forwarding bug (also fixed by
bu-6gsmh, see ``storage.py``'s ``store_fact``/``store_rule`` write-behind
calls) means a catalog row's own ``sensitivity`` column cannot always be
trusted:

1. Rows whose recorded ``sensitivity`` is already ``'pii'`` or
   ``'confidential'`` -- these came from the backfill job, which always
   propagated the source fact/rule's real sensitivity correctly.
2. Rows whose recorded ``sensitivity`` is NULL but whose canonical source
   fact/rule (in the owning butler's own schema, per the catalog row's
   ``source_schema``/``source_table``/``source_id`` provenance columns) is
   genuinely ``'pii'`` or ``'confidential'``. Before this fix,
   ``store_fact``/``store_rule``'s live write-behind call silently dropped
   ``sensitivity`` entirely, so every catalog row it produced landed with
   ``sensitivity IS NULL`` -- including ones whose source was truly
   confidential/pii. ``COALESCE(sensitivity, 'normal')`` (the same rule the
   read-time ceiling in search.py uses) treats those rows as ``'normal'``,
   so pass 1 alone would miss them. Pass 2 joins each catalog row with a NULL
   ``sensitivity`` back to its source table (schemas are not statically
   enumerable, so this iterates the DISTINCT ``source_schema`` values
   actually present in the catalog via dynamic SQL, guarded per-schema with
   ``to_regclass``) and recovers the true sensitivity from there -- the
   canonical facts/rules tables were never touched by the forwarding bug, so
   they remain the source of truth.

Idempotent (a second run deletes nothing further) and guarded with
``to_regclass`` at every level (the catalog table itself, and each
per-schema ``facts``/``rules`` table) so it safely no-ops wherever a table
does not exist.
"""

from __future__ import annotations

from alembic import op

revision = "core_183"
down_revision = "core_182"
branch_labels = None
depends_on = None


PURGE_CONFIDENTIAL_PII_MEMORY_CATALOG_SQL = """
DO $$
DECLARE
    catalog_schema RECORD;
BEGIN
    IF to_regclass('public.memory_catalog') IS NULL THEN
        RETURN;
    END IF;

    -- Pass 1: rows whose recorded sensitivity is already 'pii'/'confidential'
    -- (always correct -- these came from the backfill job).
    DELETE FROM public.memory_catalog
    WHERE COALESCE(sensitivity, 'normal') IN ('pii', 'confidential');

    -- Pass 2: NULL-sensitivity rows whose canonical source fact is genuinely
    -- pii/confidential (recovers rows mis-cataloged by the pre-bu-6gsmh
    -- write-behind bug, which never forwarded sensitivity at all -- see
    -- storage.py's store_fact write-behind call).
    FOR catalog_schema IN
        SELECT DISTINCT source_schema
        FROM public.memory_catalog
        WHERE source_table = 'facts' AND sensitivity IS NULL
    LOOP
        IF to_regclass(format('%I.facts', catalog_schema.source_schema)) IS NOT NULL THEN
            EXECUTE format(
                'DELETE FROM public.memory_catalog mc '
                'WHERE mc.source_schema = %L '
                '  AND mc.source_table = ''facts'' '
                '  AND mc.sensitivity IS NULL '
                '  AND EXISTS ('
                '      SELECT 1 FROM %I.facts f '
                '      WHERE f.id = mc.source_id '
                '        AND COALESCE(f.sensitivity, ''normal'') IN (''pii'', ''confidential'')'
                '  )',
                catalog_schema.source_schema, catalog_schema.source_schema
            );
        END IF;
    END LOOP;

    -- Pass 2, rules counterpart (store_rule's write-behind had the same
    -- forwarding bug).
    FOR catalog_schema IN
        SELECT DISTINCT source_schema
        FROM public.memory_catalog
        WHERE source_table = 'rules' AND sensitivity IS NULL
    LOOP
        IF to_regclass(format('%I.rules', catalog_schema.source_schema)) IS NOT NULL THEN
            EXECUTE format(
                'DELETE FROM public.memory_catalog mc '
                'WHERE mc.source_schema = %L '
                '  AND mc.source_table = ''rules'' '
                '  AND mc.sensitivity IS NULL '
                '  AND EXISTS ('
                '      SELECT 1 FROM %I.rules ru '
                '      WHERE ru.id = mc.source_id '
                '        AND COALESCE(ru.sensitivity, ''normal'') IN (''pii'', ''confidential'')'
                '  )',
                catalog_schema.source_schema, catalog_schema.source_schema
            );
        END IF;
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    """Delete already-cataloged confidential/pii rows; canonical facts/rules untouched."""
    op.execute(PURGE_CONFIDENTIAL_PII_MEMORY_CATALOG_SQL)


def downgrade() -> None:
    """Do not resurrect purged confidential/pii catalog rows."""
