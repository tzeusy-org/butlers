"""Trusted core installer for connector-owned filtered-copy metadata.

No runtime producer provisions a table, role or privilege. The source floor
and native birth refuse other runtime roles after bootstrap default ACL replay.
The trusted actual shared table owner can read installation/history metadata;
Chronicler may observe only content-free committed preparation receipts.
"""

from __future__ import annotations

COPY_TABLES = (
    "owntracks_filtered_copy_births",
    "owntracks_filtered_copy_floors",
    "owntracks_filtered_copy_batches",
    "owntracks_filtered_copy_members",
)


_COLUMNS = {
    "owntracks_filtered_copy_births": {
        "copy_generation": "uuid",
        "filtered_id": "uuid",
        "filtered_received_at": "timestamp with time zone",
        "logical_source_digest": "bytea",
        "raw_digest": "bytea",
        "row_digest": "bytea",
        "producer_contract": "smallint",
        "committed_at": "timestamp with time zone",
    },
    "owntracks_filtered_copy_floors": {
        "logical_source_digest": "bytea",
        "decision_id": "uuid",
        "raw_id": "uuid",
        "source_revision": "bigint",
        "raw_digest": "bytea",
        "manifest_digest": "bytea",
        "committed_at": "timestamp with time zone",
    },
    "owntracks_filtered_copy_batches": {
        "decision_id": "uuid",
        "manifest_digest": "bytea",
        "receipt_id": "uuid",
        "policy_version": "bigint",
        "cutoff": "timestamp with time zone",
        "expected_count": "integer",
        "committed_at": "timestamp with time zone",
    },
    "owntracks_filtered_copy_members": {
        "receipt_id": "uuid",
        "copy_generation": "uuid",
        "original_digest": "bytea",
        "reduced_digest": "bytea",
        "committed_at": "timestamp with time zone",
    },
}


_CONSTRAINTS = {
    "owntracks_filtered_copy_births": [
        ("pkey", "p", [1], None, None, None),
        ("row_key", "u", [2, 3], None, None, None),
        ("logical_len", "c", [4], "octet_lengthlogical_source_digest=32", None, None),
        ("raw_len", "c", [5], "octet_lengthraw_digest=32", None, None),
        ("row_len", "c", [6], "octet_lengthrow_digest=32", None, None),
        ("contract", "c", [7], "producer_contract=1", None, None),
    ],
    "owntracks_filtered_copy_floors": [
        ("pkey", "p", [1], None, None, None),
        ("logical_len", "c", [1], "octet_lengthlogical_source_digest=32", None, None),
        ("revision", "c", [4], "source_revision>0", None, None),
        ("raw_len", "c", [5], "octet_lengthraw_digest=32", None, None),
        ("manifest_len", "c", [6], "octet_lengthmanifest_digest=32", None, None),
    ],
    "owntracks_filtered_copy_batches": [
        ("pkey", "p", [1], None, None, None),
        ("receipt_key", "u", [3], None, None, None),
        ("manifest_len", "c", [2], "octet_lengthmanifest_digest=32", None, None),
        ("version", "c", [4], "policy_version>0", None, None),
        ("count", "c", [6], "expected_count>=0", None, None),
    ],
    "owntracks_filtered_copy_members": [
        ("pkey", "p", [1, 2], None, None, None),
        ("batch_fk", "f", [1], None, "owntracks_filtered_copy_batches", [3]),
        ("birth_fk", "f", [2], None, "owntracks_filtered_copy_births", [1]),
        ("original_len", "c", [3], "octet_lengthoriginal_digest=32", None, None),
        ("reduced_len", "c", [4], "octet_lengthreduced_digest=32", None, None),
    ],
}


