"""Causal coverage, irreversible decisions and truthful retention attempts.

Revision ID: chronicler_027
Revises: chronicler_026
"""

import sqlalchemy as sa

from alembic import op

revision = "chronicler_027"
down_revision = "chronicler_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_bind().execute(sa.text("SELECT current_schema()")).scalar_one()
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote(schema)
    op.execute("""
        ALTER TABLE source_adapter_state
          ADD COLUMN raw_evidence_retention TEXT,
          ADD COLUMN projected_evidence_retention TEXT,
          ADD COLUMN allowed_spatial_precision_m INTEGER,
          ADD COLUMN source_tombstone_behavior TEXT;
        CREATE TABLE location_retention_policy (
          singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
          days SMALLINT NOT NULL DEFAULT 30 CHECK(days BETWEEN 1 AND 30),
          version BIGINT NOT NULL DEFAULT 1 CHECK(version>0),
          spatial_scheme_version INTEGER NOT NULL DEFAULT 1 CHECK(spatial_scheme_version=1),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          updated_by TEXT NOT NULL DEFAULT 'trusted-bootstrap'
        );
        INSERT INTO location_retention_policy(singleton) VALUES(true);
        CREATE TABLE location_projection_coverage (
          raw_id UUID NOT NULL,
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32),
          content_digest BYTEA NOT NULL CHECK(octet_length(content_digest)=32),
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          adapter_name TEXT NOT NULL CHECK(adapter_name IN (
            'owntracks.points','owntracks.place_cluster','owntracks.ssid_presence')),
          mapping_revision BYTEA NOT NULL CHECK(octet_length(mapping_revision)=32),
          projection_batch_id UUID NOT NULL,
          disposition TEXT NOT NULL CHECK(disposition IN (
            'pending','complete','terminal_no_output','invalid')),
          output_ids UUID[] NOT NULL DEFAULT '{}',
          output_revision BYTEA CHECK(octet_length(output_revision)=32),
          original_output_revision BYTEA CHECK(octet_length(original_output_revision)=32),
          last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          completed_at TIMESTAMPTZ,
          CHECK ((disposition IN ('complete','terminal_no_output')) = (completed_at IS NOT NULL)),
          PRIMARY KEY(raw_id,source_revision,adapter_name,mapping_revision)
        );
        CREATE TABLE location_projection_privacy_transitions (
          decision_id UUID NOT NULL,
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL,
          adapter_name TEXT NOT NULL,
          mapping_revision BYTEA NOT NULL,
          previous_revision BYTEA NOT NULL CHECK(octet_length(previous_revision)=32),
          reduced_revision BYTEA NOT NULL CHECK(octet_length(reduced_revision)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(decision_id,raw_id,source_revision,adapter_name,mapping_revision),
          FOREIGN KEY(raw_id,source_revision,adapter_name,mapping_revision)
            REFERENCES location_projection_coverage(raw_id,source_revision,adapter_name,mapping_revision)
        );
        CREATE TABLE location_projection_cursors (
          adapter_name TEXT PRIMARY KEY,
          recorded_at TIMESTAMPTZ NOT NULL,
          raw_id UUID NOT NULL
        );
        CREATE TABLE location_projection_heads (
          adapter_name TEXT PRIMARY KEY,
          mapping_revision BYTEA NOT NULL CHECK(octet_length(mapping_revision)=32),
          replay_pending BOOLEAN NOT NULL DEFAULT false,
          replay_watermark TIMESTAMPTZ,
          replay_raw_id UUID
        );
        CREATE TABLE location_projection_outputs (
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL,
          adapter_name TEXT NOT NULL,
          mapping_revision BYTEA NOT NULL,
          output_kind TEXT NOT NULL CHECK(output_kind IN ('point_event','episode')),
          output_id UUID NOT NULL,
          PRIMARY KEY(raw_id,source_revision,adapter_name,mapping_revision,output_kind,output_id),
          FOREIGN KEY(raw_id,source_revision,adapter_name,mapping_revision)
            REFERENCES location_projection_coverage(raw_id,source_revision,adapter_name,mapping_revision)
        );
        CREATE TABLE location_retention_runs (
          run_id UUID PRIMARY KEY,
          policy_version BIGINT NOT NULL CHECK(policy_version>0),
          cutoff TIMESTAMPTZ NOT NULL,
          attempt_started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          lease_until TIMESTAMPTZ NOT NULL,
          completion_at TIMESTAMPTZ,
          status TEXT NOT NULL CHECK(status IN (
            'preparing','pending','no_work','complete','failed','cancelled','unknown','unavailable')),
          reason_code TEXT CHECK(reason_code IN (
            'projection_pending','holder_pending','source_unavailable','policy_unavailable',
            'lock_timeout','source_changed','receipt_unknown','storage_error','cancelled')),
          prepared_count INTEGER NOT NULL DEFAULT 0 CHECK(prepared_count>=0),
          deleted_count INTEGER NOT NULL DEFAULT 0 CHECK(deleted_count>=0),
          blocked_count INTEGER NOT NULL DEFAULT 0 CHECK(blocked_count>=0),
          unknown_count INTEGER NOT NULL DEFAULT 0 CHECK(unknown_count>=0),
          overdue_count INTEGER CHECK(overdue_count>=0),
          holder_pending_count INTEGER CHECK(holder_pending_count>=0),
          counts_observed_at TIMESTAMPTZ
        );
        CREATE TABLE location_retention_plans (
          decision_id UUID PRIMARY KEY,
          run_id UUID NOT NULL REFERENCES location_retention_runs(run_id),
          policy_version BIGINT NOT NULL CHECK(policy_version>0),
          cutoff TIMESTAMPTZ NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          state TEXT NOT NULL CHECK(state IN (
            'prepared','holder_pending','ready','raw_unknown','complete','refused')),
          prepared_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_retention_plan_rows (
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32),
          content_digest BYTEA NOT NULL CHECK(octet_length(content_digest)=32),
          retention_at TIMESTAMPTZ NOT NULL,
          accepted_request_id UUID NOT NULL,
          accepted_payload_digest BYTEA NOT NULL CHECK(octet_length(accepted_payload_digest)=32),
          accepted_normalized_digest BYTEA NOT NULL CHECK(octet_length(accepted_normalized_digest)=32),
          PRIMARY KEY(decision_id,raw_id,source_revision),
          UNIQUE(raw_id,source_revision)
        );
        CREATE TABLE location_retention_plan_outputs (
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL,
          adapter_name TEXT NOT NULL,
          mapping_revision BYTEA NOT NULL CHECK(octet_length(mapping_revision)=32),
          output_kind TEXT NOT NULL CHECK(output_kind IN ('point_event','episode')),
          output_id UUID NOT NULL,
          PRIMARY KEY(decision_id,raw_id,source_revision,adapter_name,mapping_revision,
                      output_kind,output_id)
        );
        CREATE TABLE location_retention_grants (
          grant_id UUID PRIMARY KEY,
          batch_id UUID NOT NULL UNIQUE,
          decision_id UUID NOT NULL UNIQUE REFERENCES location_retention_plans(decision_id),
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          ready_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          lease_version BIGINT NOT NULL CHECK(lease_version>0),
          lease_until TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE location_retention_holder_receipts (
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          owning_butler TEXT NOT NULL,
          holder_kind TEXT NOT NULL,
          holder_generation UUID NOT NULL,
          source_digest BYTEA NOT NULL CHECK(octet_length(source_digest)=32),
          receipt_id UUID NOT NULL,
          observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(decision_id,owning_butler,holder_kind,holder_generation)
        );
        CREATE TABLE location_evidence_tombstones (
          event_id UUID PRIMARY KEY,
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL CHECK(source_revision>0),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32),
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          spatial_precision_m INTEGER NOT NULL CHECK(spatial_precision_m=150),
          spatial_scheme_version INTEGER NOT NULL CHECK(spatial_scheme_version=1),
          occurred_at TIMESTAMPTZ NOT NULL,
          privacy TEXT NOT NULL,
          purged_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_expired_evidence_links (
          episode_id UUID NOT NULL REFERENCES episodes(id),
          event_id UUID NOT NULL REFERENCES location_evidence_tombstones(event_id),
          relation TEXT NOT NULL,
          PRIMARY KEY(episode_id,event_id,relation)
        );
        CREATE TABLE location_retention_local_receipts (
          decision_id UUID PRIMARY KEY REFERENCES location_retention_plans(decision_id),
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          removed_event_count INTEGER NOT NULL CHECK(removed_event_count>=0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_summary_floors (
          episode_id UUID PRIMARY KEY REFERENCES episodes(id),
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          spatial_precision_m INTEGER NOT NULL CHECK(spatial_precision_m=150),
          spatial_scheme_version INTEGER NOT NULL CHECK(spatial_scheme_version=1)
        );
    """)

    op.execute(f"""
        CREATE FUNCTION {quoted_schema}.preserve_location_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Location retention history is permanent';
        END $$;
        CREATE FUNCTION {quoted_schema}.freeze_location_decision()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF TG_OP='DELETE' OR
             (NEW.decision_id,NEW.run_id,NEW.policy_version,NEW.cutoff,
              NEW.manifest_digest,NEW.prepared_at) IS DISTINCT FROM
             (OLD.decision_id,OLD.run_id,OLD.policy_version,OLD.cutoff,
              OLD.manifest_digest,OLD.prepared_at) THEN
            RAISE EXCEPTION 'Location retention decision is immutable';
          END IF;
          IF NEW.state<>OLD.state AND NOT (
             (OLD.state='prepared' AND NEW.state='holder_pending') OR
             (OLD.state='holder_pending' AND NEW.state IN ('ready','refused')) OR
             (OLD.state='ready' AND NEW.state IN ('raw_unknown','complete')) OR
             (OLD.state='raw_unknown' AND NEW.state='complete')) THEN
            RAISE EXCEPTION 'Location retention transition refused';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER freeze_location_decision BEFORE UPDATE OR DELETE
          ON location_retention_plans FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.freeze_location_decision();
        CREATE FUNCTION {quoted_schema}.freeze_location_grant()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF TG_OP='DELETE' OR
             (NEW.grant_id,NEW.batch_id,NEW.decision_id,NEW.manifest_digest,NEW.ready_at)
                IS DISTINCT FROM
             (OLD.grant_id,OLD.batch_id,OLD.decision_id,OLD.manifest_digest,OLD.ready_at)
             OR NEW.lease_version<>OLD.lease_version+1
             OR NEW.lease_until<=pg_catalog.clock_timestamp()
             OR NEW.lease_until>pg_catalog.clock_timestamp()+interval '120 seconds' THEN
            RAISE EXCEPTION 'Location retention grant renewal refused';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER freeze_location_grant BEFORE UPDATE OR DELETE
          ON location_retention_grants FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.freeze_location_grant();
    """)
    for table in (
        "location_projection_privacy_transitions",
        "location_retention_plan_rows",
        "location_retention_plan_outputs",
        "location_retention_holder_receipts",
        "location_evidence_tombstones",
        "location_expired_evidence_links",
        "location_retention_local_receipts",
        "location_summary_floors",
    ):
        op.execute(f"""
            CREATE TRIGGER preserve_location_history BEFORE UPDATE OR DELETE
              ON {table} FOR EACH ROW
              EXECUTE FUNCTION {quoted_schema}.preserve_location_history();
        """)


