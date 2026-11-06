"""Primary-side columns the public MCP projection filters on (RFC 0039 D2, bu-y66o00.3).

Revision ID: core_318
Revises: core_317
Create Date: 2026-11-06 00:00:00.000000

Row filters may use only plain replica-identity columns, and no JSONB is ever published, so what a
filter needs is kept on the primary as source-owned materializations:

* ``public.entities`` gains ``merged_into``, ``deleted``, ``unidentified`` and
  ``placeholder_name``, which a ``BEFORE`` trigger keeps equal to their ``metadata`` keys and
  ``canonical_name`` (REQ-public-mcp-projection-007). A ``merged_into`` that is not a UUID fails
  the write rather than letting the column and the metadata disagree; the only writer stores
  ``str(uuid)``.
* ``public.entity_graph_edges`` gains ``publish_key``, kept by a ``BEFORE`` trigger, because
  ``predicate`` is NULL on a withheld stub and cannot enter a replica identity
  (REQ-public-mcp-projection-013). The trigger name sorts after the Chronicler identity guard, so
  it reads the final row.
* ``public.entity_graph_withheld_counts`` holds each entity's stubs per
  ``<source_schema>.<source_table>``, kept by an ``AFTER`` trigger. That function is
  SECURITY DEFINER because edge writers include roles without table privileges here, such as the
  definer role ``chronicler_memory_owner``. Bootstrap's default privileges still give butler roles
  DML on the table, as on every ``public`` table (``scripts/init-db.sql``); no code uses it, and
  the parity test would catch a stray write. RLS is not used to narrow that, because it would stop
  the replication role's initial COPY. A count that reaches zero is deleted, so the table holds
  only entities with something withheld. Its primary key covers its ``source_key`` filter, so it
  keeps ``REPLICA IDENTITY DEFAULT``.

Nothing here creates a publication: bead ``bu-y66o00.4`` does, from the manifest. Without one a
replica identity only affects WAL at ``wal_level=logical``.

The core chain runs once per butler schema against these shared objects. The first schema installs
and backfills them under the advisory lock with every statement in one transaction; a later schema
finds them installed and does nothing, so it takes no table lock. Only the last schema still at
core_318 removes them (core_308 pattern).
"""

from __future__ import annotations

from alembic import op
from butlers.migrations.projection import drop_replica_identity, ensure_replica_identity

revision = "core_318"
down_revision = "core_317"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_318:projection-source', 0))"
_COUNTS = "public.entity_graph_withheld_counts"
_ENTITY_FILTER_COLUMNS = ("deleted", "unidentified", "placeholder_name")
_EDGE_FILTER_COLUMNS = ("sensitivity", "publish_key")


def _merged_into(row: str) -> str:
    return f"({row}metadata ->> 'merged_into')::uuid"


def _deleted(row: str) -> str:
    return f"({row}metadata ->> 'deleted_at') IS NOT NULL"


def _unidentified(row: str) -> str:
    return f"COALESCE(({row}metadata ->> 'unidentified') = 'true', false)"


def _placeholder_name(row: str) -> str:
    return (
        f"COALESCE({row}canonical_name = 'Unknown (' || ({row}metadata ->> 'source_channel') "
        f"|| ' ' || ({row}metadata ->> 'source_value') || ')', false)"
    )


def _publish_key(row: str) -> str:
    return (
        f"CASE WHEN {row}withheld_reason IS NULL "
        f"THEN {row}source_schema || '.' || {row}source_table || ':' || {row}predicate "
        "ELSE '' END"
    )


