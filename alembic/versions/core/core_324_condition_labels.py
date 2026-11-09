"""Condition store label snapshots (bu-dhloi1.196).

Revision ID: core_324
Revises: core_323
Create Date: 2026-11-09 00:00:00.000000

RFC 0038 S4 ("evaluation joins labels") over the core_323 store. Every condition instance,
receipt and fire intent carries a version 1 S1 label: ``label_instr`` (I), ``label_data`` (D)
and ``label_sens`` (C), with the same grammar as ``butlers.core.goal_labels``.

* The instance's label is the registering wait envelope's snapshot. It is immutable once
  written (``condition_label_guard``), and it is part of the instance's boundary, so a
  re-registration under a different label is refused by the service.
* A fire intent and every receipt must carry exactly its instance's label
  (``condition_label_fence``): nothing is widened and nothing is laundered into a fire, even
  by the trusted evaluator role.
* Rows written before this revision (the store was dark, so normally none) take the unknown
  label, ``{world}`` and all six categories, never owner data. The columns then have no
  default, so a writer that omits a label is refused.

The guards are separate triggers and functions rather than edits to core_323's, so this
revision can be rolled back by dropping its own objects. The core chain runs once per butler
schema over these shared tables, so every statement is idempotent and only the last schema to
leave core_324 drops the columns, and only while no instance exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_324"
down_revision = "core_323"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_324:condition-labels', 0))"
_TABLES = ("condition_instances", "condition_receipts", "condition_fire_intents")
_ROLLBACK_REFUSAL = (
    "core_324 cannot drop condition labels while the store holds instances; "
    "keep core_324 installed."
)
_ALL_SIX = "'{credentials,finance,health,location,relationship_notes,routine}'::text[]"
_PRINCIPAL = "^(owner|owner_device|world|(domain|external):[a-z0-9][a-z0-9_.-]{0,127})$"

_VALID = f"""
CREATE OR REPLACE FUNCTION public.condition_label_valid(
    v SMALLINT, i TEXT[], d TEXT[], c TEXT[])
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT COALESCE(
        v = 1
        AND cardinality(i) > 0 AND cardinality(d) > 0
        AND array_position(i, NULL) IS NULL AND array_position(d, NULL) IS NULL
        AND array_position(c, NULL) IS NULL
        AND (SELECT count(DISTINCT x) = count(*) FROM unnest(i) x)
        AND (SELECT count(DISTINCT x) = count(*) FROM unnest(d) x)
        AND (SELECT count(DISTINCT x) = count(*) FROM unnest(c) x)
        AND (SELECT bool_and(x ~ '{_PRINCIPAL}') FROM unnest(i || d) x)
        AND c <@ {_ALL_SIX},
        false)
$$;
"""

_GUARDS = r"""
CREATE OR REPLACE FUNCTION public.condition_label_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF ROW(NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens)
       IS DISTINCT FROM
       ROW(OLD.label_version, OLD.label_instr, OLD.label_data, OLD.label_sens) THEN
        RAISE EXCEPTION 'a condition label snapshot is immutable' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.condition_label_fence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.condition_instances i
        WHERE i.id = NEW.instance_id
          AND ROW(i.label_version, i.label_instr, i.label_data, i.label_sens)
              IS NOT DISTINCT FROM
              ROW(NEW.label_version, NEW.label_instr, NEW.label_data, NEW.label_sens)) THEN
        RAISE EXCEPTION 'a receipt or fire intent carries its instance label exactly'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS condition_label_guard ON public.condition_instances;
CREATE TRIGGER condition_label_guard BEFORE UPDATE ON public.condition_instances
    FOR EACH ROW EXECUTE FUNCTION public.condition_label_guard();
DROP TRIGGER IF EXISTS condition_label_guard ON public.condition_fire_intents;
CREATE TRIGGER condition_label_guard BEFORE UPDATE ON public.condition_fire_intents
    FOR EACH ROW EXECUTE FUNCTION public.condition_label_guard();
DROP TRIGGER IF EXISTS condition_label_fence ON public.condition_receipts;
CREATE TRIGGER condition_label_fence BEFORE INSERT ON public.condition_receipts
    FOR EACH ROW EXECUTE FUNCTION public.condition_label_fence();
DROP TRIGGER IF EXISTS condition_label_fence ON public.condition_fire_intents;
CREATE TRIGGER condition_label_fence BEFORE INSERT ON public.condition_fire_intents
    FOR EACH ROW EXECUTE FUNCTION public.condition_label_fence();
"""


def _columns(table: str) -> None:
    """Add the label columns; pre-existing rows take the unknown label, then no default."""
    op.execute(
        f"""
        ALTER TABLE public.{table}
            ADD COLUMN IF NOT EXISTS label_version SMALLINT NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS label_instr TEXT[] NOT NULL DEFAULT '{{world}}',
            ADD COLUMN IF NOT EXISTS label_data TEXT[] NOT NULL DEFAULT '{{world}}',
            ADD COLUMN IF NOT EXISTS label_sens TEXT[] NOT NULL DEFAULT {_ALL_SIX}
        """
    )
    op.execute(
        f"""
        ALTER TABLE public.{table}
            ALTER COLUMN label_version DROP DEFAULT,
            ALTER COLUMN label_instr DROP DEFAULT,
            ALTER COLUMN label_data DROP DEFAULT,
            ALTER COLUMN label_sens DROP DEFAULT
        """
    )
    op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {table}_label_valid")
    op.execute(
        f"""
        ALTER TABLE public.{table} ADD CONSTRAINT {table}_label_valid CHECK (
            public.condition_label_valid(label_version, label_instr, label_data, label_sens))
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the label columns."""
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
                    THEN substring(version_num FROM 6)::integer >= 324
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_VALID)
    for table in _TABLES:
        _columns(table)
    op.execute(_GUARDS)


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.condition_instances') IS NOT NULL
               AND EXISTS (SELECT 1 FROM public.condition_instances) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS condition_label_guard ON public.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS condition_label_fence ON public.{table}")
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {table}_label_valid")
        op.execute(
            f"""
            ALTER TABLE public.{table}
                DROP COLUMN IF EXISTS label_version, DROP COLUMN IF EXISTS label_instr,
                DROP COLUMN IF EXISTS label_data, DROP COLUMN IF EXISTS label_sens
            """
        )
    op.execute("DROP FUNCTION IF EXISTS public.condition_label_guard()")
    op.execute("DROP FUNCTION IF EXISTS public.condition_label_fence()")
    op.execute(
        "DROP FUNCTION IF EXISTS public.condition_label_valid(SMALLINT, TEXT[], TEXT[], TEXT[])"
    )
