"""Add education to calendar.v_overlay_contributions.

Revision ID: core_387
Revises: core_386
Create Date: 2026-11-26 00:00:00.000000

The Education butler contributes one aggregated "N reviews due" overlay claim per day
(bu-5ii4gj.13 S5b), so its ``state`` table joins the read-only overlay view that core_140 created
for finance, health, relationship and travel. Same five RFC 0010 guardrails as core_140:
read-only UNION view, hardcoded ``butler`` literal per term, ``calendar/overlay/%`` key filter,
fail-open health check on the read path, and a migration-tracked, reversible SELECT grant on
``education.state`` to ``butler_calendar_rw``.

``upgrade`` rebuilds the view from the schemas that exist; ``downgrade`` restores core_140's four.

Core revisions replay once per butler schema against the shared ``calendar`` view and the shared
``education.state`` grant. Both directions therefore take one advisory lock, upgrade is idempotent,
and downgrade restores the four-schema view only when the last schema at or past this revision
leaves it, so one butler stepping back never drops education from the view the others still use.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import context, op

revision: str = "core_387"
down_revision: str = "core_386"
branch_labels = None
depends_on = None

# core_140's contributors, then the whole set after this migration. Must match
# ``OVERLAY_CONTRIBUTORS`` in butlers.api.read_models.calendar_workspace.
_PREVIOUS_SCHEMAS: tuple[str, ...] = ("finance", "health", "relationship", "travel")
_SPECIALIST_SCHEMAS: tuple[str, ...] = ("education", *_PREVIOUS_SCHEMAS)
_NEW_SCHEMA = "education"

_SHARED_DDL_LOCK = "butlers:core_387:overlay_view"

_CALENDAR_ROLE = "butler_calendar_rw"
_VIEW_FQN = "calendar.v_overlay_contributions"
_KEY_PREFIX = "calendar/overlay/%"


def _installed_schema_count() -> int:
    """Count schemas whose core chain is at this revision or a descendant of it."""
    script = context.script
    descendants: set[str] = set()
    pending = [revision]
    while pending:
        current = pending.pop()
        if current in descendants:
            continue
        descendants.add(current)
        pending.extend(script.get_revision(current).nextrev)

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
    for schema in list(schemas):
        versions = set(
            bind.exec_driver_sql(f"SELECT version_num FROM {quote(schema)}.alembic_version")
            .scalars()
            .all()
        )
        count += int(bool(versions & descendants))
    return count


def _take_shared_lock() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")


def _state_table_exists(schema_name: str) -> bool:
    bind = op.get_bind()
    relname = f"{schema_name}.state"
    return (
        bind.execute(sa.text("SELECT to_regclass(:relname)"), {"relname": relname}).scalar()
        is not None
    )


def _grant_best_effort(verb: str, schema: str) -> None:
    """GRANT/REVOKE SELECT on <schema>.state; tolerates a missing role, table or privilege."""
    preposition = "TO" if verb == "GRANT" else "FROM"
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{schema}.state') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_CALENDAR_ROLE}')
            THEN
                EXECUTE '{verb} SELECT ON TABLE {schema}.state {preposition} "{_CALENDAR_ROLE}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
    """)


def _rebuild_view(schemas: tuple[str, ...]) -> None:
    available = tuple(s for s in schemas if _state_table_exists(s))
    if available:
        terms = "\n    UNION ALL\n    ".join(
            f"SELECT '{s}' AS butler, key, value FROM {s}.state WHERE key LIKE '{_KEY_PREFIX}'"
            for s in available
        )
    else:
        terms = "SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE"
    op.execute(f"CREATE OR REPLACE VIEW {_VIEW_FQN} AS\n    {terms}")


def upgrade() -> None:
    _take_shared_lock()
    _grant_best_effort("GRANT", _NEW_SCHEMA)
    _rebuild_view(_SPECIALIST_SCHEMAS)


def downgrade() -> None:
    _take_shared_lock()
    if _installed_schema_count() > 1:
        return
    _rebuild_view(_PREVIOUS_SCHEMAS)
    _grant_best_effort("REVOKE", _NEW_SCHEMA)
