"""OwnTracks immutable birth, permanent floors and committed source receipts.

Revision ID: core_264
Revises: core_265

Raw mutation remains connector_writer-owned. Chronicler receives only the
explicit additional SELECT receipt surfaces. Legacy NULL accepted lineage is
unknown; this migration does not certify projection or delete any evidence.
"""

import sqlalchemy as sa

from alembic import op

revision = "core_264"
down_revision = "core_265"
branch_labels = None
depends_on = None

_LOCK = "butlers:core_264:owntracks-retention"
_RECEIPTS = (
    "owntracks_retention_tombstones",
    "owntracks_retention_batches",
    "owntracks_retention_batch_rows",
)


def _create_local_tables(schema: str, statement: str) -> None:
    """New own ledgers share the established core writer's owner.

    The core foundation state table predates this feature in this exact schema.
    Current invocation identity is not a retained object's permanent owner.
    Never transfer an existing relation or grant membership to make it fit.
    """
    bind = op.get_bind()
    owner = bind.execute(
        sa.text("""
        SELECT pg_catalog.pg_get_userbyid(s.relowner)
        FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid=s.relnamespace
        WHERE n.nspname=:schema AND s.relname='state' AND s.relkind='r'
    """),
        {"schema": schema},
    ).scalar_one()
    present = set(
        bind.execute(
            sa.text("""
        SELECT c.relname FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=:schema AND c.relname IN
          ('location_retention_copy_receipts','location_retention_source_floors',
           'location_catalog_copy_loans','location_catalog_copy_dispositions',
           'location_catalog_copy_lifetimes','location_catalog_copy_finished',
           'location_runtime_context_intents','location_runtime_context_bindings','location_runtime_context_ended','location_runtime_context_server_finished','location_runtime_context_episodes','location_runtime_context_artifacts','location_runtime_context_dispositions','location_runtime_tool_intents','location_runtime_tool_inputs','location_runtime_tool_results')
    """),
            {"schema": schema},
        ).scalars()
    )
    op.execute(statement)
    quote = bind.dialect.identifier_preparer.quote
    for table in (
        "location_retention_copy_receipts",
        "location_retention_source_floors",
        "location_catalog_copy_loans",
        "location_catalog_copy_dispositions",
        "location_catalog_copy_lifetimes",
        "location_catalog_copy_finished",
        "location_runtime_context_intents",
        "location_runtime_context_bindings",
        "location_runtime_context_ended",
        "location_runtime_context_server_finished",
        "location_runtime_context_episodes",
        "location_runtime_context_artifacts",
        "location_runtime_context_dispositions",
        "location_runtime_tool_intents",
        "location_runtime_tool_inputs",
        "location_runtime_tool_results",
    ):
        if table not in present:
            op.execute(f"ALTER TABLE {quote(schema)}.{quote(table)} OWNER TO {quote(owner)}")


