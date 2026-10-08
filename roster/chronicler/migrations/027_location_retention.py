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
          phase TEXT NOT NULL DEFAULT 'coarsen' CHECK(phase IN ('coarsen','dispose')),
          previous_revision BYTEA NOT NULL CHECK(octet_length(previous_revision)=32),
          reduced_revision BYTEA NOT NULL CHECK(octet_length(reduced_revision)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(decision_id,raw_id,source_revision,adapter_name,mapping_revision,phase),
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
        CREATE TABLE location_native_copy_births (
          copy_generation UUID NOT NULL,
          output_kind TEXT NOT NULL CHECK(output_kind IN ('point_event','episode')),
          output_id UUID NOT NULL,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          lineage_known BOOLEAN NOT NULL,
          receiving_session UUID,
          receiving_server_request UUID,
          exclusive_input BOOLEAN NOT NULL,
          producer_kind TEXT NOT NULL CHECK(producer_kind IN ('native_mcp','api_export','native_dispatch','native_memory')),
          produced_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(copy_generation,output_kind,output_id)
        );
        -- Native producer-owned frontier, never an empty/absent success.
        -- Enrollment and fixed owning readback transport must be installed
        -- before any row can qualify; receipt IDs are bindings, not authority.
        CREATE TABLE location_native_dispatch_inputs (
          origin_kind TEXT NOT NULL DEFAULT 'api_export'
            CHECK(origin_kind IN ('api_export','native_memory')),
          input_generation UUID PRIMARY KEY,
          server_request UUID NOT NULL,
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          parent_count INTEGER NOT NULL CHECK(parent_count>0),
          captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_dispatch_parents (
          input_generation UUID NOT NULL REFERENCES location_native_dispatch_inputs(input_generation),
          copy_generation UUID NOT NULL,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          PRIMARY KEY(input_generation,copy_generation)
        );
        CREATE TABLE location_native_dispatch_reservations (
          input_generation UUID PRIMARY KEY REFERENCES location_native_dispatch_inputs(input_generation),
          receiving_session UUID NOT NULL UNIQUE,
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_dispatch_sessions (
          input_generation UUID PRIMARY KEY REFERENCES location_native_dispatch_inputs(input_generation),
          receiving_session UUID NOT NULL UNIQUE,
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_copy_dispositions (
          copy_generation UUID PRIMARY KEY,
          receipt_id UUID NOT NULL UNIQUE,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          receiving_session UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_reservations (
          reservation_id UUID PRIMARY KEY,
          receiving_session UUID NOT NULL,
          content_digest BYTEA NOT NULL CHECK(octet_length(content_digest)=32),
          memory_schema TEXT NOT NULL, writer_role TEXT NOT NULL,
          parent_count INTEGER NOT NULL CHECK(parent_count>0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_parents (
          reservation_id UUID NOT NULL REFERENCES location_native_memory_reservations,
          copy_generation UUID NOT NULL, input_digest BYTEA NOT NULL,
          PRIMARY KEY(reservation_id,copy_generation)
        );
        CREATE TABLE location_native_memory_commits (
          reservation_id UUID PRIMARY KEY REFERENCES location_native_memory_reservations,
          episode_id UUID NOT NULL, body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_dispositions (
          reservation_id UUID PRIMARY KEY REFERENCES location_native_memory_reservations,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_cache_inputs (
          cache_key TEXT NOT NULL,
          copy_generation UUID NOT NULL,
          cache_generation UUID NOT NULL,
          PRIMARY KEY(cache_key,cache_generation,copy_generation)
        );
        CREATE TABLE location_native_cache_heads (
          cache_key TEXT PRIMARY KEY,
          cache_generation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32)
        );
        CREATE TABLE location_legacy_cache_observations (
          cache_key TEXT NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          observation_id UUID NOT NULL UNIQUE,
          observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(cache_key,body_digest)
        );
        CREATE TABLE location_native_cache_exports (
          copy_generation UUID PRIMARY KEY,
          cache_key TEXT NOT NULL,
          cache_generation UUID,
          receiving_server_request UUID,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          produced_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_api_dispositions (
          copy_generation UUID PRIMARY KEY,
          server_request UUID NOT NULL,
          producer_kind TEXT NOT NULL CHECK(producer_kind IN ('native_read','cache')),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_legacy_cache_replacements (
          observation_id UUID PRIMARY KEY REFERENCES location_legacy_cache_observations(observation_id),
          receipt_id UUID NOT NULL UNIQUE,
          replacement_generation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_processing_claims (
          claim_id UUID PRIMARY KEY,
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          parent_count INTEGER NOT NULL CHECK(parent_count>0),
          captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_processing_parents (
          claim_id UUID NOT NULL REFERENCES location_native_processing_claims,
          copy_generation UUID NOT NULL,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          PRIMARY KEY(claim_id,copy_generation)
        );
        CREATE TABLE location_native_processing_finished (
          claim_id UUID PRIMARY KEY REFERENCES location_native_processing_claims,
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_bundles (
          input_generation UUID PRIMARY KEY REFERENCES location_native_dispatch_inputs(input_generation),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          exclusive_input BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_bundle_episodes (
          input_generation UUID NOT NULL REFERENCES location_native_memory_bundles(input_generation),
          episode_id UUID NOT NULL,
          PRIMARY KEY(input_generation,episode_id)
        );
        CREATE TABLE location_native_memory_runtime_receipts (
          input_generation UUID PRIMARY KEY REFERENCES location_native_memory_bundles(input_generation),
          receiving_session UUID NOT NULL UNIQUE,
          system_digest BYTEA NOT NULL CHECK(octet_length(system_digest)=32),
          memory_context_present BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_artifact_dispositions (
          artifact_generation UUID PRIMARY KEY,
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_memory_artifacts (
          artifact_generation UUID PRIMARY KEY,
          input_generation UUID NOT NULL REFERENCES location_native_memory_bundles(input_generation),
          memory_table TEXT NOT NULL CHECK(memory_table IN ('facts','rules')),
          artifact_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          content_digest BYTEA CHECK(content_digest IS NULL OR octet_length(content_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(memory_table,artifact_id)
        );
        ALTER TABLE location_native_memory_artifact_dispositions ADD CONSTRAINT
          location_native_memory_artifact_dispositions_generation_fkey
          FOREIGN KEY(artifact_generation) REFERENCES location_native_memory_artifacts(artifact_generation);
        CREATE TABLE location_native_memory_mutations (
          mutation_generation UUID PRIMARY KEY,
          artifact_generation UUID NOT NULL REFERENCES location_native_memory_artifacts(artifact_generation),
          revision INTEGER NOT NULL CHECK(revision>0),
          previous_generation UUID REFERENCES location_native_memory_mutations(mutation_generation),
          before_digest BYTEA NOT NULL CHECK(octet_length(before_digest)=32),
          after_digest BYTEA NOT NULL CHECK(octet_length(after_digest)=32),
          lifecycle_only BOOLEAN NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(artifact_generation,revision),
          CHECK((revision=1)=(previous_generation IS NULL))
        );
        CREATE TABLE location_native_catalog_generations (
          source_generation UUID PRIMARY KEY,
          catalog_id UUID NOT NULL,
          artifact_generation UUID NOT NULL REFERENCES location_native_memory_artifacts(artifact_generation),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_catalog_heads (
          catalog_id UUID PRIMARY KEY,
          source_generation UUID NOT NULL REFERENCES location_native_catalog_generations(source_generation)
        );
        CREATE TABLE location_native_catalog_loans (
          loan_id UUID PRIMARY KEY,
          source_generation UUID NOT NULL REFERENCES location_native_catalog_generations(source_generation),
          receiver_name TEXT NOT NULL,
          receiving_incarnation UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_catalog_server_dispositions (
          loan_id UUID PRIMARY KEY REFERENCES location_native_catalog_loans(loan_id),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          server_request UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_native_catalog_dispositions (
          source_generation UUID PRIMARY KEY REFERENCES location_native_catalog_generations(source_generation),
          decision_id UUID NOT NULL REFERENCES location_retention_plans(decision_id),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_retention_frontiers (
          decision_id UUID PRIMARY KEY REFERENCES location_retention_plans(decision_id),
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          frontier_generation UUID NOT NULL,
          producer_contract INTEGER NOT NULL CHECK(producer_contract=1),
          expected_count INTEGER NOT NULL CHECK(expected_count>0),
          sealed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE location_retention_frontier_holders (
          decision_id UUID NOT NULL REFERENCES location_retention_frontiers(decision_id),
          owning_butler TEXT NOT NULL,
          holder_kind TEXT NOT NULL,
          holder_generation UUID NOT NULL,
          source_digest BYTEA NOT NULL CHECK(octet_length(source_digest)=32),
          PRIMARY KEY(decision_id,owning_butler,holder_kind,holder_generation)
        );
        CREATE TABLE location_retention_disposal_receipts (
          decision_id UUID PRIMARY KEY REFERENCES location_retention_plans(decision_id),
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          frontier_generation UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          removed_event_count INTEGER NOT NULL CHECK(removed_event_count>=0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
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
        "location_retention_frontier_holders",
        "location_retention_frontiers",
        "location_native_copy_births",
        "location_native_copy_dispositions",
        "location_native_dispatch_inputs",
        "location_native_dispatch_parents",
        "location_native_dispatch_sessions",
        "location_native_dispatch_reservations",
        "location_native_cache_inputs",
        "location_legacy_cache_observations",
        "location_native_cache_exports",
        "location_native_catalog_generations",
        "location_native_catalog_loans",
        "location_native_catalog_dispositions",
        "location_native_catalog_server_dispositions",
        "location_native_processing_claims",
        "location_native_processing_parents",
        "location_native_processing_finished",
        "location_native_memory_bundles",
        "location_native_memory_bundle_episodes",
        "location_native_memory_artifacts",
        "location_native_memory_mutations",
        "location_native_memory_runtime_receipts",
        "location_native_memory_artifact_dispositions",
        "location_native_memory_reservations",
        "location_native_memory_parents",
        "location_native_memory_commits",
        "location_native_memory_dispositions",
        "location_native_api_dispositions",
        "location_legacy_cache_replacements",
        "location_retention_disposal_receipts",
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
             OR EXISTS(SELECT 1 FROM location_summary_floors)
             OR EXISTS(SELECT 1 FROM location_native_copy_births)
             OR EXISTS(SELECT 1 FROM location_native_copy_dispositions)
             OR EXISTS(SELECT 1 FROM location_native_cache_inputs)
             OR EXISTS(SELECT 1 FROM location_legacy_cache_observations)
             OR EXISTS(SELECT 1 FROM location_native_memory_reservations)
             OR EXISTS(SELECT 1 FROM location_native_processing_claims)
             OR EXISTS(SELECT 1 FROM location_native_memory_bundles)
             OR EXISTS(SELECT 1 FROM location_native_catalog_generations)
             OR EXISTS(SELECT 1 FROM location_native_cache_exports)
             OR EXISTS(SELECT 1 FROM location_native_api_dispositions)
             OR EXISTS(SELECT 1 FROM location_native_dispatch_inputs) THEN
            RAISE EXCEPTION 'retention decisions exist; roll forward instead of erasing floors';
          END IF;
        END $$;
        DROP TABLE location_summary_floors,location_retention_local_receipts,
          location_expired_evidence_links,location_evidence_tombstones,
          location_retention_holder_receipts,location_retention_frontier_holders,
          location_retention_disposal_receipts,location_retention_frontiers,
          location_native_catalog_server_dispositions,
          location_native_catalog_dispositions,location_native_catalog_loans,
          location_native_catalog_heads,location_native_catalog_generations,
          location_native_memory_artifact_dispositions,location_native_memory_mutations,
          location_native_memory_artifacts,
          location_native_memory_runtime_receipts,location_native_memory_bundle_episodes,
          location_native_memory_bundles,
          location_native_processing_finished,location_native_processing_parents,
          location_native_processing_claims,
          location_native_copy_births,location_native_copy_dispositions,
          location_native_dispatch_sessions,location_native_dispatch_reservations,
          location_native_dispatch_parents,
          location_native_dispatch_inputs,
          location_native_cache_inputs,location_native_cache_heads,location_retention_grants,
          location_legacy_cache_replacements,location_legacy_cache_observations,
          location_native_memory_dispositions,location_native_memory_commits,
          location_native_memory_parents,location_native_memory_reservations,
          location_native_cache_exports,
          location_native_api_dispositions,
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
