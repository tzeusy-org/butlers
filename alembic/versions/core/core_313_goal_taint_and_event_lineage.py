"""Goal taint on shared D6 stores and goal lineage on domain events.

Revision ID: core_313
Revises: core_312
Create Date: 2026-11-05 00:00:00.000000

bu-pkuny2 (RFC 0038 D6, D7).  A row written inside a broker-verified call whose
assertion is exposed stamps ``taint_goal_id`` / ``taint_invocation_id``: a
lineage pointer, not a boolean, so containment can find every row one exposed
goal wrote.  The shared ``public`` D6 stores that lacked a taint field get one:

* ``public.captures`` (capture);
* ``public.dashboard_messages`` (conversation history);
* ``public.insight_candidates`` (insight candidates).

``public.domain_events`` gains the producing call's verified lineage
(``goal_id``, ``goal_invocation_id``, ``goal_effect_id``, ``goal_exposed``), which
caused admission reads.  Every column is nullable: NULL is a row written outside
a verified call, including every pre-switch row.

The memory stores (episodes, facts, rules; contact notes and decision memory are
facts) take the same columns in ``mem_017`` and relationship entity facts in
``rel_039``.  Goal outcomes have no store yet; the goal runtime adds the field
when it adds the store.

Every statement is idempotent: the core chain runs once per butler schema against
these shared objects.  A replay that cannot own a shared table skips its ALTER
(core_255 tolerance), but upgrade then asserts every column exists: the writers
bind them, so a skipped ALTER is never recorded as applied.  Only the last schema
still at core_313 drops them on downgrade (core_308 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_313"
down_revision = "core_312"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_313:goal-taint', 0))"
_TAINTED = ("public.captures", "public.dashboard_messages", "public.insight_candidates")
_EVENT_COLUMNS = (
    ("goal_id", "UUID"),
    ("goal_invocation_id", "UUID"),
    ("goal_effect_id", "UUID"),
    ("goal_exposed", "BOOLEAN"),
)


def _core_313_installation_count() -> int:
    """Count schema-local core chains still at or after core_313."""
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
                    THEN substring(version_num FROM 6)::integer >= 313
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str) -> None:
    """core_255's tolerance: a schema replay that cannot own the shared table skips it."""
    op.execute(
        f"""
        DO $do$
        BEGIN
            EXECUTE {_quote_literal(statement)};
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $do$;
        """
    )


def _expected_columns() -> list[tuple[str, str]]:
    columns = [
        (table.removeprefix("public."), column)
        for table in _TAINTED
        for column in ("taint_goal_id", "taint_invocation_id")
    ]
    return columns + [("domain_events", name) for name, _ in _EVENT_COLUMNS]


def _assert_upgraded() -> str:
    """The writers bind these columns, so a skipped ALTER must not be recorded as applied."""
    wanted = ", ".join(f"('{table}', '{column}')" for table, column in _expected_columns())
    return f"""
    DO $do$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM (VALUES {wanted}) AS wanted(table_name, column_name)
            WHERE to_regclass('public.' || wanted.table_name) IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = wanted.table_name
                  AND c.column_name = wanted.column_name
              )
        ) THEN
            RAISE EXCEPTION 'core_313: goal taint/lineage columns were not added '
                '(is a shared table owned by another role? see bu-jjv2du)';
        END IF;
    END
    $do$;
    """


def upgrade() -> None:
    op.execute(_LOCK)
    for table in _TAINTED:
        _execute_best_effort(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS taint_goal_id UUID, "
            "ADD COLUMN IF NOT EXISTS taint_invocation_id UUID"
        )
    _execute_best_effort(
        "ALTER TABLE public.domain_events "
        + ", ".join(f"ADD COLUMN IF NOT EXISTS {name} {kind}" for name, kind in _EVENT_COLUMNS)
    )
    op.execute(_assert_upgraded())


def downgrade() -> None:
    op.execute(_LOCK)
    if _core_313_installation_count() > 1:
        return
    _execute_best_effort(
        "ALTER TABLE public.domain_events "
        + ", ".join(f"DROP COLUMN IF EXISTS {name}" for name, _ in _EVENT_COLUMNS)
    )
    for table in _TAINTED:
        _execute_best_effort(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS taint_invocation_id, "
            "DROP COLUMN IF EXISTS taint_goal_id"
        )