def _validate_local_tables(schema: str) -> None:
    expected = {
        "location_retention_copy_receipts": [
            ("decision_id", "uuid", True),
            ("manifest_digest", "bytea", True),
            ("receipt_id", "uuid", True),
            ("source_kind", "text", True),
            ("forgotten_count", "integer", True),
            ("committed_at", "timestamp with time zone", True),
        ],
        "location_retention_source_floors": [
            ("dedupe_digest", "bytea", True),
            ("request_id", "uuid", True),
            ("decision_id", "uuid", True),
            ("logical_source_digest", "bytea", True),
        ],
    }
    expected.update(
        {
            "location_catalog_copy_loans": [
                ("loan_id", "uuid", True),
                ("source_generation", "uuid", True),
                ("catalog_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receiving_incarnation", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_lifetimes": [
                ("loan_id", "uuid", True),
                ("holder_kind", "text", True),
                ("holder_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_finished": [
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_catalog_copy_dispositions": [
                ("loan_id", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
        }
    )
    expected.update(
        {
            "location_runtime_context_intents": [
                ("input_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("server_request", "uuid", False),
                ("captured_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_bindings": [
                ("input_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("bundle_digest", "bytea", True),
                ("context_digest", "bytea", True),
                ("system_digest", "bytea", True),
                ("prompt_digest", "bytea", True),
                ("exclusive_input", "boolean", True),
                ("context_bytes", "integer", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_ended": [
                ("input_generation", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
        }
    )
    expected.update(
        {
            "location_runtime_context_server_finished": [
                ("input_generation", "uuid", True),
                ("server_request", "uuid", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_episodes": [
                ("input_generation", "uuid", True),
                ("episode_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_artifacts": [
                ("artifact_generation", "uuid", True),
                ("input_generation", "uuid", True),
                ("memory_table", "text", True),
                ("artifact_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_intents": [
                ("tool_generation", "uuid", True),
                ("receiving_session", "uuid", True),
                ("tool_name", "text", True),
                ("module_name", "text", True),
                ("input_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_inputs": [
                ("tool_generation", "uuid", True),
                ("loan_id", "uuid", True),
                ("body_digest", "bytea", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_tool_results": [
                ("tool_generation", "uuid", True),
                ("outcome", "text", True),
                ("result_digest", "bytea", False),
                ("exclusive_inputs", "boolean", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
            "location_runtime_context_dispositions": [
                ("input_generation", "uuid", True),
                ("decision_id", "uuid", True),
                ("manifest_digest", "bytea", True),
                ("receipt_id", "uuid", True),
                ("committed_at", "timestamp with time zone", True),
            ],
        }
    )
    expected_constraints = {
        "location_retention_copy_receipts": {
            "PRIMARY KEY (decision_id)",
            "UNIQUE (receipt_id)",
            "CHECK ((octet_length(manifest_digest) = 32))",
            "CHECK ((source_kind = 'switchboard_skipped'::text))",
            "CHECK ((forgotten_count > 0))",
        },
        "location_retention_source_floors": {
            "PRIMARY KEY (dedupe_digest)",
            "CHECK ((octet_length(dedupe_digest) = 32))",
            "CHECK ((octet_length(logical_source_digest) = 32))",
            "FOREIGN KEY (decision_id) REFERENCES location_retention_copy_receipts(decision_id)",
        },
    }
    expected_constraints.update(
        {
            "location_catalog_copy_loans": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_catalog_copy_lifetimes": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
                "CHECK ((holder_kind = ANY (ARRAY['server_response'::text, "
                "'runtime_session'::text, 'unbound_processing'::text])))",
            },
            "location_catalog_copy_finished": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_lifetimes(loan_id)",
            },
            "location_catalog_copy_dispositions": {
                "PRIMARY KEY (loan_id)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
            },
        }
    )
    expected_constraints.update(
        {
            "location_runtime_context_intents": {
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receiving_session)",
            },
            "location_runtime_context_bindings": {
                "PRIMARY KEY (input_generation)",
                "CHECK ((octet_length(system_digest) = 32))",
                "CHECK ((octet_length(prompt_digest) = 32))",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "UNIQUE (receiving_session)",
                "CHECK ((octet_length(context_digest) = 32))",
                "CHECK ((octet_length(bundle_digest) = 32))",
                "CHECK ((context_bytes >= 0))",
            },
            "location_runtime_context_ended": {
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receipt_id)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
            },
        }
    )
    expected_constraints.update(
        {
            "location_runtime_context_server_finished": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "PRIMARY KEY (input_generation)",
                "UNIQUE (receipt_id)",
            },
            "location_runtime_context_episodes": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "UNIQUE (episode_id)",
                "CHECK ((octet_length(body_digest) = 32))",
                "PRIMARY KEY (input_generation, episode_id)",
            },
            "location_runtime_context_artifacts": {
                "PRIMARY KEY (artifact_generation)",
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "CHECK ((memory_table = ANY (ARRAY['facts'::text, 'rules'::text])))",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_runtime_tool_intents": {
                "PRIMARY KEY (tool_generation)",
                "FOREIGN KEY (receiving_session) REFERENCES sessions(id)",
                "CHECK ((octet_length(input_digest) = 32))",
            },
            "location_runtime_tool_inputs": {
                "PRIMARY KEY (tool_generation, loan_id)",
                "UNIQUE (loan_id)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "FOREIGN KEY (loan_id) REFERENCES location_catalog_copy_loans(loan_id)",
                "CHECK ((octet_length(body_digest) = 32))",
            },
            "location_runtime_tool_results": {
                "PRIMARY KEY (tool_generation)",
                "FOREIGN KEY (tool_generation) REFERENCES "
                "location_runtime_tool_intents(tool_generation)",
                "UNIQUE (receipt_id)",
                "CHECK ((outcome = ANY (ARRAY['success'::text, 'error'::text])))",
                "CHECK (((result_digest IS NULL) OR (octet_length(result_digest) = 32)))",
                "CHECK (((outcome = 'success'::text) = (result_digest IS NOT NULL)))",
            },
            "location_runtime_context_dispositions": {
                "FOREIGN KEY (input_generation) REFERENCES "
                "location_runtime_context_intents(input_generation)",
                "PRIMARY KEY (input_generation)",
                "CHECK ((octet_length(manifest_digest) = 32))",
                "UNIQUE (receipt_id)",
            },
        }
    )
    bind = op.get_bind()
    for table, shape in expected.items():
        relation = bind.execute(
            sa.text("""
            SELECT c.oid,c.relkind,
              c.relowner=s.relowner AND s.relkind='r',
              pg_catalog.pg_get_userbyid(c.relowner)=current_user,
              current_user=session_user,
              pg_catalog.pg_has_role(current_user,s.relowner,'SET')
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_class s ON s.relnamespace=n.oid AND s.relname='state'
            WHERE n.nspname=:schema AND c.relname=:table
        """),
            {"schema": schema, "table": table},
        ).one()
        if relation[1] != "r" or not relation[2]:
            # Fixed booleans only: no role names, OIDs, schema/raw source values.
            predicates = (
                relation[1] == "r",
                relation[2],
                relation[3],
                relation[4],
                relation[5],
            )
            raise RuntimeError(
                "Location retention local table identity differs; "
                + ",".join(str(value is True).lower() for value in predicates)
            )
        actual = [
            tuple(row)
            for row in bind.execute(
                sa.text("""
            SELECT attname,pg_catalog.format_type(atttypid,atttypmod),attnotnull
            FROM pg_catalog.pg_attribute WHERE attrelid=:oid AND attnum>0 AND NOT attisdropped
            ORDER BY attnum
        """),
                {"oid": relation[0]},
            )
        ]
        constraints = set(
            bind.execute(
                sa.text("""
            SELECT pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint
            WHERE conrelid=:oid AND convalidated
        """),
                {"oid": relation[0]},
            ).scalars()
        )
        if actual != shape or constraints != expected_constraints[table]:
            raise RuntimeError("Location retention local table shape differs")


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
    _create_local_tables(
        schema,
        """
        CREATE TABLE IF NOT EXISTS location_retention_copy_receipts (
          decision_id UUID PRIMARY KEY,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          source_kind TEXT NOT NULL CHECK(source_kind='switchboard_skipped'),
          forgotten_count INTEGER NOT NULL CHECK(forgotten_count>0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_intents (
          input_generation UUID PRIMARY KEY,
          receiving_session UUID NOT NULL UNIQUE,
          server_request UUID,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_bindings (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          receiving_session UUID NOT NULL UNIQUE REFERENCES sessions(id),
          bundle_digest BYTEA NOT NULL CHECK(octet_length(bundle_digest)=32),
          context_digest BYTEA NOT NULL CHECK(octet_length(context_digest)=32),
          system_digest BYTEA NOT NULL CHECK(octet_length(system_digest)=32),
          prompt_digest BYTEA NOT NULL CHECK(octet_length(prompt_digest)=32),
          exclusive_input BOOLEAN NOT NULL,
          context_bytes INTEGER NOT NULL CHECK(context_bytes>=0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_ended (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_server_finished (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          server_request UUID NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_episodes (
          input_generation UUID NOT NULL REFERENCES location_runtime_context_intents,
          episode_id UUID NOT NULL UNIQUE,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(input_generation,episode_id)
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_artifacts (
          artifact_generation UUID PRIMARY KEY,
          input_generation UUID NOT NULL REFERENCES location_runtime_context_intents,
          memory_table TEXT NOT NULL CHECK(memory_table IN ('facts','rules')),
          artifact_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_context_dispositions (
          input_generation UUID PRIMARY KEY REFERENCES location_runtime_context_intents,
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_loans (
          loan_id UUID PRIMARY KEY,
          source_generation UUID NOT NULL,
          catalog_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receiving_incarnation UUID NOT NULL,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_lifetimes (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_loans(loan_id),
          holder_kind TEXT NOT NULL CHECK(holder_kind IN (
            'server_response','runtime_session','unbound_processing')),
          holder_id UUID NOT NULL,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_intents (
          tool_generation UUID PRIMARY KEY,
          receiving_session UUID NOT NULL REFERENCES sessions(id),
          tool_name TEXT NOT NULL,
          module_name TEXT NOT NULL,
          input_digest BYTEA NOT NULL CHECK(octet_length(input_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_inputs (
          tool_generation UUID NOT NULL REFERENCES location_runtime_tool_intents,
          loan_id UUID NOT NULL UNIQUE REFERENCES location_catalog_copy_loans,
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(tool_generation,loan_id)
        );
        CREATE TABLE IF NOT EXISTS location_runtime_tool_results (
          tool_generation UUID PRIMARY KEY REFERENCES location_runtime_tool_intents,
          outcome TEXT NOT NULL CHECK(outcome IN ('success','error')),
          result_digest BYTEA CHECK(result_digest IS NULL OR octet_length(result_digest)=32),
          exclusive_inputs BOOLEAN NOT NULL,
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK((outcome='success')=(result_digest IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_finished (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_lifetimes(loan_id),
          body_digest BYTEA NOT NULL CHECK(octet_length(body_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_catalog_copy_dispositions (
          loan_id UUID PRIMARY KEY REFERENCES location_catalog_copy_loans(loan_id),
          decision_id UUID NOT NULL,
          manifest_digest BYTEA NOT NULL CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL UNIQUE,
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS location_retention_source_floors (
          dedupe_digest BYTEA PRIMARY KEY CHECK(octet_length(dedupe_digest)=32),
          request_id UUID NOT NULL,
          decision_id UUID NOT NULL REFERENCES location_retention_copy_receipts(decision_id),
          logical_source_digest BYTEA NOT NULL CHECK(octet_length(logical_source_digest)=32)
        );
    """,
    )
    _validate_local_tables(schema)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {quoted_schema}.preserve_location_copy_history()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          RAISE EXCEPTION 'Location source floors are permanent';
        END $$;
        DROP TRIGGER IF EXISTS preserve_location_copy_history ON location_retention_copy_receipts;
        CREATE TRIGGER preserve_location_copy_history BEFORE UPDATE OR DELETE
          ON location_retention_copy_receipts FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
        DROP TRIGGER IF EXISTS preserve_location_source_floor ON location_retention_source_floors;
        CREATE TRIGGER preserve_location_source_floor BEFORE UPDATE OR DELETE
          ON location_retention_source_floors FOR EACH ROW
          EXECUTE FUNCTION {quoted_schema}.preserve_location_copy_history();
    """)
    for table in (
        "location_catalog_copy_loans",
        "location_catalog_copy_dispositions",
        "location_catalog_copy_lifetimes",
        "location_catalog_copy_finished",
        "location_runtime_context_intents",
        "location_runtime_context_bindings",
        "location_runtime_context_ended",
        "location_runtime_context_server_finished",
        "location_runtime_context_episodes",
        "location_runtime_context_artifacts",
        "location_runtime_context_dispositions",
        "location_runtime_tool_intents",
        "location_runtime_tool_inputs",
        "location_runtime_tool_results",
    ):
        op.execute(f"""
            DROP TRIGGER IF EXISTS preserve_location_copy_history ON {table};
            CREATE TRIGGER preserve_location_copy_history BEFORE UPDATE OR DELETE
            ON {table} FOR EACH ROW
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
             OR EXISTS(SELECT 1 FROM location_retention_copy_receipts)
             OR EXISTS(SELECT 1 FROM location_catalog_copy_loans)
             OR EXISTS(SELECT 1 FROM location_runtime_context_intents)
             OR EXISTS(SELECT 1 FROM location_runtime_tool_intents) THEN
            RAISE EXCEPTION 'retention history exists; roll forward instead of erasing floors';
          END IF;
        END $$;
    """)
    # Shared additive columns/tables stay inert on downgrade. They are needed by
    # other independently upgraded schema installations and cannot resurrect raw
    # data. No worker is installed by this migration.
