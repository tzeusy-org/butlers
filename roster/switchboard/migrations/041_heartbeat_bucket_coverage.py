"""Protect receiver recording evidence without adding a heartbeat write.

Revision ID: sw_041
Revises: sw_040

Old history remains observational, with no coverage backfill. Ordinary legacy
registry writes remain accepted but cannot certify complete receiver recording.
The existing two writes acquire relation -> endpoint -> row locks and commit
atomically. Runtime TRIGGER privileges are deliberately unchanged: a closed
catalog check refuses interference rather than assuming those privileges away.
"""

from alembic import op

revision = "sw_041"
down_revision = "sw_040"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def upgrade() -> None:
    schema = _schema()
    home = _ident(schema)
    literal = "'" + schema.replace("'", "''") + "'"
    op.execute(f"ALTER TABLE {home}.connector_registry ADD COLUMN heartbeat_history_coverage jsonb")
    op.execute(f"ALTER TABLE {home}.connector_heartbeat_log ADD COLUMN recording_xid xid8")
    # An invoker-only inspection/locking function: it neither writes nor mints
    # coverage. All names are migration-bound, including under direct child SQL.
    op.execute(f"""
    CREATE FUNCTION {home}.heartbeat_recording_catalog() RETURNS jsonb
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    DECLARE
        parent_oid oid := '{home}.connector_heartbeat_log'::regclass;
        registry_oid oid := '{home}.connector_registry'::regclass;
        trusted_owner oid;
        relation record;
        expected record;
        actual record;
        parent_trigger oid;
        boundary text[];
        receipts jsonb := '[]'::jsonb;
    BEGIN
        IF current_setting('session_replication_role') <> 'origin' THEN
            RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
        END IF;
        -- ROW EXCLUSIVE conflicts with trigger DDL (SHARE ROW EXCLUSIVE),
        -- but permits independent endpoint writers. Never take a fleet mutex.
        LOCK TABLE {home}.connector_registry, {home}.connector_heartbeat_log
            IN ROW EXCLUSIVE MODE;
        FOR relation IN
            SELECT c.oid, n.nspname, c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.inhparent = parent_oid ORDER BY c.oid
        LOOP
            EXECUTE format('LOCK TABLE %I.%I IN ROW EXCLUSIVE MODE',
                           relation.nspname, relation.relname);
        END LOOP;
        SELECT relowner INTO trusted_owner FROM pg_class WHERE oid = parent_oid;
        IF EXISTS (
            SELECT 1 FROM pg_class WHERE oid = registry_oid AND relowner <> trusted_owner
        ) OR EXISTS (
            SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = parent_oid AND
                (c.relowner <> trusted_owner OR c.relkind <> 'r' OR NOT c.relispartition)
        ) THEN
            RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
        END IF;
        FOR expected IN
            SELECT * FROM (VALUES
                ('heartbeat_recording_catalog', 'jsonb'::regtype),
                ('derive_heartbeat_recording_coverage', 'jsonb'::regtype),
                ('stamp_heartbeat_history_row', 'trigger'::regtype),
                ('validate_heartbeat_history_row', 'trigger'::regtype),
                ('derive_registry_heartbeat_coverage', 'trigger'::regtype),
                ('validate_registry_heartbeat_coverage', 'trigger'::regtype)
            ) AS e(name, result_type)
        LOOP
            SELECT p.* INTO actual FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = {literal} AND p.proname = expected.name
               AND p.prorettype = expected.result_type
               AND p.pronargs = CASE WHEN expected.name = 'derive_heartbeat_recording_coverage'
                                    THEN 2 ELSE 0 END;
            IF NOT FOUND OR actual.proowner <> trusted_owner OR actual.prosecdef
               OR actual.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, pg_temp'] THEN
                RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
            END IF;
        END LOOP;
        FOR relation IN
            SELECT oid FROM pg_class WHERE oid IN (parent_oid, registry_oid)
            UNION ALL SELECT inhrelid FROM pg_inherits WHERE inhparent = parent_oid
        LOOP
            IF EXISTS (SELECT 1 FROM pg_rewrite WHERE ev_class = relation.oid)
               OR (SELECT count(*) FROM pg_trigger WHERE tgrelid = relation.oid) <> 2 THEN
                RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
            END IF;
            FOR expected IN
                SELECT * FROM (VALUES
                    ('heartbeat_history_stamp', 'stamp_heartbeat_history_row', 31),
                    ('heartbeat_history_validate', 'validate_heartbeat_history_row', 29),
                    ('heartbeat_registry_derive', 'derive_registry_heartbeat_coverage', 23),
                    ('heartbeat_registry_validate', 'validate_registry_heartbeat_coverage', 21)
                ) AS e(trigger_name, function_name, type)
                WHERE (relation.oid = registry_oid) = (type IN (23, 21))
            LOOP
                SELECT t.*, p.proowner, p.proname, n.nspname INTO actual
                FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE t.tgrelid = relation.oid AND t.tgname = expected.trigger_name;
                SELECT oid INTO parent_trigger FROM pg_trigger
                WHERE tgrelid = parent_oid AND tgname = expected.trigger_name;
                IF actual.oid IS NULL OR actual.tgtype <> expected.type OR actual.tgenabled <> 'O'
                   OR actual.tgisinternal OR actual.tgdeferrable OR actual.tginitdeferred
                   OR actual.tgconstraint <> 0 OR actual.tgnargs <> 0 OR actual.tgqual IS NOT NULL
                   OR actual.proowner <> trusted_owner OR actual.proname <> expected.function_name
                   OR actual.nspname <> {literal}
                   OR actual.tgparentid <> (CASE WHEN relation.oid IN (parent_oid, registry_oid)
                                                THEN 0::oid ELSE parent_trigger END) THEN
                    RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
                END IF;
            END LOOP;
        END LOOP;
        FOR relation IN
            SELECT c.oid, pg_get_expr(c.relpartbound, c.oid) AS bound
            FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = parent_oid ORDER BY c.oid
        LOOP
            boundary := regexp_match(relation.bound,
                '^FOR VALUES FROM \(''([^'']+)''\) TO \(''([^'']+)''\)$');
            IF boundary IS NULL THEN
                RAISE EXCEPTION 'heartbeat recording catalog unavailable' USING ERRCODE = '55000';
            END IF;
            IF boundary[2]::timestamptz > clock_timestamp() - interval '7 days'
               AND boundary[1]::timestamptz <
                   (date_trunc('month', clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                       + interval '2 months' THEN
                receipts := receipts || jsonb_build_array(jsonb_build_object(
                    'oid', relation.oid, 'range_start', boundary[1]::timestamptz,
                    'range_end', boundary[2]::timestamptz));
            END IF;
        END LOOP;
        RETURN receipts;
    END;
    $fn$;
    """)
    op.execute(f"""
    CREATE FUNCTION {home}.derive_heartbeat_recording_coverage(
        candidate {home}.connector_registry, previous_coverage jsonb
    ) RETURNS jsonb LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    DECLARE
        receipts jsonb := {home}.heartbeat_recording_catalog();
        receipt record;
        start_at timestamptz;
        prior jsonb;
    BEGIN
        SELECT h.received_at INTO receipt FROM {home}.connector_heartbeat_log h
        WHERE h.connector_type = candidate.connector_type
          AND h.endpoint_identity = candidate.endpoint_identity
          AND h.instance_id IS NOT DISTINCT FROM candidate.instance_id
          AND h.recording_xid = pg_current_xact_id()
          AND h.received_at = candidate.last_heartbeat_at
          AND ROW(h.state, h.error_message, h.uptime_s,
                  h.counter_messages_ingested, h.counter_messages_failed,
                  h.counter_source_api_calls, h.counter_checkpoint_saves,
                  h.counter_dedupe_accepted) IS NOT DISTINCT FROM
              ROW(candidate.state, candidate.error_message, candidate.uptime_s,
                  candidate.counter_messages_ingested, candidate.counter_messages_failed,
                  candidate.counter_source_api_calls, candidate.counter_checkpoint_saves,
                  candidate.counter_dedupe_accepted)
          AND candidate.operational_role = 'runtime_instance';
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF EXISTS (SELECT 1 FROM {home}.connector_heartbeat_log h
            WHERE h.connector_type = candidate.connector_type
              AND h.endpoint_identity = candidate.endpoint_identity
              AND h.recording_xid IS NOT NULL AND h.received_at > receipt.received_at) THEN
            RETURN NULL;
        END IF;
        start_at := receipt.received_at;
        IF previous_coverage->>'version' = '1' THEN
            BEGIN
                IF (previous_coverage->>'coverage_start')::timestamptz <= start_at THEN
                    start_at := (previous_coverage->>'coverage_start')::timestamptz;
                    FOR prior IN SELECT value FROM jsonb_array_elements(previous_coverage->'partitions')
                    LOOP
                        IF (prior->>'range_end')::timestamptz > receipt.received_at - interval '7 days'
                           AND NOT receipts @> jsonb_build_array(prior) THEN
                            start_at := receipt.received_at;
                        END IF;
                    END LOOP;
                END IF;
            EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
                start_at := receipt.received_at;
            END;
        END IF;
        RETURN jsonb_build_object('version', 1, 'coverage_start', start_at, 'partitions', receipts);
    END;
    $fn$;
    """)
    op.execute(f"""
    CREATE FUNCTION {home}.stamp_heartbeat_history_row() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    DECLARE reversed_key bigint;
    BEGIN
        PERFORM {home}.heartbeat_recording_catalog();
        IF (TG_RELID <> '{home}.connector_heartbeat_log'::regclass
            AND NOT EXISTS (SELECT 1 FROM pg_inherits
                WHERE inhparent = '{home}.connector_heartbeat_log'::regclass
                  AND inhrelid = TG_RELID))
           OR TG_WHEN <> 'BEFORE' OR TG_LEVEL <> 'ROW' OR TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'heartbeat history is append only' USING ERRCODE = '42501';
        END IF;
        -- An unpaired registry mutation earlier in THIS transaction leaves a
        -- negative-only transaction lock. Refuse before taking the endpoint
        -- lock: the reverse path already owns its registry row. Session unlock
        -- cannot erase a transaction lock, and caller locks only cause refusal.
        reversed_key := hashtextextended(
            'heartbeat-registry-first:' || NEW.connector_type || ':' || NEW.endpoint_identity, 0);
        IF EXISTS (SELECT 1 FROM pg_locks
            WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
              AND mode = 'ExclusiveLock' AND objsubid = 1
              AND ((classid::bigint << 32) | objid::bigint) = reversed_key) THEN
            RAISE EXCEPTION 'heartbeat paired write order invalid' USING ERRCODE = '55000';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'connector-classification:' || NEW.connector_type || ':' || NEW.endpoint_identity, 0));
        PERFORM 1 FROM {home}.connector_registry
        WHERE connector_type = NEW.connector_type AND endpoint_identity = NEW.endpoint_identity
        FOR UPDATE;
        NEW.received_at := clock_timestamp();
        NEW.recording_xid := pg_current_xact_id();
        RETURN NEW;
    END;
    $fn$;
    CREATE FUNCTION {home}.validate_heartbeat_history_row() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    BEGIN
        PERFORM {home}.heartbeat_recording_catalog();
        IF (TG_RELID <> '{home}.connector_heartbeat_log'::regclass
            AND NOT EXISTS (SELECT 1 FROM pg_inherits
                WHERE inhparent = '{home}.connector_heartbeat_log'::regclass
                  AND inhrelid = TG_RELID))
           OR TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'ROW' OR TG_OP <> 'INSERT'
           OR NEW.recording_xid IS DISTINCT FROM pg_current_xact_id()
           OR NEW.received_at > clock_timestamp() THEN
            RAISE EXCEPTION 'heartbeat history receipt invalid' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END;
    $fn$;
    CREATE FUNCTION {home}.derive_registry_heartbeat_coverage() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    DECLARE prior jsonb;
    BEGIN
        PERFORM {home}.heartbeat_recording_catalog();
        IF TG_RELID <> '{home}.connector_registry'::regclass
           OR TG_WHEN <> 'BEFORE' OR TG_LEVEL <> 'ROW' OR TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'heartbeat registry context invalid' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' THEN
            prior := OLD.heartbeat_history_coverage;
            IF ROW(NEW.connector_type, NEW.endpoint_identity, NEW.instance_id,
                   NEW.last_heartbeat_at, NEW.state, NEW.error_message, NEW.uptime_s,
                   NEW.counter_messages_ingested, NEW.counter_messages_failed,
                   NEW.counter_source_api_calls, NEW.counter_checkpoint_saves,
                   NEW.counter_dedupe_accepted, NEW.operational_role) IS NOT DISTINCT FROM
               ROW(OLD.connector_type, OLD.endpoint_identity, OLD.instance_id,
                   OLD.last_heartbeat_at, OLD.state, OLD.error_message, OLD.uptime_s,
                   OLD.counter_messages_ingested, OLD.counter_messages_failed,
                   OLD.counter_source_api_calls, OLD.counter_checkpoint_saves,
                   OLD.counter_dedupe_accepted, OLD.operational_role) THEN
                NEW.heartbeat_history_coverage := prior;
                RETURN NEW;
            END IF;
            IF NEW.instance_id IS DISTINCT FROM OLD.instance_id THEN prior := NULL; END IF;
        END IF;
        -- Registry-only legacy callers never wait for the endpoint after
        -- acquiring their row. A distinct, nonblocking transaction lock only
        -- remembers an unpaired heartbeat mutation for later append refusal;
        -- it cannot mint coverage, and adds no persistent write.
        NEW.heartbeat_history_coverage :=
            {home}.derive_heartbeat_recording_coverage(NEW, prior);
        IF NEW.heartbeat_history_coverage IS NULL AND NEW.last_heartbeat_at IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                'heartbeat-registry-first:' || NEW.connector_type || ':' || NEW.endpoint_identity,
                0)) THEN
                RAISE EXCEPTION 'heartbeat paired write order unavailable' USING ERRCODE = '55000';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $fn$;
    CREATE FUNCTION {home}.validate_registry_heartbeat_coverage() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
    DECLARE expected jsonb;
    BEGIN
        PERFORM {home}.heartbeat_recording_catalog();
        IF TG_RELID <> '{home}.connector_registry'::regclass
           OR TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'ROW' OR TG_OP NOT IN ('INSERT', 'UPDATE') THEN
            RAISE EXCEPTION 'heartbeat registry context invalid' USING ERRCODE = '55000';
        END IF;
        IF NEW.heartbeat_history_coverage IS NOT NULL THEN
            expected := {home}.derive_heartbeat_recording_coverage(
                NEW, NEW.heartbeat_history_coverage);
            -- Settings/cursor updates preserve, but never mint, an existing
            -- marker. A fresh heartbeat must have its same-transaction row.
            IF expected IS NULL AND TG_OP = 'UPDATE'
               AND NEW.heartbeat_history_coverage = OLD.heartbeat_history_coverage
               AND NEW.last_heartbeat_at IS NOT DISTINCT FROM OLD.last_heartbeat_at
               AND NEW.instance_id IS NOT DISTINCT FROM OLD.instance_id THEN
                RETURN NEW;
            END IF;
            IF NEW.heartbeat_history_coverage IS DISTINCT FROM expected THEN
                RAISE EXCEPTION 'heartbeat registry receipt invalid' USING ERRCODE = '55000';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $fn$;
    """)
    for table, name, timing, operations, function in (
        (
            "connector_heartbeat_log",
            "heartbeat_history_stamp",
            "BEFORE",
            "INSERT OR UPDATE OR DELETE",
            "stamp_heartbeat_history_row",
        ),
        (
            "connector_heartbeat_log",
            "heartbeat_history_validate",
            "AFTER",
            "INSERT OR UPDATE OR DELETE",
            "validate_heartbeat_history_row",
        ),
        (
            "connector_registry",
            "heartbeat_registry_derive",
            "BEFORE",
            "INSERT OR UPDATE",
            "derive_registry_heartbeat_coverage",
        ),
        (
            "connector_registry",
            "heartbeat_registry_validate",
            "AFTER",
            "INSERT OR UPDATE",
            "validate_registry_heartbeat_coverage",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {name} {timing} {operations} ON {home}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {home}.{function}()"
        )


def downgrade() -> None:
    home = _ident(_schema())
    # No data deletion or invented historical certification on either direction.
    for table, names in (
        ("connector_heartbeat_log", ("heartbeat_history_stamp", "heartbeat_history_validate")),
        ("connector_registry", ("heartbeat_registry_derive", "heartbeat_registry_validate")),
    ):
        for name in names:
            op.execute(f"DROP TRIGGER {name} ON {home}.{table}")
    for name in (
        "stamp_heartbeat_history_row",
        "validate_heartbeat_history_row",
        "derive_registry_heartbeat_coverage",
        "validate_registry_heartbeat_coverage",
    ):
        op.execute(f"DROP FUNCTION {home}.{name}()")
    op.execute(
        f"DROP FUNCTION {home}.derive_heartbeat_recording_coverage({home}.connector_registry, jsonb)"
    )
    op.execute(f"DROP FUNCTION {home}.heartbeat_recording_catalog()")
    op.execute(f"ALTER TABLE {home}.connector_registry DROP COLUMN heartbeat_history_coverage")
    op.execute(f"ALTER TABLE {home}.connector_heartbeat_log DROP COLUMN recording_xid")