def downgrade() -> None:
    schema = op.get_bind().execute(sa.text("SELECT current_schema()")).scalar_one()
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote(schema)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM location_retention_plans)
             OR EXISTS(SELECT 1 FROM location_evidence_tombstones)
             OR EXISTS(SELECT 1 FROM location_summary_floors) THEN
            RAISE EXCEPTION 'retention decisions exist; roll forward instead of erasing floors';
          END IF;
        END $$;
        DROP TABLE location_summary_floors,location_retention_local_receipts,
          location_expired_evidence_links,location_evidence_tombstones,
          location_retention_holder_receipts,location_retention_grants,
          location_retention_plan_outputs,location_retention_plan_rows,
          location_retention_plans,location_retention_runs,
          location_projection_outputs,location_projection_privacy_transitions,
          location_projection_cursors,location_projection_heads,
          location_projection_coverage,location_retention_policy;
        ALTER TABLE source_adapter_state
          DROP COLUMN raw_evidence_retention,
          DROP COLUMN projected_evidence_retention,
          DROP COLUMN allowed_spatial_precision_m,
          DROP COLUMN source_tombstone_behavior;
    """)

    op.execute(f"""
        DROP FUNCTION {quoted_schema}.preserve_location_history(),
          {quoted_schema}.freeze_location_decision(),
          {quoted_schema}.freeze_location_grant();
    """)