def _install_entity_flags() -> None:
    op.execute(
        """
        ALTER TABLE public.entities
            ADD COLUMN merged_into UUID,
            ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN unidentified BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN placeholder_name BOOLEAN NOT NULL DEFAULT false
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.entities_sync_projection_flags()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            NEW.merged_into := {_merged_into("NEW.")};
            NEW.deleted := {_deleted("NEW.")};
            NEW.unidentified := {_unidentified("NEW.")};
            NEW.placeholder_name := {_placeholder_name("NEW.")};
            RETURN NEW;
        END
        $body$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.entities_sync_projection_flags() FROM PUBLIC")
    op.execute(
        f"""
        UPDATE public.entities SET
            merged_into = {_merged_into("")},
            deleted = {_deleted("")},
            unidentified = {_unidentified("")},
            placeholder_name = {_placeholder_name("")}
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_entities_projection_flags
        BEFORE INSERT OR UPDATE ON public.entities
        FOR EACH ROW EXECUTE FUNCTION public.entities_sync_projection_flags()
        """
    )
    ensure_replica_identity("public.entities", ("id",), _ENTITY_FILTER_COLUMNS)


def _install_publish_key() -> None:
    op.execute(
        "ALTER TABLE public.entity_graph_edges ADD COLUMN publish_key TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        f"""
        CREATE FUNCTION public.entity_graph_edges_sync_publish_key()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            NEW.publish_key := {_publish_key("NEW.")};
            RETURN NEW;
        END
        $body$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.entity_graph_edges_sync_publish_key() FROM PUBLIC")
    op.execute(f"UPDATE public.entity_graph_edges SET publish_key = {_publish_key('')}")
    op.execute(
        """
        CREATE TRIGGER trg_entity_graph_edges_publish_key
        BEFORE INSERT OR UPDATE ON public.entity_graph_edges
        FOR EACH ROW EXECUTE FUNCTION public.entity_graph_edges_sync_publish_key()
        """
    )
    ensure_replica_identity("public.entity_graph_edges", ("id",), _EDGE_FILTER_COLUMNS)


def _install_withheld_counts() -> None:
    op.execute(
        f"""
        CREATE TABLE {_COUNTS} (
            entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            source_key TEXT NOT NULL,
            withheld INTEGER NOT NULL CHECK (withheld >= 0),
            PRIMARY KEY (entity_id, source_key)
        )
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.entity_graph_edges_sync_withheld_counts()
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
            END IF;
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
    op.execute(
        "REVOKE ALL ON FUNCTION public.entity_graph_edges_sync_withheld_counts() FROM PUBLIC"
    )
    # Installing the trigger holds SHARE ROW EXCLUSIVE on the edges until commit, so no edge write
    # can fall between the trigger and this count.
    op.execute(
        """
        CREATE TRIGGER trg_entity_graph_edges_withheld_counts
        AFTER INSERT OR UPDATE OR DELETE ON public.entity_graph_edges
        FOR EACH ROW EXECUTE FUNCTION public.entity_graph_edges_sync_withheld_counts()
        """
    )
    op.execute(
        f"""
        INSERT INTO {_COUNTS} (entity_id, source_key, withheld)
        SELECT subject_entity_id, source_schema || '.' || source_table, count(*)
        FROM public.entity_graph_edges
        WHERE withheld_reason IS NOT NULL
        GROUP BY 1, 2
        """
    )


def _installed() -> bool:
    return bool(op.get_bind().exec_driver_sql(f"SELECT to_regclass('{_COUNTS}')").scalar())


def upgrade() -> None:
    op.execute(_LOCK)
    if _installed():
        return
    _install_entity_flags()
    _install_publish_key()
    _install_withheld_counts()


def _core_318_installation_count() -> int:
    """Count schema-local core chains still at or after core_318 (core_308 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 318
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def downgrade() -> None:
    op.execute(_LOCK)
    # Every schema still at core_318 runs code that may expect these, so only the last one leaving
    # removes them.
    if _core_318_installation_count() > 1:
        return
    op.execute(
        "DROP TRIGGER IF EXISTS trg_entity_graph_edges_withheld_counts ON public.entity_graph_edges"
    )
    op.execute("DROP FUNCTION IF EXISTS public.entity_graph_edges_sync_withheld_counts()")
    op.execute(f"DROP TABLE IF EXISTS {_COUNTS}")

    drop_replica_identity("public.entity_graph_edges")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_entity_graph_edges_publish_key ON public.entity_graph_edges"
    )
    op.execute("DROP FUNCTION IF EXISTS public.entity_graph_edges_sync_publish_key()")
    op.execute("ALTER TABLE public.entity_graph_edges DROP COLUMN IF EXISTS publish_key")

    drop_replica_identity("public.entities")
    op.execute("DROP TRIGGER IF EXISTS trg_entities_projection_flags ON public.entities")
    op.execute("DROP FUNCTION IF EXISTS public.entities_sync_projection_flags()")
    op.execute(
        """
        ALTER TABLE public.entities
            DROP COLUMN IF EXISTS placeholder_name,
            DROP COLUMN IF EXISTS unidentified,
            DROP COLUMN IF EXISTS deleted,
            DROP COLUMN IF EXISTS merged_into
        """
    )