def filtered_copy_schema_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS connectors.owntracks_filtered_copy_births (
          copy_generation UUID
            CONSTRAINT owntracks_copy_births_pkey PRIMARY KEY,
          filtered_id UUID NOT NULL,
          filtered_received_at TIMESTAMPTZ NOT NULL,
          logical_source_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_births_logical_len
            CHECK(octet_length(logical_source_digest)=32),
          raw_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_births_raw_len
            CHECK(octet_length(raw_digest)=32),
          row_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_births_row_len
            CHECK(octet_length(row_digest)=32),
          producer_contract SMALLINT NOT NULL
            CONSTRAINT owntracks_copy_births_contract
            CHECK(producer_contract=1),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

            CONSTRAINT owntracks_copy_births_row_key UNIQUE(filtered_id,filtered_received_at)
        );
        CREATE INDEX IF NOT EXISTS ix_owntracks_filtered_copy_source
          ON connectors.owntracks_filtered_copy_births(logical_source_digest,copy_generation);
        CREATE TABLE IF NOT EXISTS connectors.owntracks_filtered_copy_floors (
          logical_source_digest BYTEA
            CONSTRAINT owntracks_copy_floors_pkey PRIMARY KEY
            CONSTRAINT owntracks_copy_floors_logical_len
            CHECK(octet_length(logical_source_digest)=32),
          decision_id UUID NOT NULL,
          raw_id UUID NOT NULL,
          source_revision BIGINT NOT NULL
            CONSTRAINT owntracks_copy_floors_revision
            CHECK(source_revision>0),
          raw_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_floors_raw_len
            CHECK(octet_length(raw_digest)=32),
          manifest_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_floors_manifest_len
            CHECK(octet_length(manifest_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_filtered_copy_batches (
          decision_id UUID
            CONSTRAINT owntracks_copy_batches_pkey PRIMARY KEY,
          manifest_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_batches_manifest_len
            CHECK(octet_length(manifest_digest)=32),
          receipt_id UUID NOT NULL
            CONSTRAINT owntracks_copy_batches_receipt_key UNIQUE,
          policy_version BIGINT NOT NULL
            CONSTRAINT owntracks_copy_batches_version
            CHECK(policy_version>0),
          cutoff TIMESTAMPTZ NOT NULL,
          expected_count INTEGER NOT NULL
            CONSTRAINT owntracks_copy_batches_count
            CHECK(expected_count>=0),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS connectors.owntracks_filtered_copy_members (
          receipt_id UUID NOT NULL

            CONSTRAINT owntracks_copy_members_batch_fk
            REFERENCES connectors.owntracks_filtered_copy_batches(receipt_id),
          copy_generation UUID NOT NULL
            CONSTRAINT owntracks_copy_members_birth_fk
            REFERENCES connectors.owntracks_filtered_copy_births,
          original_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_members_original_len
            CHECK(octet_length(original_digest)=32),
          reduced_digest BYTEA NOT NULL
            CONSTRAINT owntracks_copy_members_reduced_len
            CHECK(octet_length(reduced_digest)=32),
          committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

            CONSTRAINT owntracks_copy_members_pkey PRIMARY KEY(receipt_id,copy_generation)
        );
    """


def filtered_copy_security_sql() -> str:
    """Fixed existing-role policies; bootstrap grants cannot expose a floor."""
    statements = []
    for table in COPY_TABLES:
        relation = f"connectors.{table}"
        readers = (
            "current_user IN ('connector_writer','butler_chronicler_rw')"
            if table.endswith(("batches", "members"))
            else "current_user='connector_writer'"
        )
        # The existing actual shared table owner can inspect metadata for
        # migration history guards; it cannot mint native source write receipt.
        readers += (
            " OR current_user=(SELECT pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='connectors' AND c.relname='owntracks_points' AND c.relkind='r')"
        )
        columns = ",".join(
            "'" + name + ":" + kind + ":true'" for name, kind in sorted(_COLUMNS[table].items())
        )
        constraints = []
        prefix = "owntracks_copy_" + table.removeprefix("owntracks_filtered_copy_")
        for suffix, kind, keys, expression, foreign, foreign_keys in _CONSTRAINTS[table]:
            name = prefix + "_" + suffix
            key_array = ",".join(map(str, keys))
            test = (
                f"conname='{name}' AND contype='{kind}' AND conkey=ARRAY[{key_array}]::smallint[] "
                "AND convalidated AND NOT condeferrable AND NOT condeferred "
            )
            if foreign is not None:
                foreign_array = ",".join(map(str, foreign_keys))
                test += (
                    f"AND confrelid='connectors.{foreign}'::pg_catalog.regclass "
                    f"AND confkey=ARRAY[{foreign_array}]::smallint[] "
                    "AND confupdtype='a' AND confdeltype='a' AND confmatchtype='s' "
                )
            else:
                test += "AND confrelid=0 "
            if expression is not None:
                test += (
                    "AND pg_catalog.regexp_replace(pg_catalog.pg_get_expr(conbin,conrelid),"
                    f"'[()[:space:]]','','g')='{expression}' "
                )
            constraints.append(
                f"NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint WHERE "
                f"conrelid='{relation}'::pg_catalog.regclass AND {test})"
            )
        constraint_test = " OR ".join(constraints)
        expected_constraints = len(_CONSTRAINTS[table])
        statements.append(
            f"""
            DO $native_copy_owner$
            BEGIN
              IF NOT EXISTS(
                SELECT 1 FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='connectors' AND c.relname='{table}'
                  AND c.relkind='r' AND c.relowner=(
                    SELECT p.relowner FROM pg_catalog.pg_class p
                    JOIN pg_catalog.pg_namespace pn ON pn.oid=p.relnamespace
                    WHERE pn.nspname='connectors' AND p.relname='owntracks_points'
                      AND p.relkind='r')) THEN
                RAISE EXCEPTION 'Native filtered-copy installed identity differs';
              END IF;
              IF (SELECT pg_catalog.array_agg(
                    a.attname||':'||pg_catalog.format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull
                    ORDER BY a.attname)
                  FROM pg_catalog.pg_attribute a
                  WHERE a.attrelid='{relation}'::pg_catalog.regclass
                    AND a.attnum>0 AND NOT a.attisdropped)
                  IS DISTINCT FROM ARRAY[{columns}]::text[] THEN
                RAISE EXCEPTION 'Native filtered-copy installed columns differ';
              END IF;
              IF (SELECT count(*) FROM pg_catalog.pg_constraint
                    WHERE conrelid='{relation}'::pg_catalog.regclass) <> {expected_constraints}
                  OR {constraint_test} THEN
                RAISE EXCEPTION 'Native filtered-copy installed constraints differ';
              END IF;
              IF EXISTS(SELECT 1 FROM pg_catalog.pg_policy
                  WHERE polrelid='{relation}'::pg_catalog.regclass
                    AND polname NOT IN ('native_copy_read','native_copy_write')) THEN
                RAISE EXCEPTION 'Native filtered-copy installed policy differs';
              END IF;
            END $native_copy_owner$;
            ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {relation} FORCE ROW LEVEL SECURITY;
            DROP POLICY IF EXISTS native_copy_read ON {relation};
            CREATE POLICY native_copy_read ON {relation} FOR SELECT USING ({readers});
            DROP POLICY IF EXISTS native_copy_write ON {relation};
            CREATE POLICY native_copy_write ON {relation} FOR ALL
              USING (current_user='connector_writer')
              WITH CHECK (current_user='connector_writer');
            DROP TRIGGER IF EXISTS preserve_retention_history ON {relation};
            CREATE TRIGGER preserve_retention_history BEFORE UPDATE OR DELETE ON {relation}
              FOR EACH ROW EXECUTE FUNCTION connectors.preserve_owntracks_retention_history();
            DO $native_copy_acl$
            BEGIN
              IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='connector_writer') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON {relation} TO connector_writer;
              END IF;
            END $native_copy_acl$;
            """
        )
        if table.endswith(("batches", "members")):
            statements.append(
                f"""
                DO $native_copy_observer$
                BEGIN
                  IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles
                      WHERE rolname='butler_chronicler_rw') THEN
                    GRANT SELECT ON {relation} TO butler_chronicler_rw;
                  END IF;
                END $native_copy_observer$;
                """
            )
    statements.append("""
        CREATE OR REPLACE FUNCTION connectors.preserve_native_filtered_reduction()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF OLD.connector_type='owntracks' AND EXISTS(
            SELECT 1 FROM connectors.owntracks_filtered_copy_births b
            JOIN connectors.owntracks_filtered_copy_members m USING(copy_generation)
            WHERE b.filtered_id=OLD.id AND b.filtered_received_at=OLD.received_at) THEN
            IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN
              RAISE EXCEPTION 'Native filtered-copy reductions are permanent';
            END IF;
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS preserve_native_filtered_reduction ON connectors.filtered_events;
        CREATE TRIGGER preserve_native_filtered_reduction BEFORE UPDATE OR DELETE
          ON connectors.filtered_events FOR EACH ROW
          EXECUTE FUNCTION connectors.preserve_native_filtered_reduction();
    """)
    return "\n".join(statements)
