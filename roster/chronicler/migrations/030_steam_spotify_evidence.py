"""Route Steam and Spotify source history through situational fusion (bu-r3fmja.7).

Revision ID: chronicler_030
Revises: chronicler_029

Preserve row identities, provenance, corrections and tombstones; change only the
base layer. Daily Steam totals remain fallback evidence. Spotify stays useful as
a quiet-run breaker but never counts as Play. Downgrade preserves evidence:
restoring automatic counted rows would double-count existing derived blocks.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_030"
down_revision = "chronicler_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runtime and migration share this same-database, transaction-scoped key.
    op.execute("SELECT pg_advisory_xact_lock(1128813128, 5471)")
    # This migration changes source-derived facts in the same transaction as
    # the historical relayering. An absent canonical table is not evidence that
    # no fact or projection was ever published, so fail before any DDL/data
    # mutation until the owned memory chain has been prepared.
    op.execute("""
        DO $preflight$
        DECLARE
            runtime_role CONSTANT TEXT := 'butler_chronicler_rw';
            required_column RECORD;
            invalidator OID;
        BEGIN
            IF to_regclass('chronicler_mem.facts') IS NULL THEN
                RAISE EXCEPTION 'required canonical storage chronicler_mem.facts is missing; '
                    'run butlers db prepare-chronicler-memory before chronicler migrations'
                    USING ERRCODE = '55000';
            END IF;
            IF to_regclass('public.memory_catalog') IS NULL
               OR to_regclass('public.entity_graph_edges') IS NULL
            THEN
                RAISE EXCEPTION 'required public memory projection tables are missing'
                    USING ERRCODE = '55000';
            END IF;
            invalidator := to_regprocedure(
                'public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)'
            );
            IF invalidator IS NULL THEN
                RAISE EXCEPTION 'required memory projection invalidation function is missing'
                    USING ERRCODE = '55000';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'chronicler_mem'
                  AND relation.relname = 'facts'
                  AND relation.relkind = 'r'
                  AND relation.relpersistence = 'p'
                  AND relation.relowner = namespace.nspowner
            ) THEN
                RAISE EXCEPTION 'canonical Chronicler facts must be the owned private table'
                    USING ERRCODE = '55000';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname = 'memory_catalog'
                  AND relation.relkind = 'r'
                  AND relation.relpersistence = 'p'
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname = 'entity_graph_edges'
                  AND relation.relkind = 'r'
                  AND relation.relpersistence = 'p'
            ) THEN
                RAISE EXCEPTION 'memory projections must be persistent tables'
                    USING ERRCODE = '55000';
            END IF;

            FOR required_column IN
                SELECT required.schema_name, required.table_name,
                       required.column_name, required.type_name
                FROM (VALUES
                    ('chronicler_mem', 'facts', 'id', 'uuid'),
                    ('chronicler_mem', 'facts', 'subject', 'text'),
                    ('chronicler_mem', 'facts', 'predicate', 'text'),
                    ('chronicler_mem', 'facts', 'content', 'text'),
                    ('chronicler_mem', 'facts', 'embedding', 'vector'),
                    ('chronicler_mem', 'facts', 'search_vector', 'tsvector'),
                    ('chronicler_mem', 'facts', 'confidence', 'float8'),
                    ('chronicler_mem', 'facts', 'source_butler', 'text'),
                    ('chronicler_mem', 'facts', 'content_authority', 'text'),
                    ('chronicler_mem', 'facts', 'validity', 'text'),
                    ('chronicler_mem', 'facts', 'tags', 'jsonb'),
                    ('chronicler_mem', 'facts', 'metadata', 'jsonb'),
                    ('chronicler_mem', 'facts', 'invalid_at', 'timestamptz'),
                    ('public', 'memory_catalog', 'source_schema', 'text'),
                    ('public', 'memory_catalog', 'source_table', 'text'),
                    ('public', 'memory_catalog', 'source_id', 'uuid'),
                    ('public', 'memory_catalog', 'summary', 'text'),
                    ('public', 'memory_catalog', 'title', 'text'),
                    ('public', 'memory_catalog', 'embedding', 'vector'),
                    ('public', 'memory_catalog', 'search_vector', 'tsvector'),
                    ('public', 'memory_catalog', 'confidence', 'float8'),
                    ('public', 'memory_catalog', 'invalid_at', 'timestamptz'),
                    ('public', 'memory_catalog', 'updated_at', 'timestamptz'),
                    ('public', 'entity_graph_edges', 'source_schema', 'text'),
                    ('public', 'entity_graph_edges', 'source_table', 'text'),
                    ('public', 'entity_graph_edges', 'source_id', 'uuid')
                ) AS required(schema_name, table_name, column_name, type_name)
                LEFT JOIN pg_catalog.pg_attribute AS attribute
                  ON attribute.attrelid = to_regclass(
                      format('%I.%I', required.schema_name, required.table_name)
                  )
                 AND attribute.attname = required.column_name
                 AND attribute.attnum > 0
                 AND NOT attribute.attisdropped
                LEFT JOIN pg_catalog.pg_type AS column_type
                  ON column_type.oid = attribute.atttypid
                WHERE attribute.attname IS NULL
                   OR column_type.typname <> required.type_name
            LOOP
                RAISE EXCEPTION 'memory canonical/projection shape mismatch at %.%.%',
                    required_column.schema_name, required_column.table_name,
                    required_column.column_name USING ERRCODE = '55000';
            END LOOP;

            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles AS role
                WHERE role.rolname = runtime_role
                  AND NOT role.rolsuper AND NOT role.rolcreaterole
                  AND NOT role.rolcreatedb AND NOT role.rolreplication
                  AND NOT role.rolbypassrls
            ) THEN
                RAISE EXCEPTION 'required normal Chronicler runtime role is missing'
                    USING ERRCODE = '55000';
            END IF;
            IF NOT has_schema_privilege(runtime_role, 'chronicler_mem', 'USAGE')
               OR NOT has_table_privilege(runtime_role, 'chronicler_mem.facts', 'SELECT')
               OR NOT has_table_privilege(runtime_role, 'chronicler_mem.facts', 'UPDATE')
               OR NOT has_schema_privilege(runtime_role, 'public', 'USAGE')
               OR NOT has_table_privilege(runtime_role, 'public.memory_catalog', 'SELECT')
               OR NOT has_table_privilege(runtime_role, 'public.memory_catalog', 'UPDATE')
               OR NOT has_table_privilege(runtime_role, 'public.entity_graph_edges', 'SELECT')
               OR NOT has_table_privilege(runtime_role, 'public.entity_graph_edges', 'DELETE')
               OR NOT has_function_privilege(runtime_role, invalidator, 'EXECUTE')
            THEN
                RAISE EXCEPTION 'Chronicler runtime memory projection privileges are incomplete'
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc AS function
                WHERE function.oid = invalidator
                  AND NOT function.prosecdef
                  AND function.prorettype = 'void'::regtype
            ) THEN
                RAISE EXCEPTION 'memory projection invalidation must remain SECURITY INVOKER'
                    USING ERRCODE = '55000';
            END IF;
        END
        $preflight$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_repair_control (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            replay_epoch BIGINT NOT NULL DEFAULT 1,
            replay_ack BIGINT NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL,
            closed_through DATE NOT NULL,
            blocked_reason TEXT
        );
        INSERT INTO daily_repair_control (timezone, closed_through, blocked_reason)
        SELECT coalesce(min(timezone), 'Asia/Singapore'),
               (now() AT TIME ZONE coalesce(min(timezone), 'Asia/Singapore'))::date - 1,
               CASE WHEN count(DISTINCT timezone) > 1 THEN 'mixed rollup timezones' END
        FROM daily_rollups
        ON CONFLICT (singleton) DO NOTHING;
        CREATE TABLE IF NOT EXISTS daily_repair_days (
            local_date DATE PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            timezone TEXT NOT NULL,
            dirty_generation BIGINT NOT NULL DEFAULT 1,
            built_generation BIGINT NOT NULL DEFAULT 0,
            totals_pending BOOLEAN NOT NULL DEFAULT TRUE,
            outlier_pending BOOLEAN NOT NULL DEFAULT TRUE,
            facts_pending BOOLEAN NOT NULL DEFAULT TRUE,
            published_revision BIGINT NOT NULL DEFAULT 0,
            historical_flags_status TEXT NOT NULL DEFAULT 'unknown',
            prose_status TEXT NOT NULL DEFAULT 'pending',
            availability_snapshot JSONB,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS daily_repair_archive (
            local_date DATE NOT NULL,
            generation BIGINT NOT NULL,
            row_kind TEXT NOT NULL,
            row_key TEXT NOT NULL,
            audit_hash TEXT,
            content JSONB,
            dependency_ids UUID[] NOT NULL DEFAULT '{}',
            text_access TEXT NOT NULL DEFAULT 'audit_only'
                CHECK (text_access IN ('audit_only', 'redacted')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (local_date, generation, row_kind, row_key)
        );
        REVOKE ALL ON daily_repair_archive FROM PUBLIC;
        CREATE TABLE IF NOT EXISTS daily_repair_fact_dispositions (
            local_date DATE NOT NULL,
            generation BIGINT NOT NULL,
            row_key TEXT NOT NULL,
            fact_id UUID,
            disposition TEXT NOT NULL
                CHECK (disposition IN ('disposed', 'kept', 'no_match')),
            reason_code TEXT NOT NULL
                CHECK (reason_code IN ('source_changed', 'source_delete', 'source_privacy',
                                       'source_tombstone', 'source_retention', 'upgrade')),
            dependency_ids UUID[] NOT NULL DEFAULT '{}',
            redacted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (local_date, generation, row_key),
            CHECK ((disposition = 'no_match' AND fact_id IS NULL AND row_key = 'no_match')
                OR (disposition <> 'no_match' AND fact_id IS NOT NULL
                    AND row_key = fact_id::text)),
            CHECK (NOT redacted OR disposition = 'disposed')
        );
        REVOKE ALL ON daily_repair_fact_dispositions FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE ON daily_repair_fact_dispositions
            TO butler_chronicler_rw;
        CREATE TABLE IF NOT EXISTS daily_repair_source_redactions (
            source_id UUID PRIMARY KEY,
            reason_code TEXT NOT NULL
                CHECK (reason_code IN ('source_changed', 'source_delete', 'source_privacy',
                                       'source_tombstone', 'source_retention', 'upgrade')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        REVOKE ALL ON daily_repair_source_redactions FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE ON daily_repair_source_redactions
            TO butler_chronicler_rw;
        CREATE TABLE IF NOT EXISTS daily_repair_retention_spans (
            source_id UUID NOT NULL REFERENCES daily_repair_source_redactions(source_id),
            span_number SMALLINT NOT NULL,
            first_date DATE NOT NULL,
            last_date DATE NOT NULL,
            cursor_date DATE NOT NULL,
            batch_days SMALLINT NOT NULL DEFAULT 7,
            timeout_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_id, span_number),
            CHECK (first_date <= cursor_date AND cursor_date <= last_date),
            CHECK (batch_days BETWEEN 1 AND 32),
            CHECK (timeout_count >= 0)
        );
        REVOKE ALL ON daily_repair_retention_spans FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE, DELETE ON daily_repair_retention_spans
            TO butler_chronicler_rw;
    """)
    op.execute("""
CREATE OR REPLACE FUNCTION chronicler.invalidate_chronicler_generated_facts(
    p_local_date DATE,
    p_generation BIGINT,
    p_dependency_ids UUID[],
    p_redact_text BOOLEAN,
    p_reason_code TEXT
) RETURNS VOID LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    fact RECORD;
    selected_ids UUID[] := '{}';
    redacted_ids UUID[] := '{}';
    normal_ids UUID[] := '{}';
    has_generated BOOLEAN := FALSE;
    preserve_fact BOOLEAN;
    disposition_value TEXT;
    redacted_value BOOLEAN;
    affected_rows BIGINT;
BEGIN
    IF to_regclass('chronicler_mem.facts') IS NULL THEN
        RAISE EXCEPTION 'required canonical storage chronicler_mem.facts is missing'
            USING ERRCODE = '55000';
    END IF;
    IF p_local_date IS NULL OR p_generation IS NULL OR p_dependency_ids IS NULL
       OR cardinality(p_dependency_ids) = 0
       OR array_position(p_dependency_ids, NULL::uuid) IS NOT NULL
       OR p_redact_text IS NULL OR p_reason_code IS NULL
       OR p_reason_code NOT IN ('source_changed', 'source_delete', 'source_privacy',
                                'source_tombstone', 'source_retention', 'upgrade')
       OR (p_redact_text AND cardinality(p_dependency_ids) = 0)
    THEN
        RAISE EXCEPTION 'invalid Chronicler fact-disposition scope' USING ERRCODE = '22023';
    END IF;

    FOR fact IN
        SELECT f.id, f.subject, f.predicate, f.content, f.embedding,
               f.search_vector, f.confidence, f.validity, f.invalid_at,
               f.entity_id, f.object_entity_id, f.source_episode_id,
               f.content_authority, f.metadata, f.tags,
               CASE
                   WHEN EXISTS (
                       SELECT 1 FROM chronicler.daily_repair_fact_dispositions AS prior
                       WHERE prior.fact_id = f.id
                         AND prior.disposition = 'disposed'
                         AND prior.redacted
                   ) THEN (
                       SELECT prior.local_date
                       FROM chronicler.daily_repair_fact_dispositions AS prior
                       WHERE prior.fact_id = f.id
                         AND prior.disposition = 'disposed'
                         AND prior.redacted
                       ORDER BY prior.generation DESC
                       LIMIT 1
                   )
                   WHEN f.predicate = 'sleep-debt'
                       THEN NULLIF(f.metadata->>'as_of', '')::date
                   ELSE NULLIF(f.metadata->>'local_date', '')::date
               END AS generated_date,
               EXISTS (
                   SELECT 1 FROM chronicler.daily_repair_fact_dispositions AS prior
                   WHERE prior.fact_id = f.id
                     AND prior.disposition = 'disposed'
                     AND prior.redacted
               ) AS already_redacted
        FROM chronicler_mem.facts AS f
        WHERE coalesce(f.content_authority, '') NOT IN ('owner', 'owner_device')
          AND (
              (f.source_butler = 'chronicler'
               AND f.predicate IN ('lane-skew', 'sleep-debt', 'revisit-low-confidence-block'))
              OR EXISTS (
                  SELECT 1 FROM chronicler.daily_repair_fact_dispositions AS prior
                  WHERE prior.fact_id = f.id
                    AND prior.disposition = 'disposed'
                    AND prior.redacted
              )
          )
        FOR UPDATE OF f
    LOOP
        IF fact.generated_date IS NULL THEN
            RAISE EXCEPTION 'ambiguous Chronicler generated fact identity requires review'
                USING ERRCODE = '22023';
        END IF;
        IF fact.generated_date <> p_local_date THEN
            CONTINUE;
        END IF;
        has_generated := TRUE;
        preserve_fact := FALSE;
        disposition_value := 'disposed';
        redacted_value := fact.already_redacted;

        IF fact.already_redacted THEN
            -- Durable inventory and canonical UUID are the authority after
            -- source metadata has been erased. Re-apply every privacy invariant
            -- even if an older writer recreated canonical text or vectors.
            UPDATE chronicler_mem.facts
            SET subject = 'chronicler redacted fact',
                content = '[removed by source privacy correction]',
                embedding = NULL,
                search_vector = NULL,
                confidence = 0,
                validity = 'retracted',
                invalid_at = coalesce(invalid_at, now()),
                tags = '[]'::jsonb,
                entity_id = NULL,
                object_entity_id = NULL,
                source_episode_id = NULL,
                metadata = jsonb_build_object(
                    'source', 'chronicler',
                    'repair_generation', p_generation,
                    'correction_id', p_dependency_ids[1],
                    'reason_code', p_reason_code,
                    'redacted', TRUE
                )
            WHERE id = fact.id;
            GET DIAGNOSTICS affected_rows = ROW_COUNT;
            IF affected_rows <> 1 THEN
                RAISE EXCEPTION 'canonical generated fact changed during redaction repair'
                    USING ERRCODE = '40001';
            END IF;
            -- Re-run projection cleanup even for an ordinary later edit; a
            -- stale catalog/graph copy must not restore text.
            selected_ids := array_append(selected_ids, fact.id);
            redacted_ids := array_append(redacted_ids, fact.id);
        ELSE
            IF fact.metadata->>'source' IS DISTINCT FROM 'chronicler'
               OR jsonb_typeof(fact.metadata) IS DISTINCT FROM 'object'
               OR jsonb_typeof(fact.tags) IS DISTINCT FROM 'array'
            THEN
                RAISE EXCEPTION 'ambiguous Chronicler generated fact identity requires review'
                    USING ERRCODE = '22023';
            END IF;

            IF fact.predicate = 'lane-skew' THEN
                IF fact.subject IS DISTINCT FROM (fact.metadata->>'lane' || ' lane')
                   OR coalesce(fact.metadata->>'lane', '') = ''
                   OR fact.metadata->>'direction' IS NULL
                   OR fact.metadata->>'direction' NOT IN ('up', 'down')
                   OR jsonb_typeof(fact.metadata->'seconds') IS DISTINCT FROM 'number'
                   OR NOT fact.tags @> jsonb_build_array(
                       'chronicler-insight', 'lane-skew', fact.metadata->>'lane'
                   )
                THEN
                    RAISE EXCEPTION 'ambiguous lane-skew fact identity requires review'
                        USING ERRCODE = '22023';
                END IF;
            ELSIF fact.predicate = 'sleep-debt' THEN
                IF fact.subject IS DISTINCT FROM 'sleep'
                   OR jsonb_typeof(fact.metadata->'debt_seconds') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(fact.metadata->'shortfall_days') IS DISTINCT FROM 'number'
                   OR jsonb_typeof(fact.metadata->'window_days') IS DISTINCT FROM 'number'
                   OR NOT fact.tags @> '["chronicler-insight", "sleep-debt"]'::jsonb
                THEN
                    RAISE EXCEPTION 'ambiguous sleep-debt fact identity requires review'
                        USING ERRCODE = '22023';
                END IF;
            ELSE
                IF fact.metadata->>'flag_type' IS NULL
                   OR fact.metadata->>'flag_type' NOT IN ('feeder_dark', 'sleep_missing')
                   OR fact.metadata->>'severity' IS NULL
                   OR fact.subject IS DISTINCT FROM (
                       (fact.metadata->>'local_date')
                       || ' ' || (fact.metadata->>'flag_type')
                   )
                   OR jsonb_typeof(fact.metadata->'detail') IS DISTINCT FROM 'object'
                   OR NOT fact.tags @> jsonb_build_array(
                       'chronicler-self-reminder', fact.metadata->>'flag_type'
                   )
                THEN
                    RAISE EXCEPTION 'ambiguous self-reminder fact identity requires review'
                        USING ERRCODE = '22023';
                END IF;
            END IF;

            IF NOT p_redact_text AND fact.predicate = 'revisit-low-confidence-block' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM chronicler.daily_rollup_flags AS flag
                    WHERE flag.local_date = p_local_date
                      AND flag.flag_type = fact.metadata->>'flag_type'
                      AND coalesce(flag.detail, '{}'::jsonb)
                          IS NOT DISTINCT FROM fact.metadata->'detail'
                      AND flag.severity IS NOT DISTINCT FROM fact.metadata->>'severity'
                ) INTO preserve_fact;
            END IF;

            IF p_redact_text THEN
                UPDATE chronicler_mem.facts
                SET subject = 'chronicler redacted fact',
                    content = '[removed by source privacy correction]',
                    embedding = NULL,
                    search_vector = NULL,
                    confidence = 0,
                    validity = 'retracted',
                    invalid_at = now(),
                    tags = '[]'::jsonb,
                    entity_id = NULL,
                    object_entity_id = NULL,
                    source_episode_id = NULL,
                    metadata = jsonb_build_object(
                        'source', 'chronicler',
                        'repair_generation', p_generation,
                        'correction_id', p_dependency_ids[1],
                        'reason_code', p_reason_code,
                        'redacted', TRUE
                    )
                WHERE id = fact.id;
                GET DIAGNOSTICS affected_rows = ROW_COUNT;
                IF affected_rows <> 1 THEN
                    RAISE EXCEPTION 'canonical generated fact changed during invalidation'
                        USING ERRCODE = '40001';
                END IF;
                selected_ids := array_append(selected_ids, fact.id);
                redacted_ids := array_append(redacted_ids, fact.id);
                redacted_value := TRUE;
            ELSIF preserve_fact THEN
                disposition_value := 'kept';
                IF fact.validity NOT IN ('active', 'fading') THEN
                    -- A kept historical claim can still have a stale projection.
                    selected_ids := array_append(selected_ids, fact.id);
                END IF;
            ELSE
                IF fact.validity IN ('active', 'fading') THEN
                    UPDATE chronicler_mem.facts
                    SET validity = 'retracted', confidence = 0,
                        invalid_at = coalesce(invalid_at, now())
                    WHERE id = fact.id AND validity IN ('active', 'fading');
                    GET DIAGNOSTICS affected_rows = ROW_COUNT;
                    IF affected_rows <> 1 THEN
                        RAISE EXCEPTION 'canonical generated fact changed during invalidation'
                            USING ERRCODE = '40001';
                    END IF;
                END IF;
                -- Already-terminal facts can still have a stale projection.
                selected_ids := array_append(selected_ids, fact.id);
            END IF;
        END IF;

        INSERT INTO chronicler.daily_repair_fact_dispositions
            (local_date, generation, row_key, fact_id, disposition, reason_code,
             dependency_ids, redacted)
        VALUES
            (p_local_date, p_generation, fact.id::text, fact.id, disposition_value,
             p_reason_code, p_dependency_ids, redacted_value)
        ON CONFLICT (local_date, generation, row_key) DO UPDATE SET
            disposition = EXCLUDED.disposition,
            reason_code = EXCLUDED.reason_code,
            dependency_ids = ARRAY(
                SELECT DISTINCT dependency_id
                FROM unnest(
                    chronicler.daily_repair_fact_dispositions.dependency_ids
                    || EXCLUDED.dependency_ids
                ) AS dependency(dependency_id)
                ORDER BY dependency_id
            ),
            redacted = chronicler.daily_repair_fact_dispositions.redacted OR EXCLUDED.redacted;
    END LOOP;

    -- Exact redaction inventory remains authoritative after the canonical row
    -- was physically purged. Clean only missing IDs whose prior source scope
    -- intersects this correction and whose recorded generated date is this day.
    FOR fact IN
        SELECT prior.fact_id AS id,
               array_agg(DISTINCT dependency.dependency_id
                         ORDER BY dependency.dependency_id) AS dependency_ids
        FROM chronicler.daily_repair_fact_dispositions AS prior
        CROSS JOIN LATERAL unnest(prior.dependency_ids) AS dependency(dependency_id)
        WHERE prior.local_date = p_local_date
          AND prior.disposition = 'disposed'
          AND prior.redacted
          AND prior.dependency_ids && p_dependency_ids
          AND NOT EXISTS (
              SELECT 1 FROM chronicler_mem.facts AS canonical
              WHERE canonical.id = prior.fact_id
          )
        GROUP BY prior.fact_id
        ORDER BY prior.fact_id
    LOOP
        has_generated := TRUE;
        selected_ids := array_append(selected_ids, fact.id);
        redacted_ids := array_append(redacted_ids, fact.id);
        INSERT INTO chronicler.daily_repair_fact_dispositions
            (local_date, generation, row_key, fact_id, disposition, reason_code,
             dependency_ids, redacted)
        VALUES
            (p_local_date, p_generation, fact.id::text, fact.id, 'disposed',
             p_reason_code,
             ARRAY(
                 SELECT DISTINCT dependency_id
                 FROM unnest(fact.dependency_ids || p_dependency_ids)
                      AS dependency(dependency_id)
                 ORDER BY dependency_id
             ),
             TRUE)
        ON CONFLICT (local_date, generation, row_key) DO UPDATE SET
            disposition = 'disposed', reason_code = EXCLUDED.reason_code,
            dependency_ids = ARRAY(
                SELECT DISTINCT dependency_id
                FROM unnest(
                    chronicler.daily_repair_fact_dispositions.dependency_ids
                    || EXCLUDED.dependency_ids
                ) AS dependency(dependency_id)
                ORDER BY dependency_id
            ),
            redacted = TRUE;
    END LOOP;

    IF NOT has_generated THEN
        INSERT INTO chronicler.daily_repair_fact_dispositions
            (local_date, generation, row_key, fact_id, disposition, reason_code,
             dependency_ids, redacted)
        VALUES
            (p_local_date, p_generation, 'no_match', NULL, 'no_match', p_reason_code,
             p_dependency_ids, FALSE)
        ON CONFLICT (local_date, generation, row_key) DO UPDATE SET
            reason_code = EXCLUDED.reason_code,
            dependency_ids = ARRAY(
                SELECT DISTINCT dependency_id
                FROM unnest(
                    chronicler.daily_repair_fact_dispositions.dependency_ids
                    || EXCLUDED.dependency_ids
                ) AS dependency(dependency_id)
                ORDER BY dependency_id
            );
    END IF;

    IF cardinality(redacted_ids) > 0 THEN
        PERFORM public.invalidate_memory_projection(
            'chronicler_mem', 'facts', redacted_ids, now(), TRUE, TRUE
        );
    END IF;
    normal_ids := ARRAY(
        SELECT fact_id FROM unnest(selected_ids) AS selected(fact_id)
        WHERE NOT fact_id = ANY(redacted_ids)
    );
    IF cardinality(normal_ids) > 0 THEN
        PERFORM public.invalidate_memory_projection(
            'chronicler_mem', 'facts', normal_ids, now(), TRUE, FALSE
        );
    END IF;
END
$$;
        REVOKE ALL ON FUNCTION chronicler.invalidate_chronicler_generated_facts(
            DATE, BIGINT, UUID[], BOOLEAN, TEXT
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION chronicler.invalidate_chronicler_generated_facts(
            DATE, BIGINT, UUID[], BOOLEAN, TEXT
        ) TO butler_chronicler_rw;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION chronicler.redact_retention_facts(
            p_source_id UUID
        ) RETURNS INTEGER
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE
            fact RECORD;
            ctl_timezone TEXT;
            active_day DATE;
            active_generation BIGINT;
            redacted_ids UUID[] := '{}'::uuid[];
            affected_rows BIGINT;
            fact_dependencies UUID[];
        BEGIN
            IF p_source_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM chronicler.daily_repair_source_redactions
                WHERE source_id = p_source_id AND reason_code = 'source_retention'
            ) THEN
                RAISE EXCEPTION 'invalid Chronicler retention redaction scope'
                    USING ERRCODE = '22023';
            END IF;
            SELECT timezone INTO STRICT ctl_timezone
            FROM chronicler.daily_repair_control WHERE singleton;

            FOR fact IN
                SELECT f.id, f.subject, f.predicate, f.content_authority,
                       f.metadata, f.tags,
                       CASE
                           WHEN prior.fact_id IS NOT NULL THEN prior.local_date
                           WHEN f.predicate = 'sleep-debt'
                               THEN NULLIF(f.metadata->>'as_of', '')::date
                           ELSE NULLIF(f.metadata->>'local_date', '')::date
                       END AS generated_date,
                       prior.fact_id IS NOT NULL AS already_redacted
                FROM chronicler_mem.facts AS f
                LEFT JOIN LATERAL (
                    SELECT disposition.fact_id, disposition.local_date
                    FROM chronicler.daily_repair_fact_dispositions AS disposition
                    WHERE disposition.fact_id = f.id
                      AND disposition.disposition = 'disposed' AND disposition.redacted
                    ORDER BY disposition.generation DESC LIMIT 1
                ) AS prior ON TRUE
                WHERE coalesce(f.content_authority, '') NOT IN ('owner', 'owner_device')
                  AND (
                      (f.source_butler = 'chronicler'
                       AND f.predicate IN ('lane-skew', 'sleep-debt',
                                           'revisit-low-confidence-block'))
                      OR prior.fact_id IS NOT NULL
                  )
                ORDER BY generated_date, f.id
                FOR UPDATE OF f
            LOOP
                IF fact.generated_date IS NULL THEN
                    RAISE EXCEPTION 'ambiguous Chronicler generated fact identity requires review'
                        USING ERRCODE = '22023';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM chronicler.daily_repair_retention_spans AS span
                    WHERE span.source_id = p_source_id
                      AND fact.generated_date BETWEEN span.first_date AND
                          least(span.last_date + 28,
                              (SELECT closed_through FROM chronicler.daily_repair_control
                               WHERE singleton))
                ) THEN
                    CONTINUE;
                END IF;

                IF NOT fact.already_redacted THEN
                    IF fact.metadata->>'source' IS DISTINCT FROM 'chronicler'
                       OR jsonb_typeof(fact.metadata) IS DISTINCT FROM 'object'
                       OR jsonb_typeof(fact.tags) IS DISTINCT FROM 'array'
                    THEN
                        RAISE EXCEPTION 'ambiguous Chronicler generated fact identity requires review'
                            USING ERRCODE = '22023';
                    END IF;
                    IF fact.predicate = 'lane-skew' THEN
                        IF fact.subject IS DISTINCT FROM (fact.metadata->>'lane' || ' lane')
                           OR coalesce(fact.metadata->>'lane', '') = ''
                           OR fact.metadata->>'direction' IS NULL
                           OR fact.metadata->>'direction' NOT IN ('up', 'down')
                           OR jsonb_typeof(fact.metadata->'seconds') IS DISTINCT FROM 'number'
                           OR NOT fact.tags @> jsonb_build_array(
                               'chronicler-insight', 'lane-skew', fact.metadata->>'lane'
                           )
                        THEN
                            RAISE EXCEPTION 'ambiguous lane-skew fact identity requires review'
                                USING ERRCODE = '22023';
                        END IF;
                    ELSIF fact.predicate = 'sleep-debt' THEN
                        IF fact.subject IS DISTINCT FROM 'sleep'
                           OR jsonb_typeof(fact.metadata->'debt_seconds') IS DISTINCT FROM 'number'
                           OR jsonb_typeof(fact.metadata->'shortfall_days') IS DISTINCT FROM 'number'
                           OR jsonb_typeof(fact.metadata->'window_days') IS DISTINCT FROM 'number'
                           OR NOT fact.tags @> '["chronicler-insight", "sleep-debt"]'::jsonb
                        THEN
                            RAISE EXCEPTION 'ambiguous sleep-debt fact identity requires review'
                                USING ERRCODE = '22023';
                        END IF;
                    ELSIF fact.predicate = 'revisit-low-confidence-block' THEN
                        IF fact.metadata->>'flag_type' IS NULL
                           OR fact.metadata->>'flag_type' NOT IN ('feeder_dark', 'sleep_missing')
                           OR fact.metadata->>'severity' IS NULL
                           OR fact.subject IS DISTINCT FROM (
                               (fact.metadata->>'local_date')
                               || ' ' || (fact.metadata->>'flag_type')
                           )
                           OR jsonb_typeof(fact.metadata->'detail') IS DISTINCT FROM 'object'
                           OR NOT fact.tags @> jsonb_build_array(
                               'chronicler-self-reminder', fact.metadata->>'flag_type'
                           )
                        THEN
                            RAISE EXCEPTION 'ambiguous self-reminder fact identity requires review'
                                USING ERRCODE = '22023';
                        END IF;
                    END IF;
                END IF;

                IF active_day IS DISTINCT FROM fact.generated_date THEN
                    INSERT INTO chronicler.daily_repair_days
                        (local_date, timezone, totals_pending, outlier_pending, facts_pending)
                    VALUES (fact.generated_date, ctl_timezone, FALSE, FALSE, TRUE)
                    ON CONFLICT (local_date) DO UPDATE SET
                        dirty_generation = chronicler.daily_repair_days.dirty_generation + 1,
                        facts_pending = TRUE, updated_at = now()
                    RETURNING dirty_generation INTO active_generation;
                    active_day := fact.generated_date;
                END IF;

                UPDATE chronicler_mem.facts
                SET subject = 'chronicler redacted fact',
                    content = '[removed by source privacy correction]',
                    embedding = NULL, search_vector = NULL, confidence = 0,
                    validity = 'retracted', invalid_at = coalesce(invalid_at, now()),
                    tags = '[]'::jsonb, entity_id = NULL, object_entity_id = NULL,
                    source_episode_id = NULL,
                    metadata = jsonb_build_object(
                        'source', 'chronicler', 'repair_generation', active_generation,
                        'correction_id', p_source_id, 'reason_code', 'source_retention',
                        'redacted', TRUE
                    )
                WHERE id = fact.id;
                GET DIAGNOSTICS affected_rows = ROW_COUNT;
                IF affected_rows <> 1 THEN
                    RAISE EXCEPTION 'canonical generated fact changed during retention redaction'
                        USING ERRCODE = '40001';
                END IF;

                SELECT coalesce(array_agg(DISTINCT dependency_id), ARRAY[p_source_id])
                INTO fact_dependencies
                FROM (
                    SELECT unnest(disposition.dependency_ids) AS dependency_id
                    FROM chronicler.daily_repair_fact_dispositions AS disposition
                    WHERE disposition.fact_id = fact.id
                    UNION
                    SELECT p_source_id
                ) AS prior_dependencies;
                INSERT INTO chronicler.daily_repair_fact_dispositions
                    (local_date, generation, row_key, fact_id, disposition, reason_code,
                     dependency_ids, redacted)
                VALUES
                    (fact.generated_date, active_generation, fact.id::text, fact.id,
                     'disposed', 'source_retention', fact_dependencies, TRUE)
                ON CONFLICT (local_date, generation, row_key) DO UPDATE SET
                    disposition = 'disposed', reason_code = 'source_retention',
                    dependency_ids = ARRAY(
                        SELECT DISTINCT dependency_id
                        FROM unnest(
                            chronicler.daily_repair_fact_dispositions.dependency_ids
                            || EXCLUDED.dependency_ids
                        ) AS dependency(dependency_id)
                        ORDER BY dependency_id
                    ),
                    redacted = TRUE;
                redacted_ids := array_append(redacted_ids, fact.id);
            END LOOP;

            -- Reapply privacy cleanup for prior redacted IDs whose canonical
            -- facts were purged. The source ID, durable date inventory, and
            -- bounded retention span must all match before touching projections.
            FOR fact IN
                WITH prior AS (
                    SELECT disposition.fact_id, disposition.local_date,
                           disposition.generation, disposition.dependency_ids
                    FROM chronicler.daily_repair_fact_dispositions AS disposition
                    WHERE disposition.disposition = 'disposed'
                      AND disposition.redacted
                      AND p_source_id = ANY(disposition.dependency_ids)
                ),
                latest AS (
                    SELECT DISTINCT ON (prior.fact_id)
                           prior.fact_id, prior.local_date
                    FROM prior
                    ORDER BY prior.fact_id, prior.generation DESC
                ),
                dependencies AS (
                    SELECT prior.fact_id,
                           array_agg(DISTINCT dependency.dependency_id
                                     ORDER BY dependency.dependency_id) AS dependency_ids
                    FROM prior
                    CROSS JOIN LATERAL unnest(prior.dependency_ids)
                        AS dependency(dependency_id)
                    GROUP BY prior.fact_id
                )
                SELECT latest.fact_id AS id, latest.local_date AS generated_date,
                       dependencies.dependency_ids
                FROM latest
                JOIN dependencies USING (fact_id)
                WHERE NOT EXISTS (
                    SELECT 1 FROM chronicler_mem.facts AS canonical
                    WHERE canonical.id = latest.fact_id
                )
                  AND EXISTS (
                    SELECT 1 FROM chronicler.daily_repair_retention_spans AS span
                    WHERE span.source_id = p_source_id
                      AND latest.local_date BETWEEN span.first_date AND
                          least(span.last_date + 28,
                              (SELECT closed_through FROM chronicler.daily_repair_control
                               WHERE singleton))
                  )
                ORDER BY latest.local_date, latest.fact_id
            LOOP
                IF active_day IS DISTINCT FROM fact.generated_date THEN
                    SELECT day.dirty_generation INTO active_generation
                    FROM chronicler.daily_repair_days AS day
                    WHERE day.local_date = fact.generated_date
                      AND day.facts_pending
                      AND EXISTS (
                          SELECT 1
                          FROM chronicler.daily_repair_fact_dispositions AS current
                          WHERE current.local_date = day.local_date
                            AND current.generation = day.dirty_generation
                            AND current.disposition = 'disposed'
                            AND current.redacted
                            AND current.reason_code = 'source_retention'
                            AND current.dependency_ids && ARRAY[p_source_id]
                      );
                    IF NOT FOUND THEN
                        INSERT INTO chronicler.daily_repair_days
                            (local_date, timezone, totals_pending, outlier_pending, facts_pending)
                        VALUES (fact.generated_date, ctl_timezone, FALSE, FALSE, TRUE)
                        ON CONFLICT (local_date) DO UPDATE SET
                            dirty_generation = chronicler.daily_repair_days.dirty_generation + 1,
                            facts_pending = TRUE, updated_at = now()
                        RETURNING dirty_generation INTO active_generation;
                    END IF;
                    active_day := fact.generated_date;
                END IF;

                INSERT INTO chronicler.daily_repair_fact_dispositions
                    (local_date, generation, row_key, fact_id, disposition, reason_code,
                     dependency_ids, redacted)
                VALUES
                    (fact.generated_date, active_generation, fact.id::text, fact.id,
                     'disposed', 'source_retention',
                     ARRAY(
                         SELECT DISTINCT dependency_id
                         FROM unnest(fact.dependency_ids || ARRAY[p_source_id])
                              AS dependency(dependency_id)
                         ORDER BY dependency_id
                     ),
                     TRUE)
                ON CONFLICT (local_date, generation, row_key) DO UPDATE SET
                    disposition = 'disposed', reason_code = 'source_retention',
                    dependency_ids = ARRAY(
                        SELECT DISTINCT dependency_id
                        FROM unnest(
                            chronicler.daily_repair_fact_dispositions.dependency_ids
                            || EXCLUDED.dependency_ids
                        ) AS dependency(dependency_id)
                        ORDER BY dependency_id
                    ),
                    redacted = TRUE;
                redacted_ids := array_append(redacted_ids, fact.id);
            END LOOP;

            IF cardinality(redacted_ids) > 0 THEN
                PERFORM public.invalidate_memory_projection(
                    'chronicler_mem', 'facts', redacted_ids, now(), TRUE, TRUE
                );
            END IF;
            RETURN cardinality(redacted_ids);
        END $$;
        REVOKE ALL ON FUNCTION chronicler.redact_retention_facts(UUID) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION chronicler.redact_retention_facts(UUID)
            TO butler_chronicler_rw;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enqueue_daily_repair(
            span_start TIMESTAMPTZ,
            span_end TIMESTAMPTZ,
            force_history BOOLEAN DEFAULT FALSE,
            p_dependency_ids UUID[] DEFAULT '{}'::uuid[],
            p_redact_text BOOLEAN DEFAULT FALSE,
            p_reason_code TEXT DEFAULT 'source_changed'
        ) RETURNS VOID LANGUAGE plpgsql SET search_path = chronicler, public AS $$
        DECLARE
            ctl daily_repair_control%ROWTYPE;
            d DATE;
            last_day DATE;
            target_day RECORD;
        BEGIN
            IF p_dependency_ids IS NULL OR cardinality(p_dependency_ids) = 0
               OR array_position(p_dependency_ids, NULL::uuid) IS NOT NULL
               OR p_redact_text IS NULL OR p_reason_code IS NULL
               OR p_reason_code NOT IN ('source_changed', 'source_delete', 'source_privacy',
                                        'source_tombstone', 'source_retention', 'upgrade')
            THEN
                RAISE EXCEPTION 'invalid daily repair dependency scope'
                    USING ERRCODE = '22023';
            END IF;
            IF span_start IS NULL AND NOT p_redact_text THEN RETURN; END IF;
            IF NOT pg_try_advisory_xact_lock(1128813128, 5471) THEN
                RAISE EXCEPTION 'daily repair busy' USING ERRCODE = '55P03';
            END IF;
            IF p_redact_text THEN
                INSERT INTO daily_repair_source_redactions (source_id, reason_code)
                SELECT DISTINCT dependency.source_id, p_reason_code
                FROM unnest(p_dependency_ids) AS dependency(source_id)
                ON CONFLICT (source_id) DO UPDATE SET reason_code = EXCLUDED.reason_code;
            END IF;
            IF span_start IS NULL THEN RETURN; END IF;
            SELECT * INTO STRICT ctl FROM daily_repair_control WHERE singleton;
            ctl.closed_through := greatest(ctl.closed_through,
                (now() AT TIME ZONE ctl.timezone)::date - 1);
            last_day := least(ctl.closed_through,
                ((coalesce(span_end, (ctl.closed_through + 1)::timestamp
                    AT TIME ZONE ctl.timezone) - interval '1 microsecond')
                    AT TIME ZONE ctl.timezone)::date);
            FOR d IN SELECT generate_series((span_start AT TIME ZONE ctl.timezone)::date,
                                            last_day, interval '1 day')::date LOOP
                -- Only a new uncached yesterday belongs to the ordinary first
                -- day-close (every allowed lookback includes it). Older uncached
                -- changes still queue durably; upgrade forces every affected date.
                IF NOT force_history AND d = ctl.closed_through
                    AND NOT EXISTS (SELECT 1 FROM daily_rollups WHERE local_date = d)
                    AND NOT EXISTS (SELECT 1 FROM daily_repair_days WHERE local_date = d
                                    AND (totals_pending OR built_generation > 0)) THEN
                    CONTINUE;
                END IF;
                INSERT INTO daily_repair_days (local_date, timezone)
                VALUES (d, ctl.timezone)
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    totals_pending = TRUE, outlier_pending = TRUE, facts_pending = TRUE,
                    prose_status = 'pending', updated_at = now();
                -- Only materialized dependent dates need flag reconstruction.
                INSERT INTO daily_repair_days
                    (local_date, timezone, totals_pending, outlier_pending)
                SELECT DISTINCT r.local_date, r.timezone, FALSE, TRUE FROM daily_rollups r
                WHERE r.local_date > d AND r.local_date <= d + 14
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    outlier_pending = TRUE, facts_pending = TRUE, updated_at = now();
                -- The 28-day memory horizon is distinct from the 14-day flags horizon.
                INSERT INTO daily_repair_days
                    (local_date, timezone, totals_pending, outlier_pending)
                SELECT x::date, ctl.timezone, FALSE, FALSE
                FROM generate_series(d + 1, least(d + 28, ctl.closed_through),
                                     interval '1 day') x
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    facts_pending = TRUE, updated_at = now();
                -- Canonical dispositions and projection invalidation share the
                -- source row transaction and exact resulting day generation.
                FOR target_day IN
                    SELECT local_date, dirty_generation
                    FROM daily_repair_days
                    WHERE local_date BETWEEN d AND least(d + 28, ctl.closed_through)
                    ORDER BY local_date
                LOOP
                    PERFORM chronicler.invalidate_chronicler_generated_facts(
                        target_day.local_date, target_day.dirty_generation,
                        p_dependency_ids, p_redact_text, p_reason_code
                    );
                END LOOP;
            END LOOP;
        END $$;
        REVOKE ALL ON FUNCTION chronicler.enqueue_daily_repair(
            TIMESTAMPTZ, TIMESTAMPTZ, BOOLEAN, UUID[], BOOLEAN, TEXT
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION chronicler.enqueue_daily_repair(
            TIMESTAMPTZ, TIMESTAMPTZ, BOOLEAN, UUID[], BOOLEAN, TEXT
        ) TO butler_chronicler_rw;
    """)
    # Capture old/current intervals and derived provenance before re-layering.
    op.execute("""
        SELECT enqueue_daily_repair(
            start_at, end_at, TRUE, ARRAY[id],
            coalesce(privacy = 'restricted', FALSE) OR tombstone_at IS NOT NULL
                OR (retention_days IS NOT NULL
                    AND start_at + make_interval(days => retention_days) < now()),
            CASE WHEN tombstone_at IS NOT NULL THEN 'source_tombstone'
                 WHEN privacy = 'restricted' THEN 'source_privacy'
                 WHEN retention_days IS NOT NULL
                      AND start_at + make_interval(days => retention_days) < now()
                     THEN 'source_retention'
                 ELSE 'upgrade' END
        ) FROM v_episodes_corrected
        WHERE source_name IN ('steam.play_history', 'spotify.session_summary',
                              'steam.status_history');
        SELECT enqueue_daily_repair(
            canonical_start_at, canonical_end_at, TRUE, ARRAY[id],
            coalesce(privacy = 'restricted', FALSE) OR tombstone_at IS NOT NULL
                OR (retention_days IS NOT NULL
                    AND canonical_start_at + make_interval(days => retention_days) < now()),
            CASE WHEN tombstone_at IS NOT NULL THEN 'source_tombstone'
                 WHEN privacy = 'restricted' THEN 'source_privacy'
                 WHEN retention_days IS NOT NULL
                      AND canonical_start_at + make_interval(days => retention_days) < now()
                     THEN 'source_retention'
                 ELSE 'upgrade' END
        ) FROM v_episodes_corrected
        WHERE source_name IN ('steam.play_history', 'spotify.session_summary',
                              'steam.status_history');
        SELECT enqueue_daily_repair(
            derived.start_at, derived.end_at, TRUE, ARRAY[derived.id],
            coalesce(derived.privacy = 'restricted', FALSE)
                OR derived.tombstone_at IS NOT NULL
                OR (derived.retention_days IS NOT NULL
                    AND derived.start_at + make_interval(days => derived.retention_days) < now()),
            CASE WHEN derived.tombstone_at IS NOT NULL THEN 'source_tombstone'
                 WHEN derived.privacy = 'restricted' THEN 'source_privacy'
                 WHEN derived.retention_days IS NOT NULL
                      AND derived.start_at + make_interval(days => derived.retention_days) < now()
                     THEN 'source_retention'
                 ELSE 'upgrade' END
        ) FROM episodes derived
        WHERE derived.source_name = 'chronicler.situation_inferred'
          AND EXISTS (SELECT 1 FROM episodes evidence
                      WHERE evidence.source_name IN ('steam.play_history',
                          'spotify.session_summary', 'steam.status_history')
                        AND derived.evidence_refs ? evidence.id::text);
    """)
    op.execute("""
        UPDATE episodes SET layer = 'evidence', updated_at = now()
        WHERE (source_name = 'steam.play_history' AND episode_type = 'play_episode')
           OR (source_name = 'spotify.session_summary' AND episode_type = 'listening_episode')
    """)

    # One initial replay rebuilds historical fallback Play from evidence, not
    # just the adapter's normal three-day trailing window.
    op.execute("""
        UPDATE projection_checkpoints SET watermark = NULL, watermark_id = NULL
        WHERE source_name = 'chronicler.situation_inferred'
    """)
    # The replay acknowledgement is published atomically with its checkpoint.
    # Any real old-date correction queues work in its own source transaction.
    op.execute("""
        CREATE OR REPLACE FUNCTION invalidate_daily_repair_episode()
        RETURNS TRIGGER LANGUAGE plpgsql SET search_path = chronicler, public AS $$
        DECLARE
            item RECORD;
            target UUID;
            override_start TIMESTAMPTZ;
            override_end TIMESTAMPTZ;
            source_privacy TEXT;
            source_tombstone TIMESTAMPTZ;
            source_retention INTEGER;
            source_start TIMESTAMPTZ;
            redact_text BOOLEAN := FALSE;
            reason_code TEXT := 'source_changed';
        BEGIN
            IF TG_TABLE_NAME = 'episodes' THEN
                IF TG_OP = 'UPDATE' AND
                    (to_jsonb(OLD) - ARRAY['updated_at', 'created_at']) =
                    (to_jsonb(NEW) - ARRAY['updated_at', 'created_at']) THEN
                    RETURN NULL;
                END IF;
                target := coalesce(NEW.id, OLD.id);
                redact_text := coalesce(
                    TG_OP = 'DELETE'
                    OR OLD.privacy = 'restricted' OR NEW.privacy = 'restricted'
                    OR OLD.tombstone_at IS NOT NULL OR NEW.tombstone_at IS NOT NULL
                    OR (OLD.retention_days IS NOT NULL
                        AND OLD.start_at + make_interval(days => OLD.retention_days) < now())
                    OR (NEW.retention_days IS NOT NULL
                        AND NEW.start_at + make_interval(days => NEW.retention_days) < now()),
                    FALSE
                );
                IF TG_OP = 'DELETE' THEN
                    reason_code := 'source_delete';
                ELSIF OLD.tombstone_at IS NOT NULL OR NEW.tombstone_at IS NOT NULL THEN
                    reason_code := 'source_tombstone';
                ELSIF OLD.privacy = 'restricted' OR NEW.privacy = 'restricted' THEN
                    reason_code := 'source_privacy';
                ELSIF redact_text THEN
                    reason_code := 'source_retention';
                END IF;
                -- NULL timing fields fall back to this base row, never to a stale override.
                SELECT corrected_start_at, corrected_end_at
                INTO override_start, override_end FROM v_latest_overrides
                WHERE target_kind = 'episode' AND target_id = target;
                IF TG_OP <> 'INSERT' THEN
                    PERFORM enqueue_daily_repair(
                        OLD.start_at, OLD.end_at, FALSE, ARRAY[target], redact_text, reason_code
                    );
                    PERFORM enqueue_daily_repair(
                        coalesce(override_start, OLD.start_at),
                        coalesce(override_end, OLD.end_at), FALSE, ARRAY[target],
                        redact_text, reason_code
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    PERFORM enqueue_daily_repair(
                        NEW.start_at, NEW.end_at, FALSE, ARRAY[target], redact_text, reason_code
                    );
                    PERFORM enqueue_daily_repair(
                        coalesce(override_start, NEW.start_at),
                        coalesce(override_end, NEW.end_at), FALSE, ARRAY[target],
                        redact_text, reason_code
                    );
                END IF;
                UPDATE daily_repair_archive AS archive
                SET content = NULL, audit_hash = NULL, text_access = 'redacted'
                WHERE target = ANY(archive.dependency_ids)
                  AND (
                      coalesce(redact_text, FALSE)
                      OR archive.text_access = 'redacted'
                      OR EXISTS (
                          SELECT 1 FROM daily_repair_source_redactions AS redaction
                          WHERE redaction.source_id = target
                      )
                      OR EXISTS (
                          SELECT 1 FROM daily_repair_fact_dispositions AS disposition
                          WHERE disposition.disposition = 'disposed'
                            AND disposition.redacted
                            AND target = ANY(disposition.dependency_ids)
                      )
                  );
                IF coalesce(NEW.source_name, OLD.source_name)
                    <> 'chronicler.situation_inferred' THEN
                    UPDATE daily_repair_control SET replay_epoch = replay_epoch + 1
                    WHERE singleton;
                END IF;
            ELSE
                target := coalesce(NEW.target_id, OLD.target_id);
                IF coalesce(NEW.target_kind, OLD.target_kind) <> 'episode' THEN
                    RETURN NULL;
                END IF;
                IF TG_OP = 'UPDATE' AND
                    (to_jsonb(OLD) - ARRAY['updated_at', 'created_at']) =
                    (to_jsonb(NEW) - ARRAY['updated_at', 'created_at']) THEN
                    RETURN NULL;
                END IF;
                SELECT privacy, tombstone_at, retention_days, start_at
                INTO source_privacy, source_tombstone, source_retention, source_start
                FROM episodes WHERE id = target;
                redact_text := coalesce(
                    OLD.corrected_privacy = 'restricted'
                    OR NEW.corrected_privacy = 'restricted'
                    OR OLD.corrected_tombstone_at IS NOT NULL
                    OR NEW.corrected_tombstone_at IS NOT NULL
                    OR source_privacy = 'restricted'
                    OR source_tombstone IS NOT NULL
                    OR (source_retention IS NOT NULL
                        AND source_start + make_interval(days => source_retention) < now()),
                    FALSE
                );
                IF OLD.corrected_tombstone_at IS NOT NULL OR NEW.corrected_tombstone_at IS NOT NULL
                    OR source_tombstone IS NOT NULL THEN
                    reason_code := 'source_tombstone';
                ELSIF OLD.corrected_privacy = 'restricted' OR NEW.corrected_privacy = 'restricted'
                    OR source_privacy = 'restricted' THEN
                    reason_code := 'source_privacy';
                ELSIF redact_text THEN
                    reason_code := 'source_retention';
                END IF;
                IF TG_OP <> 'INSERT' THEN
                    PERFORM enqueue_daily_repair(
                        OLD.corrected_start_at, OLD.corrected_end_at, FALSE, ARRAY[target],
                        redact_text, reason_code
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    PERFORM enqueue_daily_repair(
                        NEW.corrected_start_at, NEW.corrected_end_at, FALSE, ARRAY[target],
                        redact_text, reason_code
                    );
                END IF;
                FOR item IN
                    SELECT start_at, end_at FROM episodes WHERE id = target
                    UNION ALL
                    SELECT start_at, end_at FROM v_episodes_corrected WHERE id = target
                LOOP
                    PERFORM enqueue_daily_repair(
                        item.start_at, item.end_at, FALSE, ARRAY[target], redact_text, reason_code
                    );
                END LOOP;
                UPDATE daily_repair_control SET replay_epoch = replay_epoch + 1
                WHERE singleton;
                -- Copied audit text is removed on privacy, tombstone, deletion,
                -- and expiry, while ordinary timing/note corrections preserve it.
                UPDATE daily_repair_archive AS archive
                SET content = NULL, audit_hash = NULL, text_access = 'redacted'
                WHERE target = ANY(archive.dependency_ids)
                  AND (
                      coalesce(redact_text, FALSE)
                      OR archive.text_access = 'redacted'
                      OR EXISTS (
                          SELECT 1 FROM daily_repair_source_redactions AS redaction
                          WHERE redaction.source_id = target
                      )
                      OR EXISTS (
                          SELECT 1 FROM daily_repair_fact_dispositions AS disposition
                          WHERE disposition.disposition = 'disposed'
                            AND disposition.redacted
                            AND target = ANY(disposition.dependency_ids)
                      )
                  );
            END IF;
            RETURN NULL;
        END $$;
        DROP TRIGGER IF EXISTS episodes_daily_repair ON episodes;
        CREATE TRIGGER episodes_daily_repair AFTER INSERT OR UPDATE OR DELETE ON episodes
            FOR EACH ROW EXECUTE FUNCTION invalidate_daily_repair_episode();
        DROP TRIGGER IF EXISTS overrides_daily_repair ON overrides;
        CREATE TRIGGER overrides_daily_repair AFTER INSERT OR UPDATE OR DELETE ON overrides
            FOR EACH ROW EXECUTE FUNCTION invalidate_daily_repair_episode();
    """)
    # Local Chronicler memory reads share the same source classification as the
    # public projection guards, but accept only the selected canonical row. The
    # view is used by explicitly configured chronicler_mem readers; it is absent
    # before 030, where application SQL applies the narrow generated-predicate
    # fallback without naming optional repair relations.
    op.execute("""
        CREATE OR REPLACE FUNCTION chronicler.memory_fact_row_visible(
            p_fact chronicler_mem.facts
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp
        AS $visibility$
        DECLARE
            v_fact_date date;
            v_closed_through date;
            v_replay_epoch bigint;
            v_replay_ack bigint;
            v_blocked_reason text;
            v_timezone text;
            v_hidden boolean;
            v_rows bigint;
        BEGIN
            IF p_fact.id IS NULL THEN
                RETURN false;
            END IF;
            IF pg_catalog.to_regclass('chronicler.daily_repair_fact_dispositions') IS NOT NULL THEN
                EXECUTE 'SELECT EXISTS ('
                     || 'SELECT 1 FROM chronicler.daily_repair_fact_dispositions '
                     || 'WHERE fact_id = $1 AND disposition = ''disposed'' AND redacted)'
                  INTO v_hidden USING p_fact.id;
                IF v_hidden THEN
                    RETURN false;
                END IF;
            END IF;
            IF p_fact.content_authority IN ('owner', 'owner_device') THEN
                RETURN true;
            END IF;
            IF p_fact.predicate NOT IN (
                'lane-skew', 'sleep-debt', 'revisit-low-confidence-block'
            ) THEN
                RETURN true;
            END IF;
            IF p_fact.source_butler IS DISTINCT FROM 'chronicler'
               OR p_fact.validity IS NULL
               OR p_fact.validity NOT IN ('active', 'fading')
               OR p_fact.invalid_at IS NOT NULL
               OR pg_catalog.jsonb_typeof(p_fact.metadata) IS DISTINCT FROM 'object'
               OR p_fact.metadata->>'source' IS DISTINCT FROM 'chronicler'
               OR pg_catalog.jsonb_typeof(p_fact.tags) IS DISTINCT FROM 'array' THEN
                RETURN false;
            END IF;

            IF p_fact.predicate = 'sleep-debt' THEN
                IF pg_catalog.jsonb_typeof(p_fact.metadata->'as_of') IS DISTINCT FROM 'string'
                   OR p_fact.subject IS DISTINCT FROM 'sleep'
                   OR pg_catalog.jsonb_typeof(p_fact.metadata->'debt_seconds') IS DISTINCT FROM 'number'
                   OR pg_catalog.jsonb_typeof(p_fact.metadata->'shortfall_days') IS DISTINCT FROM 'number'
                   OR pg_catalog.jsonb_typeof(p_fact.metadata->'window_days') IS DISTINCT FROM 'number'
                   OR NOT p_fact.tags @> '["chronicler-insight", "sleep-debt"]'::jsonb THEN
                    RETURN false;
                END IF;
                v_fact_date := NULLIF(p_fact.metadata->>'as_of', '')::date;
            ELSIF p_fact.predicate = 'lane-skew' THEN
                IF pg_catalog.jsonb_typeof(p_fact.metadata->'local_date') IS DISTINCT FROM 'string'
                   OR COALESCE(p_fact.metadata->>'lane', '') = ''
                   OR p_fact.subject IS DISTINCT FROM (p_fact.metadata->>'lane' || ' lane')
                   OR p_fact.metadata->>'direction' IS NULL
                   OR p_fact.metadata->>'direction' NOT IN ('up', 'down')
                   OR pg_catalog.jsonb_typeof(p_fact.metadata->'seconds') IS DISTINCT FROM 'number'
                   OR NOT p_fact.tags @> pg_catalog.jsonb_build_array(
                       'chronicler-insight', 'lane-skew', p_fact.metadata->>'lane'
                   ) THEN
                    RETURN false;
                END IF;
                v_fact_date := NULLIF(p_fact.metadata->>'local_date', '')::date;
            ELSE
                IF pg_catalog.jsonb_typeof(p_fact.metadata->'local_date') IS DISTINCT FROM 'string'
                   OR p_fact.metadata->>'flag_type' IS NULL
                   OR p_fact.metadata->>'flag_type' NOT IN ('feeder_dark', 'sleep_missing')
                   OR p_fact.metadata->>'severity' IS NULL
                   OR p_fact.subject IS DISTINCT FROM (
                       (p_fact.metadata->>'local_date') || ' ' || (p_fact.metadata->>'flag_type')
                   )
                   OR pg_catalog.jsonb_typeof(p_fact.metadata->'detail') IS DISTINCT FROM 'object'
                   OR NOT p_fact.tags @> pg_catalog.jsonb_build_array(
                       'chronicler-self-reminder', p_fact.metadata->>'flag_type'
                   ) THEN
                    RETURN false;
                END IF;
                v_fact_date := NULLIF(p_fact.metadata->>'local_date', '')::date;
            END IF;

            IF v_fact_date IS NULL
               OR pg_catalog.to_regclass('chronicler.daily_repair_control') IS NULL
               OR pg_catalog.to_regclass('chronicler.daily_repair_days') IS NULL
               OR pg_catalog.to_regclass('chronicler.daily_repair_fact_dispositions') IS NULL
               OR pg_catalog.to_regclass('chronicler.daily_repair_source_redactions') IS NULL
               OR pg_catalog.to_regclass('chronicler.daily_repair_retention_spans') IS NULL
               OR pg_catalog.to_regclass('chronicler.episodes') IS NULL
               OR pg_catalog.to_regclass('chronicler.v_episodes_corrected') IS NULL THEN
                RETURN false;
            END IF;
            EXECUTE 'SELECT closed_through, replay_epoch, replay_ack, blocked_reason, timezone '
                 || 'FROM chronicler.daily_repair_control WHERE singleton'
              INTO v_closed_through, v_replay_epoch, v_replay_ack, v_blocked_reason, v_timezone;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            IF v_rows = 0 OR v_blocked_reason IS NOT NULL OR v_timezone IS NULL OR v_timezone = ''
               OR v_replay_epoch IS DISTINCT FROM v_replay_ack
               OR v_fact_date > v_closed_through
               OR NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = v_timezone
               ) THEN
                RETURN false;
            END IF;
            EXECUTE 'SELECT EXISTS ('
                 || 'SELECT 1 FROM chronicler.daily_repair_days '
                 || 'WHERE local_date = $1 AND facts_pending)'
              INTO v_hidden USING v_fact_date;
            IF v_hidden THEN
                RETURN false;
            END IF;
            EXECUTE 'SELECT disposition = ''disposed'' OR redacted '
                 || 'FROM chronicler.daily_repair_fact_dispositions '
                 || 'WHERE fact_id = $1 AND local_date = $2 '
                 || 'ORDER BY generation DESC LIMIT 1'
              INTO v_hidden USING p_fact.id, v_fact_date;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            IF v_rows > 0 AND v_hidden IS TRUE THEN
                RETURN false;
            END IF;
            EXECUTE 'SELECT EXISTS ('
                 || 'SELECT 1 FROM chronicler.daily_repair_retention_spans AS span '
                 || 'WHERE $1 BETWEEN span.cursor_date AND '
                 || 'LEAST(span.last_date + 28, $2))'
              INTO v_hidden USING v_fact_date, v_closed_through;
            IF v_hidden THEN
                RETURN false;
            END IF;
            EXECUTE $source_check$
                SELECT EXISTS (
                    SELECT 1
                    FROM chronicler.episodes AS raw
                    JOIN chronicler.v_episodes_corrected AS effective
                      ON effective.id = raw.id
                    CROSS JOIN chronicler.daily_repair_control AS control
                    WHERE (raw.privacy = 'restricted' OR raw.tombstone_at IS NOT NULL
                           OR (raw.retention_days IS NOT NULL
                               AND raw.start_at + pg_catalog.make_interval(days => raw.retention_days)
                                   < pg_catalog.clock_timestamp())
                           OR effective.privacy = 'restricted'
                           OR effective.tombstone_at IS NOT NULL
                           OR (effective.retention_days IS NOT NULL
                               AND effective.start_at + pg_catalog.make_interval(days => effective.retention_days)
                                   < pg_catalog.clock_timestamp()))
                      AND NOT EXISTS (
                          SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                          WHERE redaction.source_id = raw.id
                      )
                      AND (
                          $1 BETWEEN (raw.start_at AT TIME ZONE control.timezone)::date
                              AND LEAST(COALESCE(
                                  ((raw.end_at - interval '1 microsecond')
                                      AT TIME ZONE control.timezone)::date,
                                  control.closed_through), control.closed_through) + 28
                          OR $1 BETWEEN (effective.start_at AT TIME ZONE control.timezone)::date
                              AND LEAST(COALESCE(
                                  ((effective.end_at - interval '1 microsecond')
                                      AT TIME ZONE control.timezone)::date,
                                  control.closed_through), control.closed_through) + 28
                      )
                )
            $source_check$ INTO v_hidden USING v_fact_date;
            IF v_hidden THEN
                RETURN false;
            END IF;
            RETURN true;
        EXCEPTION
            WHEN invalid_datetime_format OR datetime_field_overflow THEN
                RETURN false;
        END
        $visibility$;
        REVOKE ALL ON FUNCTION chronicler.memory_fact_row_visible(chronicler_mem.facts) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION chronicler.memory_fact_row_visible(chronicler_mem.facts)
            TO butler_chronicler_rw;
        CREATE OR REPLACE VIEW chronicler_mem.facts_readable
            WITH (security_invoker = true) AS
            SELECT fact.* FROM chronicler_mem.facts AS fact
            WHERE chronicler.memory_fact_row_visible(fact);
        REVOKE ALL ON chronicler_mem.facts_readable FROM PUBLIC;
        GRANT SELECT ON chronicler_mem.facts_readable TO butler_chronicler_rw;
    """)
    # The canonical and repair relations now exist. Refresh the fixed definer's
    # exact read columns only after the migration's preflight and DDL succeed.
    op.execute("SELECT chronicler_memory_admin.finalize_interface()")


def downgrade() -> None:
    pass
