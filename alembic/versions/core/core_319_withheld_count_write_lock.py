"""Serialize withheld-count writes per source, so their row locks cannot deadlock (bu-y66o00.17).

Revision ID: core_319
Revises: core_318
Create Date: 2026-11-07 00:00:00.000000

The ``AFTER`` trigger from core_318 locks one ``public.entity_graph_withheld_counts`` row per
subject, in whatever order a transaction's edge writes arrive. Mirrored ``store_fact`` writes and
batch retractions touching the same two entities in opposite order therefore deadlocked. Every
edge writer goes through this trigger, so the lock belongs here: once a write changes a count, the
transaction holds one advisory lock per ``source_key`` until it ends, and row order within a source
stops mattering. An edge write that changes no count takes no lock.

A transaction changing the counts of two sources takes those locks in arrival order, so two such
transactions can still deadlock against each other. ``store_fact``, batch retraction and entity
merge each stay within one source; a hard delete of an entity, cascading over its edges, does not.

``store_fact`` embeds the inverse fact after its forward stub, so a mirrored sensitive write holds
this lock across that embedding.

The core chain runs once per butler schema over this one shared function. Replacing it is
idempotent, and only the last schema still at core_319 restores the core_318 body (core_308
pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_319"
down_revision = "core_318"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_319:withheld-count-lock', 0))"
_COUNTS = "public.entity_graph_withheld_counts"
_SOURCE_LOCK = """
            PERFORM pg_advisory_xact_lock(
                hashtextextended('butlers:entity-graph-withheld:' || source_key, 0)
            )
            FROM (
                SELECT DISTINCT key AS source_key
                FROM unnest(ARRAY[old_key, new_key]) AS keys(key)
                WHERE key IS NOT NULL
                ORDER BY 1
                OFFSET 0
            ) AS ordered;"""


def _replace_function(source_lock: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.entity_graph_edges_sync_withheld_counts()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            old_entity uuid;
            old_key text;
            new_entity uuid;
            new_key text;
        BEGIN
            IF TG_OP <> 'INSERT' AND OLD.withheld_reason IS NOT NULL THEN
                old_entity := OLD.subject_entity_id;
                old_key := OLD.source_schema || '.' || OLD.source_table;
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.withheld_reason IS NOT NULL THEN
                new_entity := NEW.subject_entity_id;
                new_key := NEW.source_schema || '.' || NEW.source_table;
            END IF;
            IF old_entity IS NOT DISTINCT FROM new_entity
               AND old_key IS NOT DISTINCT FROM new_key THEN
                RETURN NULL;
            END IF;{source_lock}
            IF old_entity IS NOT NULL THEN
                UPDATE {_COUNTS} SET withheld = withheld - 1
                 WHERE entity_id = old_entity AND source_key = old_key;
                DELETE FROM {_COUNTS}
                 WHERE entity_id = old_entity AND source_key = old_key AND withheld = 0;
            END IF;
            IF new_entity IS NOT NULL THEN
                INSERT INTO {_COUNTS} AS counts (entity_id, source_key, withheld)
                VALUES (new_entity, new_key, 1)
                ON CONFLICT (entity_id, source_key)
                DO UPDATE SET withheld = counts.withheld + 1;
            END IF;
            RETURN NULL;
        END
        $body$
        """
    )


def upgrade() -> None:
    op.execute(_LOCK)
    _replace_function(_SOURCE_LOCK)


def _core_319_installation_count() -> int:
    """Count schema-local core chains still at or after core_319 (core_308 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 319
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def downgrade() -> None:
    op.execute(_LOCK)
    if _core_319_installation_count() > 1:
        return
    _replace_function("")
