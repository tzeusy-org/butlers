"""Finance overlay columns and lossless scalar JSON fields (RFC 0039 D2/D4).

Revision ID: finance_020
Revises: finance_019
"""

from alembic import op

revision = "finance_020"
down_revision = "finance_019"
branch_labels = None
depends_on = None

_OVERLAY = """
BEGIN
    NEW.display_merchant := COALESCE(NEW.metadata->>'normalized_merchant', NEW.merchant);
    NEW.display_category := COALESCE(NEW.metadata->>'inferred_category', NEW.category);
    RETURN NEW;
END
"""

_TABLES = (
    "accounts",
    "balance_snapshots",
    "bills",
    "budgets",
    "categories",
    "claim_match_bindings",
    "facts",
    "merchant_mappings",
    "obligation_ledger",
    "payment_destinations",
    "recurring_groups",
    "subscriptions",
    "transaction_corrections",
    "transactions",
)


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
    op.execute(f"""
        ALTER TABLE {quoted}.transactions ADD COLUMN display_merchant TEXT,
            ADD COLUMN display_category TEXT;
        CREATE FUNCTION {quoted}.mcp_finance_overlay() RETURNS trigger LANGUAGE plpgsql
            SET search_path = pg_catalog, pg_temp AS $body${_OVERLAY}$body$;
        CREATE TRIGGER trg_mcp_finance_overlay BEFORE INSERT OR UPDATE ON {quoted}.transactions
            FOR EACH ROW EXECUTE FUNCTION {quoted}.mcp_finance_overlay();
        UPDATE {quoted}.transactions SET
            display_merchant=COALESCE(metadata->>'normalized_merchant',merchant),
            display_category=COALESCE(metadata->>'inferred_category',category);
        CREATE TABLE IF NOT EXISTS {quoted}.json_fields_public (
            id TEXT PRIMARY KEY, source_table TEXT NOT NULL, row_id TEXT NOT NULL,
            path TEXT NOT NULL, value_text TEXT, value_numeric NUMERIC,
            value_type TEXT NOT NULL CHECK (value_type IN ('string','number','boolean','null')),
            UNIQUE(source_table,row_id,path)
        );
    """)
    # current_schema() inside a pinned function would be pg_catalog. Bind the
    # actual migration namespace explicitly, including legacy flat test schemas.
    body = r"""
        DECLARE
            source_schema text := $literal$;
            required_helpers text[] := ARRAY[
                'public.mcp_json_tokens(text)',
                'public.mcp_json_credential(text[])',
                'public.mcp_json_path_key(text)',
                'public.mcp_json_leaves(jsonb,text,text[])',
                'public.mcp_write_json_fields(text,text,text,text[],boolean,jsonb)',
                'public.mcp_sync_json_fields()',
                'public.mcp_install_json_projection(text,text,text,text[],boolean)'
            ];
            present_helpers integer;
            version_schema text;
            core_revision text;
            historical_core boolean := false;
            current_core boolean := false;
            unknown_core boolean := false;
            item record;
        BEGIN
            -- Historical authority callers deliberately combine an older core
            -- with today's independent domain chain. Only an entirely absent
            -- shared boundary may defer initial installation in that case.
            -- A completed memory callback must never report that as complete.
            SELECT count(*) INTO present_helpers FROM unnest(required_helpers) AS h(signature)
                WHERE to_regprocedure(signature) IS NOT NULL;
            IF present_helpers <> cardinality(required_helpers) THEN
                IF present_helpers <> 0 THEN
                    RAISE EXCEPTION 'projection shared helper catalog incomplete';
                END IF;
                -- Shared helpers belong to every canonical core registry. Only
                -- the caller's own/public history can grant initial deferral.
                FOR version_schema IN
                    SELECT n.nspname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.relname='alembic_version' AND c.relkind IN ('r','p')
                        AND left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
                LOOP
                    FOR core_revision IN EXECUTE format(
                        'SELECT version_num FROM %I.alembic_version',version_schema) LOOP
                        IF core_revision ~ '^core_[0-9]+$' THEN
                            historical_core := historical_core OR
                                (version_schema IN (source_schema,'public') AND
                                 substring(core_revision FROM 6)::numeric < 320);
                            current_core := current_core OR
                                substring(core_revision FROM 6)::numeric >= 320;
                        ELSIF starts_with(core_revision,'core_') THEN
                            unknown_core := true;
                        END IF;
                    END LOOP;
                END LOOP;
                IF historical_core AND NOT current_core AND NOT unknown_core
                    AND NOT require_facts THEN
                    RETURN;
                END IF;
                RAISE EXCEPTION 'projection shared core prerequisite missing';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgrelid='$schema$.transactions'::regclass
                AND tgname='trg_mcp_finance_overlay'
                AND tgfoid='$schema$.mcp_finance_overlay()'::regprocedure
                AND tgtype=23 AND tgenabled='O' AND tgqual IS NULL AND tgnargs=0
            ) THEN RAISE EXCEPTION 'finance overlay trigger definition drift'; END IF;
            IF (SELECT prosrc FROM pg_proc
                WHERE oid='$schema$.mcp_finance_overlay()'::regprocedure)
                IS DISTINCT FROM $expected$$overlay$$expected$ THEN
                RAISE EXCEPTION 'finance overlay function definition drift';
            END IF;
            FOR item IN SELECT * FROM (VALUES
                ('accounts','id',ARRAY['metadata']::text[]),
                ('balance_snapshots','id',ARRAY['metadata']::text[]),
                ('bills','id',ARRAY['metadata']::text[]),
                ('budgets','id',ARRAY[]::text[]),
                ('categories','id',ARRAY[]::text[]),
                ('claim_match_bindings','claim_id',ARRAY[]::text[]),
                ('facts','id',ARRAY['metadata','tags']::text[]),
                ('merchant_mappings','id',ARRAY['metadata']::text[]),
                ('obligation_ledger','id',ARRAY[]::text[]),
                ('payment_destinations','id',ARRAY[]::text[]),
                ('recurring_groups','id',ARRAY[]::text[]),
                ('subscriptions','id',ARRAY['metadata','field_claims']::text[]),
                ('transaction_corrections','id',ARRAY[]::text[]),
                ('transactions','id',ARRAY['metadata','raw_data']::text[])
            ) AS source(name,key_column,json_columns) LOOP
                IF item.name='facts' AND to_regclass(format('%I.facts',source_schema)) IS NULL THEN
                    IF require_facts THEN RAISE EXCEPTION 'finance facts missing after memory'; END IF;
                    CONTINUE;
                END IF;
                PERFORM public.mcp_install_json_projection(source_schema,item.name,
                    item.key_column,item.json_columns,item.name='facts');
            END LOOP;
        END
    """.replace("$literal$", "'" + schema.replace("'", "''") + "'")
    body = body.replace("$schema$", quoted).replace("$overlay$", _OVERLAY)
    op.execute(
        f"CREATE FUNCTION {quoted}.mcp_install_finance_projection(require_facts boolean) "
        "RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $body$"
        + body
        + "$body$"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {quoted}.mcp_install_finance_projection(boolean) FROM PUBLIC"
    )
    op.execute(f"SELECT {quoted}.mcp_install_finance_projection(false)")


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.exec_driver_sql("SELECT current_schema()").scalar_one()
    quote = bind.dialect.identifier_preparer.quote
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in _TABLES:
        if bind.exec_driver_sql(f"SELECT to_regclass('{quote(schema)}.{table}')").scalar():
            op.execute(f"DROP TRIGGER IF EXISTS trg_mcp_json_fields ON {quote(schema)}.{table}")
    op.execute(f"DROP FUNCTION {quote(schema)}.mcp_install_finance_projection(boolean)")
    op.execute(f"DROP TABLE {quote(schema)}.json_fields_public")
    op.execute(f"DROP TRIGGER trg_mcp_finance_overlay ON {quote(schema)}.transactions")
    op.execute(f"DROP FUNCTION {quote(schema)}.mcp_finance_overlay()")
    op.execute(
        f"ALTER TABLE {quote(schema)}.transactions DROP COLUMN display_merchant, "
        "DROP COLUMN display_category"
    )
