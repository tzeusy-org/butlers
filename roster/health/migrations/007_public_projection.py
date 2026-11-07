"""Typed health facts and lossless JSON fields (RFC 0039 D2/D4).

Revision ID: health_007
Revises: health_006
"""

from alembic import op

revision = "health_007"
down_revision = "health_006"
branch_labels = None
depends_on = None

_TABLES = (
    "calendar_event_instances",
    "calendar_events",
    "calendar_sources",
    "conditions",
    "conditions_public",
    "facts",
    "meals",
    "measurements_public",
    "medication_doses",
    "medications",
    "medications_public",
    "research",
    "symptoms",
    "visit_briefs",
)

_SYNC = """
BEGIN
    IF TG_OP <> 'INSERT' THEN
        DELETE FROM $schema$.measurements_public
            WHERE id=OLD.id::text OR starts_with(id,OLD.id::text || ':');
        DELETE FROM $schema$.medications_public WHERE id=OLD.id;
        DELETE FROM $schema$.conditions_public WHERE id=OLD.id;
    END IF;
    IF TG_OP <> 'DELETE' THEN PERFORM $schema$.mcp_write_health_fact(to_jsonb(NEW)); END IF;
    RETURN NULL;
END
"""


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.exec_driver_sql("SELECT current_schema()").scalar_one()
    quoted = bind.dialect.identifier_preparer.quote(schema)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    sources = [
        f"{quoted}.{table}"
        for table in _TABLES
        if bind.exec_driver_sql(f"SELECT to_regclass('{quoted}.{table}')").scalar()
    ]
    op.execute("LOCK TABLE " + ",".join(sources) + " IN SHARE ROW EXCLUSIVE MODE")
    sql = r"""
        CREATE TABLE $schema$.measurements_public (
            id TEXT PRIMARY KEY, metric TEXT NOT NULL, value_numeric NUMERIC NOT NULL,
            unit TEXT, measured_at TIMESTAMPTZ
        );
        CREATE TABLE $schema$.medications_public (
            id UUID PRIMARY KEY, name TEXT, dosage TEXT, frequency TEXT, schedule TEXT[],
            active BOOLEAN, notes TEXT, recorded_at TIMESTAMPTZ
        );
        CREATE TABLE $schema$.conditions_public (
            id UUID PRIMARY KEY, name TEXT, status TEXT, diagnosed_at TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS $schema$.json_fields_public (
            id TEXT PRIMARY KEY, source_table TEXT NOT NULL, row_id TEXT NOT NULL,
            path TEXT NOT NULL, value_text TEXT, value_numeric NUMERIC,
            value_type TEXT NOT NULL CHECK (value_type IN ('string','number','boolean','null')),
            UNIQUE(source_table,row_id,path)
        );
        CREATE FUNCTION $schema$.mcp_health_text(meta jsonb, key text, fallback text DEFAULT NULL)
        RETURNS text LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $body$
            SELECT CASE WHEN jsonb_typeof(meta) IS DISTINCT FROM 'object' THEN NULL
                WHEN NOT meta ? key THEN fallback
                WHEN jsonb_typeof(meta->key)='string' THEN meta->>key END
        $body$;
        CREATE FUNCTION $schema$.mcp_write_health_fact(source_row jsonb) RETURNS void
        LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $body$
        DECLARE
            meta jsonb := source_row->'metadata';
            fact_predicate text := source_row->>'predicate';
            fact_id text := source_row->>'id';
            reading jsonb;
            unit text;
            schedule text[];
        BEGIN
            IF source_row->>'validity' IS DISTINCT FROM 'active' THEN RETURN; END IF;
            IF starts_with(fact_predicate,'measurement_') THEN
                reading := meta->'value';
                IF meta ? 'unit' THEN
                    unit := $schema$.mcp_health_text(meta,'unit');
                ELSE
                    SELECT units.unit INTO unit FROM (VALUES
                        ('measurement_weight','kg'), ('measurement_blood_pressure','mmHg'),
                        ('measurement_heart_rate','bpm'), ('measurement_blood_sugar','mg/dL'),
                        ('measurement_temperature','°C'), ('measurement_resting_hr','bpm'),
                        ('measurement_hrv','ms'), ('measurement_spo2','%'),
                        ('measurement_breathing_rate','breaths/min'), ('measurement_steps','steps'),
                        ('measurement_active_minutes','min'), ('measurement_vo2_max','ml/kg/min'),
                        ('measurement_body_fat','%')
                    ) AS units(predicate,unit) WHERE units.predicate=fact_predicate;
                END IF;
                IF jsonb_typeof(reading)='number' THEN
                    INSERT INTO $schema$.measurements_public
                    VALUES(fact_id,fact_predicate,(reading #>> '{}')::numeric,unit,
                           (source_row->>'valid_at')::timestamptz);
                ELSIF jsonb_typeof(reading)='object' THEN
                    INSERT INTO $schema$.measurements_public
                    SELECT fact_id || ':' || key, fact_predicate || '.' || key,
                        (value #>> '{}')::numeric, unit, (source_row->>'valid_at')::timestamptz
                    FROM jsonb_each(reading) WHERE jsonb_typeof(value)='number'
                        AND NOT public.mcp_json_credential(
                            ARRAY['metadata','value'] || public.mcp_json_tokens(key));
                END IF;
            ELSIF fact_predicate='medication' THEN
                IF jsonb_typeof(meta)='object' AND NOT meta ? 'schedule' THEN
                    schedule := ARRAY[]::text[];
                ELSIF jsonb_typeof(meta->'schedule')='string' THEN
                    schedule := ARRAY[meta->>'schedule'];
                ELSIF jsonb_typeof(meta->'schedule')='array' THEN
                    SELECT ARRAY(SELECT CASE WHEN jsonb_typeof(value)='string'
                        THEN value #>> '{}' END
                        FROM jsonb_array_elements(meta->'schedule') WITH ORDINALITY
                        ORDER BY ordinality) INTO schedule;
                END IF;
                INSERT INTO $schema$.medications_public VALUES(
                    fact_id::uuid,$schema$.mcp_health_text(meta,'name',''),
                    $schema$.mcp_health_text(meta,'dosage',''),
                    $schema$.mcp_health_text(meta,'frequency',''), schedule,
                    CASE WHEN jsonb_typeof(meta) IS DISTINCT FROM 'object' THEN NULL
                         WHEN NOT meta ? 'active' THEN true
                         WHEN jsonb_typeof(meta->'active')='boolean' THEN (meta->>'active')::boolean END,
                    $schema$.mcp_health_text(meta,'notes'),(source_row->>'created_at')::timestamptz);
            ELSIF fact_predicate='condition' THEN
                INSERT INTO $schema$.conditions_public VALUES(
                    fact_id::uuid,$schema$.mcp_health_text(meta,'name',source_row->>'content'),
                    $schema$.mcp_health_text(meta,'status','active'),
                    $schema$.mcp_health_text(meta,'diagnosed_at'),$schema$.mcp_health_text(meta,'notes'));
            END IF;
        END
        $body$;
        CREATE FUNCTION $schema$.mcp_sync_health_fact() RETURNS trigger LANGUAGE plpgsql
            SET search_path = pg_catalog, pg_temp AS $body$$sync$$body$;
    """.replace("$sync$", _SYNC).replace("$schema$", quoted)
    op.execute(sql)
    body = r"""
        DECLARE
            source_schema text := $literal$;
            item record;
            source_oid oid;
            installed record;
        BEGIN
            FOR item IN SELECT * FROM (VALUES
                ('calendar_event_instances','id',ARRAY['metadata']::text[]),
                ('calendar_events','id',ARRAY['metadata']::text[]),
                ('calendar_sources','id',ARRAY['metadata']::text[]),
                ('conditions','id',ARRAY[]::text[]),
                ('conditions_public','id',ARRAY[]::text[]),
                ('facts','id',ARRAY['metadata','tags']::text[]),
                ('meals','id',ARRAY['nutrition']::text[]),
                ('measurements_public','id',ARRAY[]::text[]),
                ('medication_doses','id',ARRAY[]::text[]),
                ('medications','id',ARRAY['schedule']::text[]),
                ('medications_public','id',ARRAY[]::text[]),
                ('research','id',ARRAY['tags']::text[]),
                ('symptoms','id',ARRAY[]::text[]),
                ('visit_briefs','event_id',ARRAY['digest']::text[])
            ) AS source(name,key_column,json_columns) LOOP
                IF item.name IN ('calendar_sources','calendar_events','calendar_event_instances')
                    AND to_regclass(format('%I.%I',source_schema,item.name)) IS NULL THEN
                    -- Legacy reader fixtures may migrate core into public and
                    -- the butler chain into health. Never bind that public
                    -- calendar as a health export. A migrated own core must
                    -- contain its declared sources, so drift still refuses.
                    IF EXISTS (SELECT 1 FROM $schema$.alembic_version
                               WHERE version_num ~ '^core_[0-9]+$') THEN
                        RAISE EXCEPTION 'health calendar source missing after core';
                    END IF;
                    CONTINUE;
                END IF;
                IF item.name='facts' AND to_regclass(format('%I.facts',source_schema)) IS NULL THEN
                    IF require_facts THEN RAISE EXCEPTION 'health facts missing after memory'; END IF;
                    CONTINUE;
                END IF;
                PERFORM public.mcp_install_json_projection(source_schema,item.name,
                    item.key_column,item.json_columns,item.name='facts');
            END LOOP;
            source_oid := to_regclass(format('%I.facts',source_schema));
            IF source_oid IS NULL THEN RETURN; END IF;
            IF (SELECT prosrc FROM pg_proc WHERE oid='$schema$.mcp_sync_health_fact()'::regprocedure)
                IS DISTINCT FROM $expected$$sync$$expected$ THEN
                RAISE EXCEPTION 'health fact trigger function definition drift';
            END IF;
            SELECT * INTO installed FROM pg_trigger
                WHERE tgrelid=source_oid AND tgname='trg_mcp_health_fact';
            IF FOUND THEN
                IF installed.tgfoid<>'$schema$.mcp_sync_health_fact()'::regprocedure
                    OR installed.tgtype<>29 OR installed.tgenabled<>'O'
                    OR installed.tgqual IS NOT NULL OR installed.tgnargs<>0 THEN
                    RAISE EXCEPTION 'health fact trigger definition drift';
                END IF;
                RETURN;
            END IF;
            LOCK TABLE $schema$.facts IN SHARE ROW EXCLUSIVE MODE;
            CREATE TRIGGER trg_mcp_health_fact AFTER INSERT OR UPDATE OR DELETE ON $schema$.facts
                FOR EACH ROW EXECUTE FUNCTION $schema$.mcp_sync_health_fact();
            DELETE FROM $schema$.measurements_public;
            DELETE FROM $schema$.medications_public;
            DELETE FROM $schema$.conditions_public;
            PERFORM $schema$.mcp_write_health_fact(to_jsonb(f)) FROM $schema$.facts f;
        END
    """.replace("$literal$", "'" + schema.replace("'", "''") + "'")
    body = body.replace("$sync$", _SYNC).replace("$schema$", quoted)
    op.execute(
        f"CREATE FUNCTION {quoted}.mcp_install_health_projection(require_facts boolean) "
        "RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $body$"
        + body
        + "$body$"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {quoted}.mcp_install_health_projection(boolean) FROM PUBLIC"
    )
    op.execute(f"SELECT {quoted}.mcp_install_health_projection(false)")


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.exec_driver_sql("SELECT current_schema()").scalar_one()
    quote = bind.dialect.identifier_preparer.quote
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _TABLES:
        if bind.exec_driver_sql(f"SELECT to_regclass('{quote(schema)}.{table}')").scalar():
            op.execute(f"DROP TRIGGER IF EXISTS trg_mcp_json_fields ON {quote(schema)}.{table}")
    if bind.exec_driver_sql(f"SELECT to_regclass('{quote(schema)}.facts')").scalar():
        op.execute(f"DROP TRIGGER IF EXISTS trg_mcp_health_fact ON {quote(schema)}.facts")
    for signature in (
        "mcp_install_health_projection(boolean)",
        "mcp_sync_health_fact()",
        "mcp_write_health_fact(jsonb)",
        "mcp_health_text(jsonb,text,text)",
    ):
        op.execute(f"DROP FUNCTION {quote(schema)}.{signature}")
    for table in (
        "measurements_public",
        "medications_public",
        "conditions_public",
        "json_fields_public",
    ):
        op.execute(f"DROP TABLE {quote(schema)}.{table}")
