"""connectors.calendar_event_revisions: an append-only journal of every observed event version.

Revision ID: core_366
Revises: core_361
Create Date: 2026-11-23 00:00:00.000000

bu-uhbhzq.1 slice 2.  Google keeps no edit history and the per-butler calendar projections
overwrite and delete rows in place, so "what was planned for that day, as seen that day" cannot be
rebuilt. The Google Calendar connector commits one row here for every provider event version it
observes, before it submits anything or saves its sync token.

Shape:

  - ``UNIQUE (endpoint_identity, calendar_id, event_id, etag)``: Google changes the etag on every
    modification (RSVPs included), so a replayed page collapses and a new version appends.
  - ``observation`` is ``baseline`` (first full sync), ``change`` (incremental) or ``resync``
    (410 recovery). The kind of change (moved, retitled, cancelled, RSVP) is derived at read time
    by diffing an event's consecutive revisions; it is not stored.
  - ``policy_blocked`` rows keep structure and times only (``raw`` is NULL).
  - Append-only: a row trigger refuses UPDATE and DELETE, a statement trigger refuses TRUNCATE. The
    per-source retention contract is bu-uhbhzq.7's; until then the journal is never pruned.

Grants: ``connector_writer`` holds SELECT and INSERT; ``butler_chronicler_rw`` holds SELECT; no
other role holds any privilege. ``raw`` keeps titles, locations, times and attendees verbatim, so
the connectors schema's default privileges (SELECT for every runtime role, DML for the writer) are
revoked here and scripts/init-db.sql re-asserts the revokes after its blanket grants.

The relation is shared across schema-local core chains, so the migration is replay-safe: an advisory
lock serialises installs, a chain that finds everything installed does no DDL, and the downgrade
drops the shared table only when no other schema-local chain is still at core_366 or later, and
refuses while the table has rows (core_361 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_366"
down_revision = "core_361"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_TABLE = "connectors.calendar_event_revisions"
_GUARD = "connectors.calendar_event_revisions_append_only"
_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_366:calendar-event-revisions', 0))"
)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _installation_count() -> int:
    """Count schema-local core chains still at core_366 or later (includes this one)."""
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
                    THEN substring(version_num FROM 6)::integer >= 366
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


# Catalog-only: the table, both triggers, the guard, all three indexes and the expected ACL
# (connector_writer exactly SELECT+INSERT, butler_chronicler_rw exactly SELECT, nobody else) are in
# place. A later schema's chain that finds them returns without DDL, because CREATE OR REPLACE
# TRIGGER and CREATE INDEX IF NOT EXISTS lock the table even as a no-op.
_ALREADY_INSTALLED = f"""
SELECT to_regclass({_quote_literal(_TABLE)}) IS NOT NULL
AND (
    SELECT count(*) FROM pg_trigger
    WHERE tgrelid = to_regclass({_quote_literal(_TABLE)}) AND NOT tgisinternal
      AND tgname IN ('calendar_event_revisions_append_only', 'calendar_event_revisions_no_truncate')
) = 2
AND EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'connectors' AND p.proname = 'calendar_event_revisions_append_only'
)
AND (
    SELECT count(*) FROM pg_indexes
    WHERE schemaname = 'connectors'
      AND indexname IN (
          'ix_calendar_event_revisions_starts', 'ix_calendar_event_revisions_observed'
      )
) = 2
AND NOT EXISTS (
    SELECT 1 FROM pg_class c
    CROSS JOIN LATERAL aclexplode(c.relacl) AS a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
    WHERE c.oid = to_regclass({_quote_literal(_TABLE)}) AND a.grantee <> c.relowner
      AND NOT (
          (r.rolname = {_quote_literal(_CONNECTOR_ROLE)}
              AND a.privilege_type IN ('SELECT', 'INSERT'))
          OR (r.rolname = {_quote_literal(_CHRONICLER_ROLE)} AND a.privilege_type = 'SELECT')
      )
)
AND (
    NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CONNECTOR_ROLE)})
    OR (
        SELECT count(*) FROM pg_class c
        CROSS JOIN LATERAL aclexplode(c.relacl) AS a
        JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.oid = to_regclass({_quote_literal(_TABLE)})
          AND r.rolname = {_quote_literal(_CONNECTOR_ROLE)}
          AND a.privilege_type IN ('SELECT', 'INSERT')
    ) = 2
)
AND (
    NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CHRONICLER_ROLE)})
    OR EXISTS (
        SELECT 1 FROM pg_class c
        CROSS JOIN LATERAL aclexplode(c.relacl) AS a
        JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.oid = to_regclass({_quote_literal(_TABLE)})
          AND r.rolname = {_quote_literal(_CHRONICLER_ROLE)}
          AND a.privilege_type = 'SELECT'
    )
)
"""


def upgrade() -> None:
    op.execute(_LOCK)
    if op.get_bind().exec_driver_sql(_ALREADY_INSTALLED).scalar():
        return
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint_identity   TEXT NOT NULL,
            calendar_id         TEXT NOT NULL,
            event_id            TEXT NOT NULL,
            recurring_event_id  TEXT,
            original_start_time TIMESTAMPTZ,
            etag                TEXT NOT NULL,
            provider_updated    TIMESTAMPTZ,
            sequence            INTEGER,
            status              TEXT,
            starts_at           TIMESTAMPTZ,
            ends_at             TIMESTAMPTZ,
            all_day             BOOLEAN NOT NULL DEFAULT false,
            observation         TEXT NOT NULL
                CHECK (observation IN ('baseline', 'change', 'resync')),
            policy_blocked      BOOLEAN NOT NULL DEFAULT false,
            raw                 JSONB,
            observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT calendar_event_revisions_identity_key
                UNIQUE (endpoint_identity, calendar_id, event_id, etag),
            CONSTRAINT calendar_event_revisions_blocked_has_no_raw
                CHECK (NOT policy_blocked OR raw IS NULL)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_calendar_event_revisions_starts
            ON {_TABLE} (endpoint_identity, starts_at)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_calendar_event_revisions_observed
            ON {_TABLE} (observed_at)
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; no retention contract exists (bu-uhbhzq.7)',
                TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE TRIGGER calendar_event_revisions_append_only
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE TRIGGER calendar_event_revisions_no_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_GUARD}()
        """
    )
    # Verbatim titles, locations and attendees: the writer and Chronicler only. The connectors
    # schema's default privileges grant every runtime role SELECT on a new table, so revoke them.
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE _role TEXT;
        BEGIN
            FOR _role IN
                SELECT r.rolname
                FROM pg_class c
                CROSS JOIN LATERAL aclexplode(c.relacl) AS a
                JOIN pg_roles r ON r.oid = a.grantee
                WHERE c.oid = {_quote_literal(_TABLE)}::regclass
                  AND a.grantee <> c.relowner
                  AND r.rolname NOT IN (
                      {_quote_literal(_CONNECTOR_ROLE)}, {_quote_literal(_CHRONICLER_ROLE)}
                  )
            LOOP
                EXECUTE format('REVOKE ALL ON TABLE {_TABLE} FROM %I', _role);
            END LOOP;
        END;
        $$
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CONNECTOR_ROLE)}
            ) THEN
                REVOKE UPDATE, DELETE, TRUNCATE ON TABLE {_TABLE}
                    FROM {_quote_ident(_CONNECTOR_ROLE)};
                GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_quote_ident(_CONNECTOR_ROLE)};
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CHRONICLER_ROLE)}
            ) THEN
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {_TABLE}
                    FROM {_quote_ident(_CHRONICLER_ROLE)};
                GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)};
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        # Another schema-local chain still depends on the shared journal.
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass({_quote_literal(_TABLE)}) IS NOT NULL THEN
                -- Wait out every uncommitted connector insert first: a row the check cannot see
                -- yet would otherwise commit while the DROP waits, and the DROP would erase it.
                LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                    RAISE EXCEPTION
                        'connectors.calendar_event_revisions holds history; '
                        'downgrade refused (bu-uhbhzq.1)';
                END IF;
            END IF;
        END;
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}()")
