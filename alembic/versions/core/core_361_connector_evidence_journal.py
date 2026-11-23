"""connectors.evidence_journal: one append-only evidence journal for sources with no table.

Revision ID: core_361
Revises: core_358
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

Grants: INSERT and SELECT to ``connector_writer`` only. A Chronicler read grant comes with its
first consumer (bu-uhbhzq.11/.12). The identity column needs no sequence grant.

The downgrade refuses while the table has rows.
"""

from __future__ import annotations

from alembic import op

revision = "core_361"
down_revision = "core_358"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_TABLE = "connectors.evidence_journal"
_GUARD = "connectors.evidence_journal_append_only"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
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
        CREATE TRIGGER evidence_journal_append_only
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER evidence_journal_no_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_GUARD}()
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(_CONNECTOR_ROLE)}) THEN
                GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_quote_ident(_CONNECTOR_ROLE)};
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
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
