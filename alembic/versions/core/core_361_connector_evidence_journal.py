"""connectors.evidence_journal: one append-only evidence journal for sources with no table.

Revision ID: core_361
Revises: core_360
Create Date: 2026-11-23 00:00:00.000000

bu-uhbhzq.4 slice 3.  For gmail, google-drive, google-health, telegram-bot, telegram-user and
whatsapp-user the only verbatim copy of what arrived was ``switchboard.message_inbox``, a routing
buffer. A connector now commits one row here before it acknowledges, advances a checkpoint or
submits to Switchboard.

Shape:

  - ``UNIQUE (source, endpoint_identity, provider_id, provider_revision)``: a duplicate delivery
    collapses; a new provider revision appends.
  - Unpartitioned (thousands of rows a month).
  - Append-only: a row trigger refuses UPDATE and DELETE, a statement trigger refuses TRUNCATE.
    A sanctioned deleter arrives with the per-source retention contract (bu-uhbhzq.7).
  - ``event_time`` is nullable; the event clock is bu-h40h2b.12's.
  - ``raw`` holds exactly what the envelope's ``message_inbox.raw_payload`` would hold at its
    ingestion tier, after the bearer scrub; it never holds more.

Grants: ``connector_writer`` holds SELECT and INSERT and nothing else; no other role holds any
privilege. The journal holds full message content, and the connectors schema's default privileges
would otherwise give every runtime role SELECT (and the writer UPDATE and DELETE), so both are
revoked here and scripts/init-db.sql re-asserts the revokes after its blanket grants. A reader
grant comes only with an owner-approved consumer. The identity column needs no sequence grant.

The relation is shared across schema-local core chains, so the migration is replay-safe: an
advisory lock serialises installs, triggers are dropped before they are created, and the downgrade
drops the shared table only when no other schema-local chain is still at core_361 or later
(core_246 pattern). It refuses while the table has rows.
"""

from __future__ import annotations

from alembic import op

revision = "core_361"
down_revision = "core_360"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_TABLE = "connectors.evidence_journal"
_GUARD = "connectors.evidence_journal_append_only"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _installation_count() -> int:
    """Count schema-local core chains still at core_361 or later (includes this one)."""
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
                    THEN substring(version_num FROM 6)::integer >= 361
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_361:evidence-journal', 0))"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source                  TEXT NOT NULL,
            endpoint_identity       TEXT NOT NULL,
            provider_id             TEXT NOT NULL,
            provider_revision       TEXT NOT NULL DEFAULT '',
            event_time              TIMESTAMPTZ,
            observed_at             TIMESTAMPTZ NOT NULL,
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            capture_schema_version  INTEGER NOT NULL,
            ingestion_tier          TEXT NOT NULL CHECK (ingestion_tier IN ('full', 'metadata')),
            idempotency_key         TEXT,
            raw                     JSONB NOT NULL,
            CONSTRAINT evidence_journal_identity_key
                UNIQUE (source, endpoint_identity, provider_id, provider_revision)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_evidence_journal_source_observed
            ON {_TABLE} (source, observed_at)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_evidence_journal_idempotency_key
            ON {_TABLE} (idempotency_key) WHERE idempotency_key IS NOT NULL
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
        CREATE OR REPLACE TRIGGER evidence_journal_append_only
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE TRIGGER evidence_journal_no_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_GUARD}()
        """
    )
    # The journal holds full message content: connector_writer only. The connectors-schema default
    # privileges grant every runtime role SELECT on a new table, so revoke them here.
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
                  AND r.rolname <> {_quote_literal(_CONNECTOR_ROLE)}
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
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_361:evidence-journal', 0))"
    )
    if _installation_count() > 1:
        # Another schema-local chain still depends on the shared journal.
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass({_quote_literal(_TABLE)}) IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'connectors.evidence_journal holds evidence; downgrade refused (bu-uhbhzq.4)';
            END IF;
        END;
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}()")
