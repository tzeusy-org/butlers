"""OwnTracks immutable birth, permanent floors and committed source receipts.

Revision ID: core_264
Revises: core_261

Raw mutation remains connector_writer-owned. Chronicler receives only the
explicit additional SELECT receipt surfaces. Legacy NULL accepted lineage is
unknown; this migration does not certify projection or delete any evidence.
"""

import sqlalchemy as sa

from alembic import op

revision = "core_264"
down_revision = "core_261"
branch_labels = None
depends_on = None

_LOCK = "butlers:core_264:owntracks-retention"
_RECEIPTS = (
    "owntracks_retention_tombstones",
    "owntracks_retention_batches",
    "owntracks_retention_batch_rows",
)


def upgrade() -> None:
    schema = op.get_bind().execute(sa.text("SELECT current_schema()")).scalar_one()
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote(schema)
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    op.execute("""
        ALTER TABLE connectors.owntracks_points
          ADD COLUMN IF NOT EXISTS retention_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS source_revision BIGINT NOT NULL DEFAULT 1
            CHECK (source_revision > 0),
          ADD COLUMN IF NOT EXISTS logical_source_digest BYTEA
            CHECK (octet_length(logical_source_digest)=32),
          ADD COLUMN IF NOT EXISTS content_digest BYTEA CHECK (octet_length(content_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_payload_digest BYTEA
            CHECK (octet_length(accepted_payload_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_normalized_digest BYTEA
            CHECK (octet_length(accepted_normalized_digest)=32),
          ADD COLUMN IF NOT EXISTS accepted_request_id UUID;
        UPDATE connectors.owntracks_points SET retention_at =
          CASE WHEN abs(extract(epoch FROM (ts-recorded_at))) <= 14400
               THEN ts ELSE recorded_at END
          WHERE retention_at IS NULL;
        ALTER TABLE connectors.owntracks_points ALTER COLUMN retention_at SET NOT NULL;
        CREATE OR REPLACE FUNCTION connectors.freeze_owntracks_birth()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.retention_at IS NULL THEN
              NEW.retention_at := CASE
                WHEN abs(extract(epoch FROM (NEW.ts-NEW.recorded_at))) <= 14400
                THEN NEW.ts ELSE NEW.recorded_at END;
            END IF;
          ELSIF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'OwnTracks source revisions are immutable';
          END IF;
          RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS freeze_owntracks_birth ON connectors.owntracks_points;
        CREATE TRIGGER freeze_owntracks_birth BEFORE INSERT OR UPDATE
          ON connectors.owntracks_points FOR EACH ROW
          EXECUTE FUNCTION connectors.freeze_owntracks_birth();
        CREATE INDEX IF NOT EXISTS ix_owntracks_points_arrival
          ON connectors.owntracks_points(recorded_at, id);
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_tombstones (
          logical_source_digest BYTEA PRIMARY KEY CHECK(octet_length(logical_source_digest)=32),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          retention_at TIMESTAMPTZ NOT NULL,
          decision_id UUID NOT NULL,
          batch_id UUID NOT NULL,
          purged_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_batches (
          batch_id UUID PRIMARY KEY,
          decision_id UUID NOT NULL UNIQUE,
          grant_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          policy_version BIGINT NOT NULL CHECK(policy_version>0),
          cutoff TIMESTAMPTZ NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          deleted_count INTEGER NOT NULL CHECK(deleted_count>=0),
          already_forgotten_count INTEGER NOT NULL CHECK(already_forgotten_count>=0)
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_retention_batch_rows (
          batch_id UUID NOT NULL REFERENCES connectors.owntracks_retention_batches(batch_id),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32),
          disposition TEXT NOT NULL CHECK(disposition IN ('deleted','already_forgotten')),
          PRIMARY KEY(batch_id,raw_id,source_revision),
          FOREIGN KEY(logical_source_digest)
            REFERENCES connectors.owntracks_retention_tombstones(logical_source_digest)
        );
        CREATE OR REPLACE FUNCTION connectors.preserve_owntracks_retention_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'OwnTracks retention history is permanent';
        END $$;
    """)
    # Per-owning-schema ledger: source-holder actions never write through a
    # peer role. Core replay also covers Switchboard-only and legacy public DBs.
    op.execute("""
        CREATE TABLE location_retention_copy_receipts (
          decision_id UUID PRIMARY KEY,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          source_kind TEXT NOT NULL CHECK(source_kind='switchboard_skipped'),
          forgotten_count INTEGER NOT NULL CHECK(forgotten_count>0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_retention_source_floors (
          dedupe_digest BYTEA PRIMARY KEY CHECK(octet_length(dedupe_digest)=32),
          request_id UUID NOT NULL,
          decision_id UUID NOT NULL REFERENCES location_retention_copy_receipts(decision_id),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32)
        );
    """)
    op.execute(f"""
        CREATE FUNCTION {quoted_schema}.preserve_location_copy_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Location source floors are permanent';
        END $$;
        CREATE TRIGGER preserve_location_copy_history BEFORE UPDATE OR DELETE
          ON location_retention_copy_receipts FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
        CREATE TRIGGER preserve_location_source_floor BEFORE UPDATE OR DELETE
          ON location_retention_source_floors FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
    """)
    for table in _RECEIPTS:
        op.execute(f"""
            DROP TRIGGER IF EXISTS preserve_retention_history ON connectors.{table};
            CREATE TRIGGER preserve_retention_history BEFORE UPDATE OR DELETE
              ON connectors.{table} FOR EACH ROW
              EXECUTE FUNCTION connectors.preserve_owntracks_retention_history();
        """)
        op.execute(f"""
            DO $$ BEGIN
              IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='connector_writer') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON connectors.{table} TO connector_writer;
              END IF;
              IF '{table}' <> 'owntracks_retention_tombstones' AND EXISTS(
                SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='butler_chronicler_rw') THEN
                GRANT SELECT ON connectors.{table} TO butler_chronicler_rw;
              END IF;
            END $$;
        """)


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    # Core is replayed per schema: removing shared history while another schema
    # is still installed is forbidden even when the current table is empty.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM connectors.owntracks_retention_tombstones)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_retention_batches)
             OR EXISTS(SELECT 1 FROM connectors.owntracks_points
                       WHERE accepted_request_id IS NOT NULL)
             OR EXISTS(SELECT 1 FROM location_retention_copy_receipts) THEN
            RAISE EXCEPTION 'retention history exists; roll forward instead of erasing floors';
          END IF;
        END $$;
    """)
    # Shared additive columns/tables stay inert on downgrade. They are needed by
    # other independently upgraded schema installations and cannot resurrect raw
    # data. No worker is installed by this migration.
