"""Shared scalar JSON materialization for finance and health (RFC 0039 D2).

Revision ID: core_320
Revises: core_319

The domain chains own tables, source maps and installation. This revision owns
one invoker trigger and its extraction/installation primitives, so either domain
can install independently. Nothing here creates a publication or changes readers.
"""

from alembic import op

revision = "core_320"
down_revision = "core_319"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_320:json-projection', 0))"

_TRIGGER_BODY = """
DECLARE
    source_name text := TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
    old_key text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_key := to_jsonb(OLD) ->> TG_ARGV[0];
        EXECUTE format('DELETE FROM %I.json_fields_public WHERE source_table=$1 AND row_id=$2',
                       TG_TABLE_SCHEMA) USING source_name, old_key;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        PERFORM public.mcp_write_json_fields(TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_ARGV[0],
            TG_ARGV[2:TG_NARGS-1], TG_ARGV[1]::boolean, to_jsonb(NEW));
    END IF;
    RETURN NULL;
END
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(_LOCK)
    # Shared per-schema replay must not replace a function while a domain writes.
    if (
        op.get_bind()
        .exec_driver_sql("SELECT to_regprocedure('public.mcp_sync_json_fields()') IS NOT NULL")
        .scalar()
    ):
        return
    op.execute(r"""
        CREATE FUNCTION public.mcp_json_tokens(key text) RETURNS text[]
        LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $body$
            SELECT array_remove(regexp_split_to_array(lower(regexp_replace(
                key, '([a-z])([A-Z])', '\1_\2', 'g')), '[._[:space:]-]+'), '')
        $body$;

        CREATE FUNCTION public.mcp_json_credential(tokens text[]) RETURNS boolean
        LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $body$
        DECLARE
            denied text;
            parts text[];
            i integer;
        BEGIN
            FOREACH denied IN ARRAY ARRAY['secret','token','password','credential','otp','cvv',
                'card','api_key','private_key','access_key','client_key','signing_key','access_url',
                'passcode','passphrase','pin','bearer','cookie','auth'] LOOP
                parts := string_to_array(denied, '_');
                FOR i IN 1..cardinality(tokens)-cardinality(parts)+1 LOOP
                    IF tokens[i:i+cardinality(parts)-1] = parts THEN RETURN true; END IF;
                END LOOP;
            END LOOP;
            RETURN false;
        END
        $body$;

        CREATE FUNCTION public.mcp_json_path_key(key text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $body$
            SELECT replace(replace(replace(replace(key,
                E'\\', E'\\\\'), '.', E'\\.'), '[', E'\\['), ']', E'\\]')
        $body$;

        CREATE FUNCTION public.mcp_json_leaves(value jsonb, prefix text, tokens text[])
        RETURNS TABLE(path text, value_text text, value_numeric numeric, value_type text)
        LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $body$
        DECLARE
            member record;
            kind text := jsonb_typeof(value);
        BEGIN
            IF kind IS NULL OR public.mcp_json_credential(tokens) THEN RETURN; END IF;
            IF kind = 'object' THEN
                FOR member IN SELECT * FROM jsonb_each(value) LOOP
                    RETURN QUERY SELECT * FROM public.mcp_json_leaves(member.value,
                        prefix || '.' || public.mcp_json_path_key(member.key),
                        tokens || public.mcp_json_tokens(member.key));
                END LOOP;
            ELSIF kind = 'array' THEN
                FOR member IN SELECT * FROM jsonb_array_elements(value) WITH ORDINALITY LOOP
                    RETURN QUERY SELECT * FROM public.mcp_json_leaves(member.value,
                        prefix || '.[' || (member.ordinality-1)::text || ']', tokens);
                END LOOP;
            ELSE
                RETURN QUERY SELECT prefix, value #>> '{}',
                    CASE WHEN kind='number' THEN (value #>> '{}')::numeric END, kind;
            END IF;
        END
        $body$;

        CREATE FUNCTION public.mcp_write_json_fields(source_schema text, source_table text,
            key_column text, json_columns text[], active_only boolean, source_row jsonb)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $body$
        BEGIN
            IF active_only AND source_row->>'validity' IS DISTINCT FROM 'active' THEN
                RETURN;
            END IF;
            EXECUTE format($query$
                INSERT INTO %I.json_fields_public
                    (id, source_table, row_id, path, value_text, value_numeric, value_type)
                SELECT jsonb_build_array($1,$2,leaf.path)::text, $1,$2,
                       leaf.path,leaf.value_text,leaf.value_numeric,leaf.value_type
                FROM unnest($4::text[]) AS col(name)
                CROSS JOIN LATERAL public.mcp_json_leaves(
                    $3->col.name, public.mcp_json_path_key(col.name),
                    public.mcp_json_tokens(col.name)) AS leaf
            $query$, source_schema)
            USING source_schema || '.' || source_table, source_row->>key_column,
                  source_row, json_columns;
        END
        $body$;
    """)
    op.execute(
        "CREATE FUNCTION public.mcp_sync_json_fields() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path = pg_catalog, pg_temp AS $body$" + _TRIGGER_BODY + "$body$"
    )
    op.execute(
        r"""
        CREATE FUNCTION public.mcp_install_json_projection(source_schema text, source_table text,
            key_column text, json_columns text[], active_only boolean)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $body$
        DECLARE
            source_oid oid;
            actual_key text[];
            actual_columns text[];
            expected_args bytea;
            installed record;
            arg_sql text;
        BEGIN
            IF source_schema NOT IN ('finance','health','public') THEN
                RAISE EXCEPTION 'unsupported projection source schema';
            END IF;
            IF (SELECT prosrc FROM pg_proc
                WHERE oid='public.mcp_sync_json_fields()'::regprocedure)
                IS DISTINCT FROM $expected$$trigger_body$$expected$ THEN
                RAISE EXCEPTION 'projection JSON trigger function definition drift';
            END IF;
            source_oid := to_regclass(format('%I.%I',source_schema,source_table));
            IF source_oid IS NULL THEN RAISE EXCEPTION 'projection source missing'; END IF;
            SELECT array_agg(a.attname::text ORDER BY k.ordinality) INTO actual_key
            FROM pg_constraint c
            CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(num,ordinality)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num
            WHERE c.conrelid=source_oid AND c.contype='p';
            IF actual_key IS DISTINCT FROM ARRAY[key_column] THEN
                RAISE EXCEPTION 'projection source must have declared single-column primary key';
            END IF;
            SELECT coalesce(array_agg(attname::text ORDER BY attname), ARRAY[]::text[])
                INTO actual_columns FROM pg_attribute
            WHERE attrelid=source_oid AND atttypid='jsonb'::regtype AND NOT attisdropped;
            IF actual_columns IS DISTINCT FROM
                ARRAY(SELECT col FROM unnest(json_columns) AS col ORDER BY col) THEN
                RAISE EXCEPTION 'projection JSON column map drift';
            END IF;
            IF cardinality(json_columns)=0 THEN RETURN; END IF;
            SELECT string_agg(convert_to(arg,'UTF8') || decode('00','hex'), ''::bytea ORDER BY n),
                   string_agg(quote_literal(arg), ',' ORDER BY n)
                INTO expected_args,arg_sql
            FROM unnest(ARRAY[key_column,active_only::text] || json_columns)
                WITH ORDINALITY AS x(arg,n);
            SELECT * INTO installed FROM pg_trigger
            WHERE tgrelid=source_oid AND tgname='trg_mcp_json_fields';
            IF FOUND THEN
                IF installed.tgfoid <> 'public.mcp_sync_json_fields()'::regprocedure
                    OR installed.tgtype<>29 OR installed.tgenabled<>'O'
                    OR installed.tgqual IS NOT NULL OR installed.tgargs<>expected_args
                    OR installed.tgisinternal THEN
                    RAISE EXCEPTION 'projection JSON trigger definition drift';
                END IF;
                RETURN;
            END IF;
            EXECUTE format('LOCK TABLE %I.%I IN SHARE ROW EXCLUSIVE MODE',
                source_schema,source_table);
            EXECUTE format('CREATE TRIGGER trg_mcp_json_fields AFTER INSERT OR UPDATE OR DELETE '
                'ON %I.%I FOR EACH ROW EXECUTE FUNCTION public.mcp_sync_json_fields(%s)',
                source_schema,source_table,arg_sql);
            EXECUTE format('DELETE FROM %I.json_fields_public WHERE source_table=$1',source_schema)
                USING source_schema || '.' || source_table;
            EXECUTE format('SELECT public.mcp_write_json_fields($1,$2,$3,$4,$5,to_jsonb(src)) '
                'FROM %I.%I AS src',source_schema,source_table)
                USING source_schema,source_table,key_column,json_columns,active_only;
        END
        $body$;
    """.replace("$trigger_body$", _TRIGGER_BODY)
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(_LOCK)
    bind = op.get_bind()
    # A domain chain may still own a live consumer after this core peer leaves.
    if bind.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM pg_trigger "
        "WHERE tgfoid='public.mcp_sync_json_fields()'::regprocedure)"
    ).scalar():
        return
    quote = bind.dialect.identifier_preparer.quote
    peers = 0
    for schema in bind.exec_driver_sql(
        "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname='alembic_version' AND c.relkind IN ('r','p') "
        "AND left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'"
    ).scalars():
        peers += bool(
            bind.exec_driver_sql(
                f"SELECT EXISTS(SELECT 1 FROM {quote(schema)}.alembic_version "
                "WHERE CASE WHEN version_num ~ '^core_[0-9]+$' "
                "THEN substring(version_num FROM 6)::integer>=320 ELSE false END)"
            ).scalar()
        )
    if peers > 1:
        return
    for signature in (
        "mcp_install_json_projection(text,text,text,text[],boolean)",
        "mcp_sync_json_fields()",
        "mcp_write_json_fields(text,text,text,text[],boolean,jsonb)",
        "mcp_json_leaves(jsonb,text,text[])",
        "mcp_json_path_key(text)",
        "mcp_json_credential(text[])",
        "mcp_json_tokens(text)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
