"""Unexposed, connection-free subscriber prerequisite for RFC0039 D3.

There is intentionally no CLI/operator entrypoint. The eventual owner-sql command,
matching deployment recipe and real public chart must ship together. This module
renders the same production program that the isolated lifecycle tests execute.
"""

from __future__ import annotations

from dataclasses import dataclass

from butlers.knowledge_read.manifest import (
    Manifest,
    ManifestError,
    Table,
    decode_applied_manifest,
    literal,
    qualified,
    quote,
)

# Also present verbatim in init-db.sql. Both execution paths must refuse before
# synchronizing publisher privileges; a source contract checks this equality.
PUBLISHER_SECURITY_SQL = r"""
IF NOT EXISTS (SELECT 1 FROM pg_authid WHERE
        rolname='butlers_projection_publisher'
              AND rolcanlogin AND rolreplication AND NOT rolinherit
              AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolbypassrls AND rolpassword IS NULL) THEN
    RAISE EXCEPTION 'public MCP publisher attributes/credential refused';
END IF;
IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r
           ON r.oid IN (m.roleid, m.member)
           WHERE r.rolname='butlers_projection_publisher') THEN
    RAISE EXCEPTION 'public MCP publisher membership refused';
END IF;
IF EXISTS (SELECT 1 FROM pg_hba_file_rules WHERE error IS NOT NULL)
   OR EXISTS (SELECT 1 FROM pg_ident_file_mappings WHERE error IS NOT
        NULL) THEN
    RAISE EXCEPTION 'public MCP authentication configuration refused';
END IF;
IF (SELECT count(*) FROM pg_ident_file_mappings WHERE
        map_name='local')<>2
   OR NOT EXISTS (SELECT 1 FROM pg_ident_file_mappings WHERE
        map_name='local'
                  AND sys_name='postgres' AND pg_username='postgres')
   OR NOT EXISTS (SELECT 1 FROM pg_ident_file_mappings WHERE
        map_name='local'
                  AND sys_name='postgres' AND
        pg_username='butlers_projection_publisher')
        THEN
    RAISE EXCEPTION 'public MCP local peer map refused';
END IF;
IF (SELECT count(*) FROM pg_hba_file_rules WHERE type='local'
    AND database=ARRAY['all'] AND user_name=ARRAY['all']
    AND auth_method='peer' AND options=ARRAY['map=local'])<>1 THEN
    RAISE EXCEPTION 'public MCP local peer rule refused';
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_hba_file_rules WHERE type='host'
               AND database=ARRAY['all'] AND
        user_name=ARRAY['butlers_projection_publisher']
               AND address='all' AND auth_method='reject') THEN
    RAISE EXCEPTION 'public MCP publisher TCP rejection missing';
END IF;
IF EXISTS (
    SELECT 1 FROM pg_hba_file_rules h
    WHERE (h.user_name && ARRAY['all', 'butlers_projection_publisher']
           OR EXISTS (SELECT 1 FROM unnest(h.user_name) u WHERE u LIKE
        '+%' OR u LIKE '/%'))
      AND (
        'replication'=ANY(h.database)
        OR (h.type='local' AND NOT (h.database=ARRAY['all']
              AND h.user_name=ARRAY['all'] AND h.auth_method='peer'
              AND h.options=ARRAY['map=local']))
        OR (h.type<>'local' AND h.auth_method<>'reject'
            AND h.rule_number < (SELECT min(rule_number) FROM
        pg_hba_file_rules
                 WHERE type='host' AND database=ARRAY['all']
                   AND user_name=ARRAY['butlers_projection_publisher']
                   AND address='all' AND auth_method='reject'))
      )
) THEN
    RAISE EXCEPTION
        'public MCP publisher alternate authentication refused';
END IF;
""".strip()

PUBLISHER_GRANTS_SQL = r"""
GRANT CONNECT ON DATABASE butlers TO butlers_projection_publisher;
FOR item IN SELECT nspname FROM pg_namespace
    WHERE left(nspname, 3)<>'pg_' AND nspname<>'information_schema' LOOP
    EXECUTE
        format('REVOKE ALL ON SCHEMA %I FROM butlers_projection_publisher',
        item.nspname);
    EXECUTE

        format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM butlers_projection_publisher',
        item.nspname);
END LOOP;
FOR item IN SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace
        n ON
        n.oid=c.relnamespace
    WHERE left(n.nspname, 3)<>'pg_' AND n.nspname<>'information_schema'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f') LOOP
    EXECUTE
        format('REVOKE ALL ON TABLE %I.%I FROM butlers_projection_publisher',
                   item.nspname, item.relname);
    FOR attribute IN SELECT attname FROM pg_attribute
        WHERE attrelid=format('%I.%I', item.nspname,
        item.relname)::regclass
          AND attnum>0 AND NOT attisdropped LOOP
        EXECUTE
        format('REVOKE ALL (%I) ON TABLE %I.%I FROM butlers_projection_publisher',

                       attribute.attname, item.nspname, item.relname);
    END LOOP;
END LOOP;
FOR item IN SELECT DISTINCT schemaname FROM pg_publication_tables
             WHERE pubname='butlers_public_v1' LOOP
    EXECUTE

        format('GRANT USAGE ON SCHEMA %I TO butlers_projection_publisher',
        item.schemaname);
END LOOP;
FOR item IN SELECT schemaname, tablename FROM pg_publication_tables
             WHERE pubname='butlers_public_v1' LOOP
    EXECUTE
        format('GRANT SELECT ON TABLE %I.%I TO butlers_projection_publisher',
                   item.schemaname, item.tablename);
END LOOP;
""".strip()


_ENTITY_OBJECT_EXPRESSION = (
    "CASE WHEN object_kind='entity' AND object ~ "
    "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "
    "THEN object::uuid END"
)


@dataclass(frozen=True)
class LocalColumn:
    name: str
    type: str
    expression: str


@dataclass(frozen=True)
class ReadField:
    name: str
    type: str
    expression: str


@dataclass(frozen=True)
class SubscriberTable:
    """The closed effective catalog; local extras never enter
    publication/digest."""

    table: Table
    view: str
    extras: tuple[LocalColumn, ...]
    view_where: str
    read_fields: tuple[ReadField, ...]


def subscriber_plan(manifest: Manifest) -> tuple[SubscriberTable, ...]:
    # Validate callers' immutable objects through the same closed stored format.
    decode_applied_manifest(manifest.canonical)
    result = []
    for table in manifest.tables:
        extras = ()
        if table.source == "relationship.entity_facts" and {"object", "object_kind"} <= set(
            table.columns
        ):
            extras = (
                LocalColumn(
                    "object_entity_id",
                    "uuid",
                    _ENTITY_OBJECT_EXPRESSION,
                ),
            )
        view = table.source.replace(".", "__")
        if (
            len(view) > 63
            or any(x.view == view for x in result)
            or any(c.startswith("_mcp_retired_") for c in table.columns)
        ):
            raise ManifestError("subscriber view name collision/length")
        where = "deleted_at IS NULL" if table.source == "finance.transactions" else ""
        excluded = {
            "public.entities": {"deleted", "unidentified", "placeholder_name"},
            "public.entity_graph_edges": {"publish_key"},
        }.get(table.source, set())
        fields = tuple(
            ReadField(
                c,
                "text" if typ.startswith("numeric") else typ,
                quote(c) + ("::text" if typ.startswith("numeric") else ""),
            )
            for c, typ in table.types
            if c not in excluded
        )
        fields += tuple(ReadField(e.name, e.type, quote(e.name)) for e in extras)
        if table.source == "public.entity_graph_edges":
            domains = {
                t.source: t.domain
                for t in manifest.tables
                if t.source.endswith((".facts", ".entity_facts"))
                and t.domain not in {"finance", "health"}
            }
            expression = (
                "CASE split_part(publish_key, ':', 1) "
                + " ".join(
                    f"WHEN {literal(src)} THEN {literal(domain)}"
                    for src, domain in sorted(domains.items())
                )
                + " ELSE NULL END"
            )
            fields += (ReadField("source_domain", "text", expression),)
        result.append(SubscriberTable(table, view, extras, where, fields))
    return tuple(result)


def _do(body: str, *, declare: str = "") -> str:
    return (
        "DO $mcp$\nDECLARE item record; attribute record; deadline timestamptz;"
        + declare
        + "\nBEGIN\n"
        + body
        + "\nEND\n$mcp$;\n"
    )


def _raise_unless(predicate: str, message: str) -> str:
    return (
        f"IF ({predicate}) IS DISTINCT FROM true THEN RAISE EXCEPTION {literal(message)}; END IF;\n"
    )


def _filter_deparsed(table: Table) -> str:
    # Only the closed manifest equality/IN grammar. PostgreSQL uses scalar equality
    # for singleton IN and ScalarArrayOpExpr for multiple values.
    pairs = []
    for column, values in table.filters:
        value_sql = [literal(v) + ("::text" if isinstance(v, str) else "") for v in values]
        pairs.append(
            f"{column} = "
            + (value_sql[0] if len(values) == 1 else "ANY (ARRAY[" + ", ".join(value_sql) + "])")
        )
    return " AND ".join(pairs)


def _publication_guard(manifest: Manifest) -> str:
    expected = []
    for table in manifest.tables:
        expected.append(
            "("
            + ", ".join(
                (
                    literal(table.source),
                    "ARRAY[" + ", ".join(literal(c) for c in sorted(table.columns)) + "]::text[]",
                    literal(_filter_deparsed(table)),
                )
            )
            + ")"
        )
    return (
        _raise_unless(
            """EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user AND rolsuper)""",
            "public MCP owner superuser required",
        )
        + _raise_unless(
            f"""(SELECT array_agg(digest) FROM
        mcp_surface.manifest_state)=ARRAY[{literal(manifest.digest)}]""",
            "public MCP primary manifest mismatch",
        )
        + _raise_unless(
            """
        EXISTS (SELECT 1 FROM pg_publication p WHERE
        pubname='butlers_public_v1'
          AND NOT puballtables AND NOT pubviaroot AND pubinsert AND
        pubupdate AND pubdelete
          AND pubtruncate AND NOT EXISTS (SELECT 1 FROM
        pg_publication_namespace WHERE
        pnpubid=p.oid))
        """,
            "public MCP publication actions/wildcard mismatch",
        )
        + _raise_unless(
            """
        NOT EXISTS (
          WITH expected(source,columns,filter) AS (VALUES """
            + ",\n".join(expected)
            + """),
          actual AS (
            SELECT n.nspname||'.'||c.relname AS source,
              ARRAY(SELECT a.attname::text FROM unnest(pr.prattrs) WITH
        ORDINALITY k(n, p)
                  JOIN pg_attribute a ON a.attrelid=pr.prrelid AND
        a.attnum=k.n ORDER BY
        a.attname) AS columns,
              COALESCE(pg_get_expr(pr.prqual, pr.prrelid), '') AS filter,

              pr.prattrs IS NOT NULL AS explicit
            FROM pg_publication_rel pr JOIN pg_publication p ON
        p.oid=pr.prpubid
            JOIN pg_class c ON c.oid=pr.prrelid JOIN pg_namespace n ON
        n.oid=c.relnamespace
            WHERE p.pubname='butlers_public_v1'
          )
          SELECT 1 FROM expected e FULL JOIN actual a USING(source)
          WHERE e.source IS NULL OR a.source IS NULL OR NOT a.explicit
            OR e.columns<>a.columns
            OR
        pg_temp.mcp_filter_tokens(a.filter)<>pg_temp.mcp_filter_tokens(e.filter)
        )""",
            "public MCP publication membership/columns/filter mismatch",
        )
    )


def _primary_catalog_guard(manifest: Manifest) -> str:
    body = ""
    owner = """(SELECT pubowner FROM pg_publication WHERE pubname='butlers_public_v1')"""
    for table in manifest.tables:
        rel = literal(table.source)
        body += _raise_unless(
            f"""EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass({rel}) AND
        relkind='r' AND
        relowner={owner})""",
            "primary source owner/shape mismatch",
        )
        for col, typ in table.types:
            body += _raise_unless(
                f"""EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid={rel}::regclass AND
        attname={literal(col)}
        AND attnum>0 AND NOT attisdropped AND attgenerated='' AND
        format_type(atttypid, atttypmod)={literal(typ)}"""
                + (" AND attnotnull" if col in table.identity else "")
                + ")",
                "primary source column/type/identity drift",
            )
        for primary, expected in ((True, table.key), (False, table.identity)):
            attrs = "ARRAY[" + ", ".join(literal(c) for c in expected) + "]::text[]"
            where = (
                "i.indisprimary"
                if primary
                else (
                    "((c.relreplident='d' AND i.indisprimary) OR "
                    "(c.relreplident='i' AND i.indisreplident))"
                )
            )
            body += _raise_unless(
                f"""EXISTS (SELECT 1 FROM pg_class c JOIN pg_index i ON i.indrelid=c.oid
        WHERE
        c.oid={rel}::regclass AND {where} AND i.indisunique AND
        i.indisvalid AND i.indimmediate AND
        i.indpred IS NULL AND i.indexprs IS NULL AND ARRAY(SELECT
        a.attname::text FROM
        unnest(i.indkey) WITH ORDINALITY k(n, p) JOIN pg_attribute a ON
        a.attrelid=c.oid AND
        a.attnum=k.n ORDER BY k.p)={attrs})""",
                "primary source key/replica identity drift",
            )
        if table.domain not in {"finance", "health"}:
            for col in ("sensitivity", "privacy", "effective_privacy"):
                values = dict(table.filters).get(col, ())
                safe = {"normal", "sensitive"} if col == "effective_privacy" else {"normal"}
                if not values or not set(values) <= safe:
                    body += _raise_unless(
                        f"""NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid={rel}::regclass AND
        attname={literal(col)} AND attnum>0 AND NOT attisdropped)""",
                        "primary privacy catalog drift",
                    )
        predicates = dict(table.filters).get("predicate", ())
        if table.source == "relationship.entity_facts":
            predicate_array = "ARRAY[" + ", ".join(literal(v) for v in predicates) + "]::text[]"
            body += _raise_unless(
                f"""NOT EXISTS(SELECT 1 FROM unnest({predicate_array}) p WHERE NOT
        EXISTS(SELECT 1 FROM
        relationship.entity_predicate_registry WHERE predicate=p AND
        kind='relational'))""",
                "primary relationship predicate drift",
            )
        elif table.source.endswith(".facts") and table.domain not in {"finance", "health"}:
            predicate_array = "ARRAY[" + ", ".join(literal(v) for v in predicates) + "]::text[]"
            schema = quote(table.source.split(".")[0])
            body += _raise_unless(
                f"""NOT EXISTS(SELECT 1 FROM unnest({predicate_array}) p WHERE NOT
        EXISTS(SELECT 1 FROM
        {schema}.predicate_registry WHERE name=p))""",
                "primary fact predicate drift",
            )
    return body


_FILTER_PATTERN = (
    r"^('(''|[^'])*'|"
    r'"[a-z_][a-z0-9_]*"|[a-zA-Z_][a-zA-Z_0-9]*|::|[=(),\[\]])'
)

_FILTER_TOKENS_SQL = r"""
CREATE OR REPLACE FUNCTION pg_temp.mcp_filter_tokens(expression text) RETURNS text[]
LANGUAGE plpgsql AS $tokens$
DECLARE tail text:=expression; token text; result text[]:=ARRAY[]::text[];
BEGIN
    WHILE length(tail)>0 LOOP
        token:=substring(tail FROM '^[[:space:]]+');
        IF token IS NOT NULL THEN tail:=substring(tail FROM length(token)+1); CONTINUE; END IF;
        token:=substring(tail FROM __FILTER_PATTERN__);
        IF token IS NULL THEN RAISE EXCEPTION 'publication filter grammar refused'; END IF;
        tail:=substring(tail FROM length(token)+1);
        IF token='::' THEN
            token:=substring(tail FROM '^[[:space:]]*text');
            IF token IS NULL THEN RAISE EXCEPTION 'publication filter cast refused'; END IF;
            tail:=substring(tail FROM length(token)+1);
        ELSIF token NOT IN ('(',')','"') THEN
            result:=array_append(result,CASE WHEN ascii(left(token,1))=39 THEN token
                ELSE lower(replace(token,'"','')) END);
        END IF;
    END LOOP;
    RETURN result;
END $tokens$;
""".replace("__FILTER_PATTERN__", literal(_FILTER_PATTERN))


def _view_sql(plan: SubscriberTable, manifest: Manifest) -> str:
    table = plan.table
    pairs = [part for field in plan.read_fields for part in (literal(field.name), field.expression)]
    keys = [quote(c) + "::text" for c in table.key]
    key = keys[0] if len(keys) == 1 else "jsonb_build_array(" + ", ".join(keys) + ")::text"
    sql = f"""SELECT {key} AS source_id, jsonb_build_object({", ".join(pairs)}) AS
        fields FROM
        {qualified(table.source)}"""
    return sql + (" WHERE " + plan.view_where if plan.view_where else "")


def _subscriber_ddl(plans: tuple[SubscriberTable, ...], manifest: Manifest) -> str:
    body = ""
    for schema in sorted(
        {p.table.source.split(".")[0] for p in plans} | {"mcp_local", "mcp_read", "mcp_inbox"}
    ):
        if schema == "public":
            body += """IF (SELECT nspowner FROM pg_namespace WHERE
        nspname='public')='pg_database_owner'::regrole
        THEN ALTER SCHEMA public OWNER TO mcp_projection_owner; END IF;\n"""
        body += f"""CREATE SCHEMA IF NOT EXISTS {quote(schema)} AUTHORIZATION
        mcp_projection_owner;\n"""
        body += _raise_unless(
            f"""(SELECT nspowner FROM pg_namespace WHERE
        nspname={literal(schema)})='mcp_projection_owner'::regrole""",
            "subscriber schema owner mismatch",
        )
    body += """
CREATE TABLE IF NOT EXISTS mcp_local.manifest_applied (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'), canonical text
        NOT NULL,
    applied_at timestamp with time zone NOT NULL DEFAULT
        clock_timestamp()
);
"""
    # An entry can disappear only after the old stored declaration was decoded.
    allowed = "ARRAY[" + ", ".join(literal(p.table.source) for p in plans) + "]::text[]"
    body += f"""FOR item IN SELECT value AS source FROM jsonb_array_elements_text(
        current_setting('butlers.e_cleanup_sources')::jsonb)
        WHERE NOT value=ANY({allowed}) LOOP
        EXECUTE format('DROP VIEW IF EXISTS mcp_read.%I',
        replace(item.source, '.', '__'));
        IF to_regclass(item.source) IS NOT NULL THEN
            IF (SELECT relowner FROM pg_class WHERE
        oid=to_regclass(item.source))<>
                'mcp_projection_owner'::regrole THEN RAISE EXCEPTION
        'removed subscriber owner refused'; END IF;
            EXECUTE format('DROP TABLE %s', item.source::regclass);
        END IF;
    END LOOP;\n"""
    for plan in plans:
        table = plan.table
        columns = ", ".join(
            quote(c) + " " + typ + (" NOT NULL" if c in table.identity else "")
            for c, typ in table.types
        )
        columns += ", PRIMARY KEY (" + ", ".join(quote(c) for c in table.key) + ")"
        for extra in plan.extras:
            columns += (
                f", {quote(extra.name)} {extra.type} GENERATED ALWAYS AS "
                f"({extra.expression}) STORED"
            )
        body += f"CREATE TEMP TABLE mcp_expected_table ({columns});\n"
        body += f"CREATE TABLE IF NOT EXISTS {qualified(table.source)} ({columns});\n"
        body += _raise_unless(
            f"""(SELECT relkind='r' AND relowner='mcp_projection_owner'::regrole FROM
        pg_class WHERE
        oid={literal(table.source)}::regclass)""",
            "subscriber relation owner/shape mismatch",
        )
        # Stage changed base/generated columns under their desired names. Renaming
        # the old column retains dependencies until the fixed-shape view is replaced.
        body += f"""FOR attribute IN
            SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type,
                   a.attgenerated, a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS expression
            FROM pg_attribute a
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid='pg_temp.mcp_expected_table'::regclass
              AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY a.attnum LOOP
            SELECT a.attnum, format_type(a.atttypid, a.atttypmod) AS type,
                   a.attgenerated, pg_get_expr(d.adbin, d.adrelid) AS expression INTO item
            FROM pg_attribute a
            LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid={literal(table.source)}::regclass
              AND a.attname=attribute.attname AND a.attnum>0 AND NOT a.attisdropped;
            IF item.attnum IS NOT NULL AND
               (item.type<>attribute.type OR item.attgenerated<>attribute.attgenerated
                OR (attribute.attgenerated<>'' AND
                    item.expression IS DISTINCT FROM attribute.expression)) THEN
                EXECUTE format(
                    'ALTER TABLE {qualified(table.source)} RENAME COLUMN %I TO %I',
                    attribute.attname, '_mcp_retired_'||item.attnum);
                item.attnum:=NULL;
            END IF;
            IF item.attnum IS NULL THEN
                EXECUTE format(
                    'ALTER TABLE {qualified(table.source)} ADD COLUMN %I %s%s',
                    attribute.attname, attribute.type,
                    CASE WHEN attribute.attgenerated<>'' THEN
                        ' GENERATED ALWAYS AS ('||attribute.expression||') STORED'
                        ELSE '' END);
            END IF;
        END LOOP;
"""
        body += _conditional_view("mcp_read." + plan.view, _view_sql(plan, manifest))
        # Retire generated dependents before their inputs; never CASCADE a view.
        expected = (
            "ARRAY["
            + ", ".join(literal(c) for c in (*table.columns, *(x.name for x in plan.extras)))
            + "]::text[]"
        )
        body += f"""FOR attribute IN
            SELECT attname FROM pg_attribute
            WHERE attrelid={literal(table.source)}::regclass
              AND attnum>0 AND NOT attisdropped AND NOT attname=ANY({expected})
            ORDER BY attgenerated DESC, attnum DESC LOOP
            EXECUTE format('ALTER TABLE {qualified(table.source)} DROP COLUMN %I',
                           attribute.attname);
        END LOOP;
"""
        key_array = "ARRAY[" + ", ".join(literal(c) for c in table.key) + "]::text[]"
        body += f"""IF (SELECT ARRAY(
                   SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n, p)
                   JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n
                   ORDER BY k.p)
               FROM pg_constraint c
               WHERE c.conrelid={literal(table.source)}::regclass AND c.contype='p')
               IS DISTINCT FROM {key_array} THEN
            FOR item IN SELECT conname FROM pg_constraint
                WHERE conrelid={literal(table.source)}::regclass AND contype='p' LOOP
                EXECUTE format('ALTER TABLE {qualified(table.source)} DROP CONSTRAINT %I',
                               item.conname);
            END LOOP;
            ALTER TABLE {qualified(table.source)}
                ADD PRIMARY KEY ({", ".join(quote(c) for c in table.key)});
        END IF;
        FOR attribute IN SELECT attname, attnotnull FROM pg_attribute
            WHERE attrelid='pg_temp.mcp_expected_table'::regclass
              AND attnum>0 AND NOT attisdropped LOOP
            IF (SELECT attnotnull FROM pg_attribute
                WHERE attrelid={literal(table.source)}::regclass
                  AND attname=attribute.attname AND NOT attisdropped)
               <>attribute.attnotnull THEN
                EXECUTE format(
                    'ALTER TABLE {qualified(table.source)} ALTER COLUMN %I %s NOT NULL',
                    attribute.attname,
                    CASE WHEN attribute.attnotnull THEN 'SET' ELSE 'DROP' END);
            END IF;
        END LOOP;
"""
        # Check the complete effective catalog, not merely the manifest digest.
        shape = """SELECT
        jsonb_agg(jsonb_build_array(a.attname, format_type(a.atttypid,
        a.atttypmod), a.attnotnull, a.attgenerated, pg_get_expr(d.adbin,
        d.adrelid))
        ORDER BY a.attname) FROM pg_attribute a LEFT JOIN pg_attrdef d ON
        d.adrelid=a.attrelid AND
        d.adnum=a.attnum WHERE a.attrelid={rel}::regclass AND a.attnum>0
        AND NOT a.attisdropped"""
        body += _raise_unless(
            "("
            + shape.format(rel=literal(table.source))
            + ")=("
            + shape.format(rel="'pg_temp.mcp_expected_table'")
            + ")",
            "subscriber effective catalog readback mismatch",
        )
        body += "DROP TABLE pg_temp.mcp_expected_table;\n"
    return body


def _conditional_view(name: str, select: str) -> str:
    # PostgreSQL normalizes both definitions using the same deparser. This keeps
    # exact view OIDs on rerun while changing dependencies before column removal.
    return f"""CREATE TEMP VIEW mcp_expected_view AS {select};
IF to_regclass({literal(name)}) IS NULL OR
   pg_get_viewdef(to_regclass({literal(name)}),true)<>
   pg_get_viewdef('pg_temp.mcp_expected_view'::regclass,true) THEN
    CREATE OR REPLACE VIEW {qualified(name)} AS {select};
END IF;
DROP VIEW pg_temp.mcp_expected_view;
"""


def _applied_decoder_sql() -> str:
    """Closed normalized JSON validation at the psql runtime boundary.

    The renderer cannot call Python after connecting. These catalog-side
        checks
    validate the same version1 shape before any old source name drives
        cleanup.
    Differential lifecycle cases compare its result with
        decode_applied_manifest.
    """
    from butlers.knowledge_read.manifest import (
        _BREADTH,
        _CANONICAL_FIELDS,
        _CREDENTIALS,
        _PRIVATE_TABLES,
        _TYPE,
    )

    def token_pattern(rules):
        return (
            r"(^|[.[:space:]_-])("
            + "|".join("[.[:space:]_-]+".join(r) for r in rules)
            + r")([.[:space:]_-]|$)"
        )

    credentials = token_pattern(_CREDENTIALS)
    breadth = token_pattern(_BREADTH)
    private_predicates = token_pattern(
        (
            *_CREDENTIALS,
            ("identity", "document"),
            *(
                (x,)
                for x in ("phone", "email", "address", "handle", "website", "birthday", "passport")
            ),
        )
    )
    fields = "ARRAY[" + ", ".join(literal(x) for x in sorted(_CANONICAL_FIELDS)) + "]"
    private = "ARRAY[" + ", ".join(literal(x) for x in sorted(_PRIVATE_TABLES)) + "]"
    return (
        r"""
CREATE OR REPLACE FUNCTION pg_temp.mcp_canonical(v jsonb) RETURNS text
LANGUAGE plpgsql AS $canonical$
BEGIN
    CASE jsonb_typeof(v)
    WHEN 'object' THEN RETURN '{'||COALESCE((SELECT
        string_agg(to_json(k)::text||':'||
        pg_temp.mcp_canonical(val), ',' ORDER BY k COLLATE "C") FROM
        jsonb_each(v)
        x(k, val)), '')||'}';
    WHEN 'array' THEN RETURN '['||COALESCE((SELECT
        string_agg(pg_temp.mcp_canonical(val), ','
        ORDER BY n)
        FROM jsonb_array_elements(v) WITH ORDINALITY x(val, n)),
        '')||']';
    ELSE RETURN v::text;
    END CASE;
END $canonical$;
CREATE OR REPLACE FUNCTION pg_temp.mcp_decode_applied(doc text) RETURNS
        jsonb
LANGUAGE plpgsql AS $decode$
DECLARE d jsonb; t jsonb; pair jsonb; field text; col text; typ text;
        src text; domain text; previous text := ''; seen text[]; cols
        text[];
        filters jsonb; expected jsonb; i integer;
BEGIN
    IF doc IS NULL THEN RETURN NULL; END IF;
    IF octet_length(doc)>1048576 OR NOT (doc IS JSON OBJECT WITH UNIQUE
        KEYS) THEN
        RAISE EXCEPTION 'applied manifest JSON refused';
    END IF;
    d:=doc::jsonb;
    IF pg_temp.mcp_canonical(d)<>doc THEN
        RAISE EXCEPTION 'applied manifest is not canonical'; END IF;
    IF ARRAY(SELECT json_object_keys(doc::json))<>ARRAY['tables',
        'version']
       OR (d->'version')::text<>'1' OR jsonb_typeof(d->'tables')<>'array'
       OR jsonb_array_length(d->'tables')=0 THEN
        RAISE EXCEPTION 'applied manifest root refused';
    END IF;
    FOR t IN SELECT value FROM jsonb_array_elements(d->'tables') LOOP
        IF jsonb_typeof(t)<>'object'
           OR ARRAY(SELECT jsonb_object_keys(t) ORDER BY 1)<>__FIELDS__
        THEN
            RAISE EXCEPTION 'applied manifest fields refused';
        END IF;
        src:=t->>'source'; domain:=t->>'domain';
        IF jsonb_typeof(t->'source')<>'string' OR
        jsonb_typeof(t->'domain')<>'string' OR src
        !~ '^[a-z_][a-z0-9_]*[.][a-z_][a-z0-9_]*$'
           OR src COLLATE "C"<=previous OR split_part(src, '.',
        2)=ANY(__PRIVATE__)
           OR split_part(src, '.', 2) LIKE 'oauth_%' OR split_part(src,
        '.', 2) LIKE '%_tokens'
           OR domain IS DISTINCT FROM CASE
               WHEN src='relationship.external_fact_proposals' THEN
        'proposals'
               WHEN split_part(src, '.', 1) IN ('general', 'lifestyle',
        'education') THEN
        'knowledge'
               WHEN split_part(src, '.', 1) IN ('relationship', 'public')
        THEN 'relationship'
               WHEN split_part(src, '.', 1)='chronicler' THEN 'time'
               WHEN split_part(src, '.', 1)='mcp_surface' THEN ''
               WHEN split_part(src, '.', 1) IN ('travel', 'finance',
        'health') THEN
        split_part(src, '.', 1)
               ELSE NULL END THEN
            RAISE EXCEPTION 'applied manifest source/domain refused';
        END IF;
        previous:=src;
        FOREACH field IN ARRAY
        ARRAY['columns', 'key', 'identity', 'identifiers',
        'json_identifiers', 'tools'] LOOP
            IF jsonb_typeof(t->field)<>'array' OR EXISTS(
                SELECT 1 FROM jsonb_array_elements(t->field) x WHERE
        jsonb_typeof(x)<>'string')
               OR (SELECT count(*) FROM jsonb_array_elements(t->field))<>
                  (SELECT count(DISTINCT x) FROM
        jsonb_array_elements(t->field) x) THEN
                RAISE EXCEPTION 'applied manifest string list refused';
            END IF;
        END LOOP;
        cols:=ARRAY(SELECT jsonb_array_elements_text(t->'columns'));
        IF cardinality(cols)=0 OR jsonb_array_length(t->'key')=0
           OR jsonb_array_length(t->'identity')=0
           OR NOT t->'columns' @> t->'identity' OR NOT t->'identity' @>
        t->'key'
           OR NOT t->'columns' @> t->'identifiers'
           OR EXISTS(SELECT 1 FROM unnest(cols) c WHERE c !~
        '^[a-z_][a-z0-9_]*$'
               OR lower(regexp_replace(c, '([a-z0-9])([A-Z])', '\1_\2',
        'g')) ~

        __CREDENTIALS_EMBEDDING__)
        THEN
            RAISE EXCEPTION 'applied manifest columns/identity refused';
        END IF;
        IF jsonb_typeof(t->'types')<>'array' OR
        jsonb_array_length(t->'types')<>cardinality(cols) THEN
            RAISE EXCEPTION 'applied manifest types refused';
        END IF;
        i:=0;
        FOR pair IN SELECT value FROM jsonb_array_elements(t->'types')
        LOOP
            i:=i+1;
            IF jsonb_typeof(pair)<>'array' OR jsonb_array_length(pair)<>2
               OR jsonb_typeof(pair->0)<>'string' OR
        jsonb_typeof(pair->1)<>'string'
               OR pair->>0<>cols[i] OR pair->>1 !~ __TYPE__ THEN
                RAISE EXCEPTION
        'applied manifest normalized types refused';
            END IF;
        END LOOP;
        IF jsonb_typeof(t->'filters')<>'array' THEN
            RAISE EXCEPTION 'applied manifest filters refused'; END IF;
        seen:=ARRAY[]::text[]; previous:='';
        FOR pair IN SELECT value FROM jsonb_array_elements(t->'filters')
        LOOP
            IF jsonb_typeof(pair)<>'array' OR jsonb_array_length(pair)<>2
               OR jsonb_typeof(pair->0)<>'string' OR
        jsonb_typeof(pair->1)<>'array'
               OR jsonb_array_length(pair->1)=0 THEN
                RAISE EXCEPTION
        'applied manifest normalized filters refused'; END IF;
            col:=pair->>0;
            IF col COLLATE "C"<=previous OR NOT t->'identity' @>
        jsonb_build_array(col) THEN
                RAISE EXCEPTION
        'applied manifest filter identity refused'; END IF;
            previous:=col;
            SELECT x->>1 INTO typ FROM jsonb_array_elements(t->'types') x
        WHERE x->>0=col;
            IF typ NOT IN ('text', 'boolean') OR EXISTS(
               SELECT 1 FROM jsonb_array_elements(pair->1) x
               WHERE jsonb_typeof(x)<>CASE typ WHEN 'text' THEN 'string'
        ELSE 'boolean' END)
               OR pair->1 IS DISTINCT FROM (SELECT jsonb_agg(x ORDER BY
        (x#>>'{}') COLLATE
        "C")
                    FROM (SELECT DISTINCT value x FROM
        jsonb_array_elements(pair->1)) q) THEN
                RAISE EXCEPTION 'applied manifest filter values refused';
        END IF;
            seen:=array_append(seen, col);
        END LOOP;
        IF NOT (t->'time_column'='null'::jsonb OR
                (jsonb_typeof(t->'time_column')='string' AND t->'columns'
        @>
        jsonb_build_array(t->>'time_column')))
           OR jsonb_typeof(t->'uri')<>'string'
           OR (t->>'uri'<>'' AND t->>'uri' NOT LIKE 'butlers://%') THEN
            RAISE EXCEPTION 'applied manifest metadata refused'; END IF;
        SELECT COALESCE(jsonb_object_agg(x->>0, x->1), '{}') INTO filters
            FROM jsonb_array_elements(t->'filters') x;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(t->'tools') x
                  WHERE x !~ '^[a-z_][a-z0-9_]*$')
           OR EXISTS(SELECT 1 FROM unnest(cols) c WHERE domain NOT IN
        ('finance', 'health')
               AND c ~ __BREADTH__)
           OR EXISTS(SELECT 1 FROM
        jsonb_array_elements_text(t->'json_identifiers') x
               WHERE x !~ '^[a-zA-Z_][a-zA-Z_0-9]*([.][a-zA-Z_0-9-]+)+$'
                  OR lower(regexp_replace(x, '([a-z0-9])([A-Z])',
        '\1_\2', 'g')) ~
        __CREDENTIALS__)
           OR ((jsonb_array_length(t->'identifiers')>0 OR
        jsonb_array_length(t->'json_identifiers')>0)
               AND (domain NOT IN ('finance', 'health') OR
        split_part(src, '.', 2)='json_fields_public'))
           OR (domain IN ('finance', 'health') AND
        jsonb_array_length(t->'key')<>1) THEN
            RAISE EXCEPTION 'applied manifest marks/breadth/key refused';
        END IF;
        IF domain='proposals' AND
        (t->'columns'<>'["proposal_id","state","decided_at"]'
           OR

        t->'types'<>jsonb_build_array(jsonb_build_array('proposal_id','uuid'),
            jsonb_build_array('state','text'),
            jsonb_build_array('decided_at','timestamp with time zone'))
           OR t->'key'<>'["proposal_id"]' OR
        t->'identity'<>'["proposal_id"]'
           OR filters<>'{}' OR t->'identifiers'<>'[]' OR
        t->'json_identifiers'<>'[]') THEN
            RAISE EXCEPTION 'applied proposal status exposure refused';
        END IF;
        IF split_part(src, '.', 2)='facts' THEN
            IF domain IN ('finance', 'health') THEN
                IF filters<>'{"validity":["active"]}' THEN
                    RAISE EXCEPTION
        'applied domain fact filters refused'; END IF;
            ELSIF ARRAY(SELECT jsonb_object_keys(filters) ORDER BY 1)<>
                    ARRAY['predicate', 'sensitivity', 'validity']
                OR filters->'sensitivity'<>'["normal"]'
                OR filters->'validity'<>'["active","fading"]'
                OR EXISTS(SELECT 1 FROM
        jsonb_array_elements_text(filters->'predicate') x
                     WHERE x IN
        ('contact_phone', 'contact_email', 'birthday', 'lives_in',
        'nickname')
                       OR lower(regexp_replace(x, '([a-z0-9])([A-Z])',
        '\1_\2', 'g')) ~
        __PRIVATE_PREDICATES__) THEN
                RAISE EXCEPTION 'applied memory fact privacy refused';
        END IF;
        END IF;
        IF split_part(src, '.', 2)='entity_facts' AND
            (filters->'validity' IS DISTINCT FROM '["active"]'::jsonb
             OR COALESCE(jsonb_array_length(filters->'predicate'), 0)=0)
        THEN
            RAISE EXCEPTION
        'applied relationship predicate filter refused'; END IF;
        IF src='public.entities' AND filters<>

        '{"deleted":[false],"unidentified":[false],"placeholder_name":[false]}'
        THEN
            RAISE EXCEPTION 'applied entity privacy refused'; END IF;
        IF src='chronicler.episodes_public' AND filters<>

        '{"effective_privacy":["normal","sensitive"],"tombstoned":[false]}' THEN
            RAISE EXCEPTION 'applied episode privacy refused'; END IF;
        previous:=src;
    END LOOP;
    FOR t IN SELECT value FROM jsonb_array_elements(d->'tables') WHERE
        value->>'source'
        IN ('public.entity_graph_edges',
        'public.entity_graph_withheld_counts') LOOP
        IF t->>'source'='public.entity_graph_edges' THEN
            SELECT jsonb_build_array(jsonb_build_array('publish_key',
        jsonb_agg(k ORDER BY k
        COLLATE "C")),
                jsonb_build_array('sensitivity',
        jsonb_build_array('normal'))) INTO expected
            FROM (SELECT f->>'source'||':'||p AS k FROM
        jsonb_array_elements(d->'tables') f,
                LATERAL jsonb_array_elements(f->'filters') fl,
                LATERAL jsonb_array_elements_text(fl->1) p
                WHERE (f->>'source' LIKE '%.facts' OR f->>'source' LIKE
        '%.entity_facts')
                    AND f->>'domain' NOT IN ('finance', 'health') AND
        fl->>0='predicate') q;
        ELSE
            SELECT jsonb_build_array(jsonb_build_array('source_key',
        jsonb_agg(f->>'source'
                 ORDER BY f->>'source' COLLATE "C"))) INTO expected
            FROM jsonb_array_elements(d->'tables') f
            WHERE (f->>'source' LIKE '%.facts' OR f->>'source' LIKE
        '%.entity_facts')
                AND f->>'domain' NOT IN ('finance', 'health');
        END IF;
        IF expected IS NULL OR expected->0->1='null'::jsonb OR
        t->'filters'<>expected THEN
            RAISE EXCEPTION 'applied graph derivation refused'; END IF;
    END LOOP;
    RETURN d;
END
$decode$;
""".replace("__FIELDS__", fields)
        .replace("__PRIVATE__", private)
        .replace("__TYPE__", literal("^" + _TYPE.pattern))
        .replace("__BREADTH__", literal(breadth))
        .replace(
            "__CREDENTIALS_EMBEDDING__", literal(token_pattern((*_CREDENTIALS, ("embedding",))))
        )
        .replace("__CREDENTIALS__", literal(credentials))
        .replace("__PRIVATE_PREDICATES__", literal(private_predicates))
    )


def _subscriber_preflight() -> str:
    return _do("""
IF current_database()<>'butlers_public' OR NOT EXISTS(SELECT 1 FROM
        pg_catalog.pg_database
    WHERE datname=current_database() AND
        datdba='mcp_projection_owner'::regrole) THEN
    RAISE EXCEPTION 'projection database owner refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_subscription WHERE subdbid=(SELECT oid FROM
        pg_catalog.pg_database
    WHERE datname=current_database()) AND subname<>'butlers_public_sub')
        THEN
    RAISE EXCEPTION 'foreign subscriber refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_subscription WHERE
        subname='butlers_public_sub' AND
        subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database()) AND
    (subowner<>'mcp_subscription_owner'::regrole OR
        subpublications<>ARRAY['butlers_public_v1']
     OR subslotname IS DISTINCT FROM 'butlers_public_sub' OR
        subpasswordrequired
     OR
        subconninfo<>'host=/controller/run dbname=butlers user=butlers_projection_publisher'
     OR subbinary OR substream<>'f' OR subtwophasestate<>'d' OR
        subrunasowner)) THEN
    RAISE EXCEPTION 'existing subscription identity/options refused'; END
        IF;
IF EXISTS(SELECT 1 FROM pg_namespace WHERE nspname IN ('mcp_local',
        'mcp_read', 'mcp_inbox')
          AND nspowner<>'mcp_projection_owner'::regrole)
   OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON
        n.oid=c.relnamespace
             WHERE n.nspname IN ('mcp_local', 'mcp_read', 'mcp_inbox')
             AND c.relowner<>'mcp_projection_owner'::regrole) THEN
    RAISE EXCEPTION 'existing projection catalog owner refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.relkind IN ('r','p','v','m','f','S') AND
        ((n.nspname='mcp_local' AND c.relname NOT IN ('manifest_applied','rebuild_state'))
         OR (n.nspname='mcp_inbox' AND c.relname<>'proposals'))) THEN
    RAISE EXCEPTION 'undeclared local catalog relation refused'; END IF;
IF to_regclass('mcp_local.manifest_applied') IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM pg_class WHERE
        oid=to_regclass('mcp_local.manifest_applied') AND
        relkind='r'
        AND relowner='mcp_projection_owner'::regrole) THEN
    RAISE EXCEPTION 'applied record relation refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_namespace WHERE left(nspname, 3)<>'pg_'
    AND nspname<>'information_schema' AND
        nspowner<>'mcp_projection_owner'::regrole
    AND NOT (nspname='public' AND nspowner='pg_database_owner'::regrole))
        THEN
    RAISE EXCEPTION 'foreign projection schema owner refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON
        n.oid=c.relnamespace,
    LATERAL aclexplode(c.relacl) a WHERE left(n.nspname, 3)<>'pg_'
    AND n.nspname<>'information_schema' AND a.grantee NOT IN

        (0, 'mcp_projection_owner'::regrole, 'mcp_public_ro'::regrole,
        'mcp_proposals_reader'::regrole))
   OR EXISTS(SELECT 1 FROM pg_namespace n, LATERAL aclexplode(n.nspacl) a
    WHERE left(n.nspname, 3)<>'pg_' AND n.nspname<>'information_schema'
    AND a.grantee NOT IN (0, 'pg_database_owner'::regrole,
        'mcp_projection_owner'::regrole,
                          'mcp_public_ro'::regrole,
        'mcp_proposals_reader'::regrole))
   OR EXISTS(SELECT 1 FROM pg_attribute c JOIN pg_class r ON
        r.oid=c.attrelid
    JOIN pg_namespace n ON n.oid=r.relnamespace, LATERAL
        aclexplode(c.attacl) a
    WHERE left(n.nspname, 3)<>'pg_' AND n.nspname<>'information_schema'
    AND a.grantee NOT IN
        (0, 'mcp_projection_owner'::regrole, 'mcp_public_ro'::regrole,
        'mcp_proposals_reader'::regrole))
        THEN
    RAISE EXCEPTION 'foreign projection ACL refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_class c, LATERAL aclexplode(c.relacl) a
    WHERE c.oid=to_regclass('mcp_local.rebuild_state') AND
        a.grantee<>'mcp_projection_owner'::regrole)
   OR EXISTS(SELECT 1 FROM pg_attribute c, LATERAL aclexplode(c.attacl) a
    WHERE c.attrelid=to_regclass('mcp_local.rebuild_state') AND
        a.grantee<>'mcp_projection_owner'::regrole) THEN
    RAISE EXCEPTION 'rebuild journal ACL refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE left(n.nspname, 3)<>'pg_' AND n.nspname<>'information_schema'
        AND NOT t.tgisinternal
    AND NOT (n.nspname='mcp_inbox' AND c.relname='proposals' AND
        t.tgname='mcp_inbox_cap'
        AND t.tgfoid=to_regprocedure('mcp_inbox.cap_proposals()') AND
        t.tgtype=7
        AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0)) THEN
    RAISE EXCEPTION 'foreign projection trigger refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON
        n.oid=p.pronamespace
    WHERE left(n.nspname, 3)<>'pg_' AND n.nspname<>'information_schema'
    AND NOT (p.pronargs=0 AND p.prokind='f' AND
        p.proowner='mcp_projection_owner'::regrole
        AND ((n.nspname='mcp_read' AND p.proname='replication_as_of')
             OR (n.nspname='mcp_inbox' AND p.proname='cap_proposals'))))
        THEN
    RAISE EXCEPTION 'foreign projection function refused'; END IF;
IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON
        n.oid=c.relnamespace
    WHERE n.nspname='mcp_read' AND (c.relkind<>'v' OR c.reloptions IS NOT
        NULL)) THEN
    RAISE EXCEPTION 'read view kind/options refused'; END IF;
""")


def _identity_guard() -> str:
    return _raise_unless(
        """(SELECT datdba FROM pg_catalog.pg_database WHERE
        datname='butlers_public')='mcp_projection_owner'::regrole""",
        "projection database owner required",
    ) + _raise_unless(
        """
    (SELECT count(*) FROM pg_roles WHERE rolname IN
        ('mcp_projection_owner', 'mcp_public_ro',
      'mcp_proposals_reader', 'mcp_subscription_owner') AND NOT rolsuper
        AND NOT rolcreatedb
      AND NOT rolcreaterole AND NOT rolreplication AND NOT
        rolbypassrls)=4
    AND EXISTS(SELECT 1 FROM pg_roles WHERE
        rolname='mcp_projection_owner' AND NOT
        rolcanlogin)
    AND EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mcp_public_ro' AND
        rolcanlogin
        AND NOT rolinherit AND rolconnlimit=10 AND
        'statement_timeout=2s'=ANY(rolconfig))
    AND EXISTS(SELECT 1 FROM pg_roles WHERE
        rolname='mcp_proposals_reader' AND rolcanlogin)
    AND EXISTS(SELECT 1 FROM pg_roles WHERE
        rolname='mcp_subscription_owner' AND rolcanlogin)
    AND pg_has_role('mcp_subscription_owner', 'pg_create_subscription',
        'MEMBER')
    AND pg_has_role('mcp_subscription_owner', 'mcp_projection_owner',
        'MEMBER')
    AND NOT pg_has_role('mcp_projection_owner', 'pg_read_all_stats',
        'MEMBER')
    AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE
        m.member IN ('mcp_public_ro'::regrole,
        'mcp_proposals_reader'::regrole)
        OR m.roleid IN ('mcp_public_ro'::regrole,
        'mcp_proposals_reader'::regrole)
        OR (m.roleid='mcp_projection_owner'::regrole AND
        m.member<>'mcp_subscription_owner'::regrole))
    """,
        "public MCP managed identity attributes refused",
    )


def _prior_catalog_sql() -> str:
    body = """PERFORM set_config('butlers.e_catalog_drift', 'false', false);
    FOR item IN SELECT value AS source FROM jsonb_array_elements_text(
        current_setting('butlers.e_cleanup_sources')::jsonb) LOOP
        IF to_regclass(item.source) IS NOT NULL THEN
            IF NOT EXISTS(SELECT 1 FROM pg_class WHERE
        oid=to_regclass(item.source)
                AND relkind='r' AND
        relowner='mcp_projection_owner'::regrole
                AND NOT relrowsecurity AND NOT relforcerowsecurity)
               OR EXISTS(SELECT 1 FROM pg_constraint WHERE
        conrelid=to_regclass(item.source)
        AND contype<>'p')
               OR EXISTS(SELECT 1 FROM pg_index WHERE
        indrelid=to_regclass(item.source) AND
        NOT indisprimary) THEN
                RAISE EXCEPTION
        'subscriber unowned/undeclared relation policy refused'; END
        IF;
        END IF;
    END LOOP;
    FOR entry IN SELECT value FROM jsonb_array_elements(

        COALESCE(current_setting('butlers.e_old_canonical')::jsonb->'tables',
        '[]')) LOOP
        IF to_regclass(entry->>'source') IS NULL THEN
            PERFORM set_config('butlers.e_catalog_drift', 'true', false);
            CONTINUE;
        END IF;
        SELECT string_agg(format('%I %s%s', x->>0, x->>1,
            CASE WHEN entry->'identity' @> jsonb_build_array(x->>0) THEN
        ' NOT NULL' ELSE ''
        END), ',' ORDER BY n)
            INTO definition FROM jsonb_array_elements(entry->'types')
        WITH ORDINALITY p(x, n);
        definition:=definition||', PRIMARY KEY ('||
            (SELECT string_agg(format('%I', x), ',' ORDER BY n)
             FROM jsonb_array_elements_text(entry->'key') WITH ORDINALITY
        p(x, n))||')';
        IF entry->>'source'='relationship.entity_facts' AND
            entry->'columns' @> '["object","object_kind"]'::jsonb THEN

        definition:=definition||', object_entity_id uuid GENERATED ALWAYS AS ('||
                __EXPRESSION__||') STORED';
        END IF;
        EXECUTE
        'CREATE TEMP TABLE mcp_prior_expected ('||definition||')';
        SELECT jsonb_agg(jsonb_build_array(a.attname,
        format_type(a.atttypid, a.atttypmod),
            a.attnotnull, a.attgenerated, pg_get_expr(d.adbin,
        d.adrelid)) ORDER BY a.attname)
            INTO attrs FROM pg_attribute a LEFT JOIN pg_attrdef d ON
        d.adrelid=a.attrelid AND
        d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(entry->>'source') AND a.attnum>0
        AND NOT
        a.attisdropped;
        SELECT jsonb_agg(jsonb_build_array(a.attname,
        format_type(a.atttypid, a.atttypmod),
            a.attnotnull, a.attgenerated, pg_get_expr(d.adbin,
        d.adrelid)) ORDER BY a.attname)
            INTO expected_attrs FROM pg_attribute a LEFT JOIN pg_attrdef
        d ON
        d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid='pg_temp.mcp_prior_expected'::regclass AND
        a.attnum>0 AND NOT
        a.attisdropped;
        IF attrs IS DISTINCT FROM expected_attrs OR
            (SELECT to_jsonb(ARRAY(SELECT a.attname::text FROM
        unnest(c.conkey) WITH
        ORDINALITY k(n, p)
                JOIN pg_attribute a ON a.attrelid=c.conrelid AND
        a.attnum=k.n ORDER BY k.p))
             FROM pg_constraint c WHERE
        c.conrelid=to_regclass(entry->>'source') AND
        c.contype='p')
             IS DISTINCT FROM entry->'key' THEN
            PERFORM set_config('butlers.e_catalog_drift', 'true', false);
        END IF;
        DROP TABLE pg_temp.mcp_prior_expected;
    END LOOP;""".replace("__EXPRESSION__", literal(_ENTITY_OBJECT_EXPRESSION))
    return (
        _do(body, declare="entry jsonb; definition text; attrs jsonb; expected_attrs jsonb;")
        + """
SELECT current_setting('butlers.e_catalog_drift') AS e_catalog_drift
\\gset
"""
    )


def _transition_sql(target: Manifest) -> str:
    """SQL projection of the shared version1 entry comparison (differentially
    tested)."""
    return f"""
WITH old AS (SELECT value t FROM

        jsonb_array_elements(COALESCE(:'e_old_canonical'::jsonb->'tables',
        '[]'))),
new AS (SELECT value t FROM

        jsonb_array_elements({literal(target.canonical)}::jsonb->'tables'))
SELECT (:'e_old_missing'::boolean OR :'e_slot_lost'::boolean OR
        :'e_recovering'::boolean OR
        :'e_catalog_drift'::boolean OR EXISTS(
    SELECT 1 FROM old o LEFT JOIN new n ON n.t->>'source'=o.t->>'source'
    WHERE n.t IS NULL OR n.t<>o.t)) AS e_rebuild,
EXISTS(SELECT 1 FROM old o LEFT JOIN new n ON
        n.t->>'source'=o.t->>'source'
    WHERE n.t IS NULL OR NOT n.t->'columns' @> o.t->'columns'
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(n.t->'filters') nf
          WHERE NOT EXISTS(SELECT 1 FROM
        jsonb_array_elements(o.t->'filters') ofi
             WHERE ofi->>0=nf->>0 AND nf->1 @> ofi->1))) AS e_narrowing
\\gset
"""


def _managed_table(name: str, columns: str) -> str:
    shape = """SELECT
        jsonb_agg(jsonb_build_array(a.attname, format_type(a.atttypid,
        a.atttypmod), a.attnotnull, a.attgenerated, pg_get_expr(d.adbin,
        d.adrelid))
        ORDER BY a.attnum) FROM pg_attribute a LEFT JOIN pg_attrdef d ON
        d.adrelid=a.attrelid AND
        d.adnum=a.attnum WHERE a.attrelid={rel}::regclass AND a.attnum>0
        AND NOT a.attisdropped"""
    checks = """SELECT array_agg(pg_get_constraintdef(oid) ORDER BY
        pg_get_constraintdef(oid)) FROM
        pg_constraint WHERE conrelid={rel}::regclass AND convalidated AND
        NOT connoinherit"""
    body = f"""CREATE TEMP TABLE mcp_expected_table ({columns});\nCREATE TABLE IF NOT
        EXISTS
        {qualified(name)} ({columns});\n"""
    body += _raise_unless(
        f"""EXISTS(SELECT 1 FROM pg_class WHERE oid={literal(name)}::regclass AND
        relkind='r' AND
        relowner='mcp_projection_owner'::regrole AND NOT relrowsecurity
        AND NOT relforcerowsecurity)""",
        "local managed table owner/shape refused",
    )
    body += _raise_unless(
        "("
        + shape.format(rel=literal(name))
        + ")=("
        + shape.format(rel="'pg_temp.mcp_expected_table'")
        + ")",
        "local managed table columns/defaults refused",
    )
    body += _raise_unless(
        "("
        + checks.format(rel=literal(name))
        + ")=("
        + checks.format(rel="'pg_temp.mcp_expected_table'")
        + ")",
        "local managed table constraints refused",
    )
    body += "DROP TABLE pg_temp.mcp_expected_table;\n"
    return body


_APPLIED_COLUMNS = """singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'), canonical text
        NOT NULL,
    applied_at timestamp with time zone NOT NULL DEFAULT
        clock_timestamp()"""
_INBOX_COLUMNS = """proposal_id uuid PRIMARY KEY,
    sub text NOT NULL CHECK(length(sub) BETWEEN 1 AND 256),
    cid text NOT NULL CHECK(length(cid) BETWEEN 1 AND 256),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1
        AND 64),
    subject_entity_id uuid NOT NULL, predicate text NOT NULL
        CHECK(length(predicate) BETWEEN
        1 AND 128),
    object_entity_id uuid NOT NULL,
    evidence_url text CHECK(length(evidence_url)<=2048), note text
        CHECK(length(note)<=500),
    received_at timestamp with time zone NOT NULL DEFAULT
        clock_timestamp()"""


def _projection_bootstrap(manifest: Manifest) -> str:
    # The fixed metadata view compares READY relations to the APPLIED declaration,
    # never to a transient target literal, and never treats enabled alone as copied.
    metadata = """SELECT a.digest, a.canonical, a.applied_at,
        NOT EXISTS(SELECT 1 FROM mcp_local.rebuild_state) AND
        EXISTS (SELECT 1 FROM pg_catalog.pg_subscription WHERE
        subname='butlers_public_sub'
        AND subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database()) AND
        subenabled)
        AND (SELECT array_agg(n.nspname||'.'||c.relname ORDER BY
        n.nspname||'.'||c.relname)
             FROM pg_catalog.pg_subscription_rel r JOIN
        pg_catalog.pg_subscription s ON
        s.oid=r.srsubid
             JOIN pg_catalog.pg_class c ON c.oid=r.srrelid JOIN
        pg_catalog.pg_namespace n ON
        n.oid=c.relnamespace
             WHERE s.subname='butlers_public_sub' AND subdbid=(SELECT oid
        FROM
        pg_catalog.pg_database WHERE datname=current_database()) AND
        r.srsubstate='r')=
            (SELECT array_agg(t->>'source' ORDER BY t->>'source')
             FROM
        pg_catalog.jsonb_array_elements(a.canonical::jsonb->'tables') t)
        AND (SELECT count(*) FROM pg_catalog.pg_subscription_rel r
             JOIN pg_catalog.pg_subscription s ON s.oid=r.srsubid WHERE
        s.subname='butlers_public_sub' AND subdbid=(SELECT oid FROM
        pg_catalog.pg_database WHERE
        datname=current_database()))=
            pg_catalog.jsonb_array_length(a.canonical::jsonb->'tables')
        AS copy_complete
        FROM mcp_local.manifest_applied a"""
    sql = _do(
        _managed_table("mcp_local.manifest_applied", _APPLIED_COLUMNS)
        + _managed_table("mcp_inbox.proposals", _INBOX_COLUMNS)
    )
    sql += """
CREATE OR REPLACE FUNCTION mcp_inbox.cap_proposals() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, pg_temp AS
        $cap$
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(716793632804263790);
    IF NOT EXISTS (SELECT 1 FROM mcp_inbox.proposals WHERE
        proposal_id=NEW.proposal_id)
       AND (SELECT count(*) FROM mcp_inbox.proposals)>=1000 THEN
        RAISE EXCEPTION 'public MCP inbox full';
    END IF;
    RETURN NEW;
END $cap$;
CREATE OR REPLACE FUNCTION mcp_read.replication_as_of() RETURNS timestamp
        with time zone
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog, pg_temp
AS $fresh$ SELECT pg_catalog.max(latest_end_time) FROM
        pg_catalog.pg_stat_subscription stats JOIN pg_catalog.pg_subscription sub
        ON sub.oid=stats.subid WHERE sub.subname='butlers_public_sub' AND subdbid=(SELECT oid
        FROM
        pg_catalog.pg_database WHERE datname=current_database()) $fresh$;
"""
    sql += _do(
        """IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE
        tgrelid='mcp_inbox.proposals'::regclass
        AND NOT tgisinternal) THEN
        CREATE TRIGGER mcp_inbox_cap BEFORE INSERT ON mcp_inbox.proposals
            FOR EACH ROW EXECUTE FUNCTION mcp_inbox.cap_proposals();
    END IF;
    IF (SELECT count(*) FROM pg_trigger WHERE
        tgrelid='mcp_inbox.proposals'::regclass
        AND NOT tgisinternal)<>1 OR NOT EXISTS(SELECT 1 FROM pg_trigger
        WHERE tgrelid='mcp_inbox.proposals'::regclass AND
        tgname='mcp_inbox_cap'
          AND tgfoid='mcp_inbox.cap_proposals()'::regprocedure AND
        tgtype=7
          AND tgenabled='O' AND tgqual IS NULL AND tgnargs=0) THEN
        RAISE EXCEPTION 'inbox trigger shape refused'; END IF;
"""
        + _conditional_view("mcp_read.manifest_applied", metadata)
    )
    # Revoke first, across every subscriber schema, including old removed sources.
    sql += _do("""FOR item IN SELECT nspname FROM pg_namespace
        WHERE left(nspname, 3)<>'pg_' AND nspname<>'information_schema'
        LOOP
        EXECUTE

        format('REVOKE ALL ON SCHEMA %I FROM PUBLIC,mcp_public_ro,mcp_proposals_reader',
        item.nspname);
        EXECUTE

        format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM '
               'PUBLIC,mcp_public_ro,mcp_proposals_reader',
        item.nspname);
        EXECUTE

        format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM '
               'PUBLIC,mcp_public_ro,mcp_proposals_reader',
        item.nspname);
        EXECUTE

        format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM '
               'PUBLIC,mcp_public_ro,mcp_proposals_reader',
        item.nspname);
        FOR attribute IN SELECT c.relname, a.attname FROM pg_class c JOIN
        pg_namespace n ON
        n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid WHERE
        n.nspname=item.nspname
            AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND a.attnum>0 AND
        NOT a.attisdropped LOOP
            EXECUTE

        format('REVOKE ALL (%I) ON TABLE %I.%I FROM PUBLIC,mcp_public_ro,mcp_proposals_reader',

                attribute.attname, item.nspname, attribute.relname);
        END LOOP;
    END LOOP;""")
    sql += """REVOKE CONNECT, TEMPORARY ON DATABASE butlers_public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE butlers_public TO mcp_public_ro,
        mcp_proposals_reader;
GRANT USAGE ON SCHEMA mcp_read, mcp_inbox TO mcp_public_ro;
GRANT SELECT ON mcp_read.manifest_applied TO mcp_public_ro;
GRANT EXECUTE ON FUNCTION mcp_read.replication_as_of() TO mcp_public_ro;
GRANT
        INSERT(proposal_id, sub, cid, idempotency_key, subject_entity_id,
        predicate, object_entity_id, evidence_url, note)
    ON mcp_inbox.proposals TO mcp_public_ro;
GRANT USAGE ON SCHEMA mcp_inbox TO mcp_proposals_reader;
GRANT SELECT, DELETE ON mcp_inbox.proposals TO mcp_proposals_reader;
"""
    for plan in subscriber_plan(manifest):
        sql += f"""GRANT SELECT ON {qualified("mcp_read." + plan.view)} TO mcp_public_ro;\n"""
    sql += _do(
        _raise_unless(
            """(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON
        n.oid=p.pronamespace
        WHERE p.prosecdef AND left(n.nspname, 3)<>'pg_' AND
        n.nspname<>'information_schema')=2
        AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON
        n.oid=p.pronamespace
            WHERE p.prosecdef AND left(n.nspname, 3)<>'pg_' AND
        n.nspname<>'information_schema'
            AND (p.proconfig IS DISTINCT FROM
        ARRAY['search_path=pg_catalog, pg_temp']
                OR p.proowner<>'mcp_projection_owner'::regrole))""",
            "subscriber definer boundary refused",
        )
    )
    return sql


_JOURNAL_COLUMNS = """singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    database_oid oid NOT NULL,
    subscription_name text NOT NULL
        CHECK(subscription_name='butlers_public_sub'),
    attempt_id uuid NOT NULL DEFAULT gen_random_uuid(),
    target_digest text NOT NULL CHECK(target_digest ~ '^[a-f0-9]{64}$'),
    manifests text[] NOT NULL CHECK(cardinality(manifests) BETWEEN 1 AND
        16
        AND array_position(manifests, NULL) IS NULL),
    subscription_oids oid[] NOT NULL
        CHECK(cardinality(subscription_oids)<=16
        AND array_position(subscription_oids, 0::oid) IS NULL
        AND array_position(subscription_oids, NULL) IS NULL)"""


def _journal_read_sql(target: Manifest) -> str:
    shape = _do(
        "IF to_regclass('mcp_local.rebuild_state') IS NOT NULL THEN\n"
        + _managed_table("mcp_local.rebuild_state", _JOURNAL_COLUMNS)
        + "END IF;\n"
    )
    return (
        shape
        + _do("""PERFORM set_config('butlers.e_recovering', 'false', false);
    PERFORM set_config('butlers.e_manifests', '[]', false);
    PERFORM set_config('butlers.e_old_oids', '{}', false);
    IF to_regclass('mcp_local.rebuild_state') IS NOT NULL THEN
        IF (SELECT count(*) FROM mcp_local.rebuild_state)>1 THEN
            RAISE EXCEPTION 'rebuild journal cardinality refused'; END
        IF;
        IF EXISTS(SELECT 1 FROM mcp_local.rebuild_state) THEN
            IF current_setting('butlers.e_old_canonical')<>'null' THEN
                RAISE EXCEPTION
        'rebuild journal conflicts with applied completion'; END IF;
            IF EXISTS(SELECT 1 FROM mcp_local.rebuild_state j WHERE
                j.database_oid<>(SELECT oid FROM pg_catalog.pg_database
        WHERE
        datname=current_database())
                OR j.subscription_name<>'butlers_public_sub'
                OR NOT EXISTS(SELECT 1 FROM unnest(j.manifests) canonical
                    WHERE encode(sha256(convert_to(canonical, 'UTF8')),
        'hex')=j.target_digest)
                OR cardinality(j.manifests)<>(SELECT count(DISTINCT x)
        FROM
        unnest(j.manifests) x)
                OR cardinality(j.subscription_oids)<>(SELECT
        count(DISTINCT x) FROM
        unnest(j.subscription_oids) x)
                OR EXISTS(SELECT 1 FROM pg_subscription s WHERE
        s.oid=ANY(j.subscription_oids)
                    AND (s.subdbid<>j.database_oid OR
        s.subname<>j.subscription_name))) THEN
                RAISE EXCEPTION
        'rebuild journal identity/consistency refused'; END IF;
            FOR item IN SELECT unnest(manifests) AS canonical FROM
        mcp_local.rebuild_state
        LOOP
                PERFORM pg_temp.mcp_decode_applied(item.canonical);
            END LOOP;
            PERFORM set_config('butlers.e_manifests',
                (SELECT to_jsonb(manifests)::text FROM
        mcp_local.rebuild_state), false);
            PERFORM set_config('butlers.e_old_oids',
                (SELECT subscription_oids::text FROM
        mcp_local.rebuild_state), false);
            PERFORM set_config('butlers.e_recovering', 'true', false);
        END IF;
    END IF;""")
        + _do(f"""PERFORM set_config('butlers.e_manifests', (
        SELECT jsonb_agg(canonical ORDER BY canonical)::text FROM (
            SELECT DISTINCT canonical FROM (
                SELECT

        jsonb_array_elements_text(current_setting('butlers.e_manifests')::jsonb)
        AS canonical
                UNION ALL SELECT
        current_setting('butlers.e_old_canonical')
                    WHERE
        current_setting('butlers.e_old_canonical')<>'null'
                UNION ALL SELECT {literal(target.canonical)}
            ) attempts) unique_attempts), false);
    PERFORM set_config('butlers.e_old_oids', (
        SELECT ARRAY(SELECT DISTINCT oid FROM (
            SELECT unnest(current_setting('butlers.e_old_oids')::oid[])
        AS oid
            UNION ALL SELECT oid FROM pg_subscription WHERE
        subname='butlers_public_sub'
                AND subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database())
        ) candidates ORDER BY oid)::text), false);
    IF
        jsonb_array_length(current_setting('butlers.e_manifests')::jsonb)>16
        OR cardinality(current_setting('butlers.e_old_oids')::oid[])>16
        THEN
        RAISE EXCEPTION 'bounded rebuild journal exhausted'; END IF;
    PERFORM set_config('butlers.e_cleanup_sources', (
        SELECT jsonb_agg(source ORDER BY source)::text FROM (
            SELECT DISTINCT t->>'source' AS source
            FROM
        jsonb_array_elements_text(current_setting('butlers.e_manifests')::jsonb)
        a,
                 LATERAL jsonb_array_elements(a::jsonb->'tables') t
        ) sources), false);""")
        + """
SELECT current_setting('butlers.e_manifests') AS e_manifests,
       current_setting('butlers.e_old_oids') AS e_old_oids,
       current_setting('butlers.e_cleanup_sources') AS e_cleanup_sources,

       current_setting('butlers.e_recovering') AS e_recovering
\\gset
"""
    )


def _journal_write_sql() -> str:
    return (
        "SET ROLE mcp_projection_owner;\n"
        + _do(
            """CREATE SCHEMA IF NOT EXISTS mcp_local AUTHORIZATION
        mcp_projection_owner;\n"""
            + _managed_table("mcp_local.rebuild_state", _JOURNAL_COLUMNS)
        )
        + """BEGIN;
INSERT INTO
        mcp_local.rebuild_state(singleton, database_oid,
        subscription_name, target_digest, manifests, subscription_oids)
    VALUES(true, (SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database()),
        'butlers_public_sub', :'e_target_digest',
        ARRAY(SELECT jsonb_array_elements_text(:'e_manifests'::jsonb)),
        :'e_old_oids'::oid[])
    ON CONFLICT(singleton) DO UPDATE SET
        target_digest=EXCLUDED.target_digest,
        manifests=EXCLUDED.manifests,
        subscription_oids=EXCLUDED.subscription_oids;
"""
        + _do("""IF to_regclass('mcp_local.manifest_applied') IS NOT NULL THEN
    DELETE FROM mcp_local.manifest_applied; END IF;""")
        + "COMMIT;\nRESET ROLE;\n"
    )


def _owned_slots() -> str:
    return """(slot_name='butlers_public_sub' OR EXISTS(
        SELECT 1 FROM
        unnest(current_setting('butlers.e_old_oids')::oid[]) owned_oid
        WHERE slot_name ~
        ('^pg_'||owned_oid::text||'_sync_[0-9]+_[0-9]+$')))"""


def render_owner_program(manifest: Manifest) -> str:
    """Render only. No connection, operator surface, env read, clock or file
    write."""
    plans = subscriber_plan(manifest)
    guard = (
        _publication_guard(manifest)
        + _primary_catalog_guard(manifest)
        + PUBLISHER_SECURITY_SQL
        + _identity_guard()
        + _raise_unless(
            "pg_size_bytes(current_setting('max_slot_wal_keep_size'))>0",
            "public MCP finite slot WAL cap required",
        )
    )
    sql = "\\set ON_ERROR_STOP on\n\\set QUIET on\n\\connect butlers\n"
    sql += "SET lock_timeout='5s'; SET statement_timeout='60s';\n" + _FILTER_TOKENS_SQL + _do(guard)
    sql += """SELECT (NOT EXISTS(SELECT 1 FROM pg_replication_slots WHERE
        slot_name='butlers_public_sub')
           OR EXISTS(SELECT 1 FROM pg_replication_slots WHERE
        slot_name='butlers_public_sub'
           AND wal_status='lost')) AS e_slot_lost,
       COALESCE((SELECT slot_type='logical' AND plugin='pgoutput' AND
        database='butlers'
                 FROM pg_replication_slots WHERE
        slot_name='butlers_public_sub'), true) AS
        e_slot_valid
\\gset
"""
    # psql variables are not interpolated within dollar-quoted DO bodies.
    sql += (
        "\\if :e_slot_valid\n\\else\n"
        + _do("RAISE EXCEPTION 'public MCP slot identity refused';")
        + "\\endif\n"
    )
    sql += "\\connect butlers_public\nSET lock_timeout='5s'; SET statement_timeout='60s';\n"
    sql += _subscriber_preflight()
    sql += _applied_decoder_sql()
    sql += _do("""IF to_regclass('mcp_local.manifest_applied') IS NULL THEN
    PERFORM set_config('butlers.e_old_canonical', 'null', false);
ELSE
    IF (SELECT count(*) FROM mcp_local.manifest_applied)>1 THEN
        RAISE EXCEPTION 'applied manifest cardinality refused'; END IF;
    EXECUTE 'SELECT canonical FROM mcp_local.manifest_applied' INTO item;
    IF encode(sha256(convert_to(item.canonical, 'UTF8')), 'hex') IS
        DISTINCT FROM
       (SELECT digest FROM mcp_local.manifest_applied) THEN
        RAISE EXCEPTION 'applied manifest digest inconsistent'; END IF;
    PERFORM pg_temp.mcp_decode_applied(item.canonical);
    PERFORM set_config('butlers.e_old_canonical',
        COALESCE(item.canonical, 'null'), false);
END IF;""")
    sql += """SELECT current_setting('butlers.e_old_canonical') AS e_old_canonical,
       current_setting('butlers.e_old_canonical')='null' AS
        e_old_missing,
       COALESCE((SELECT oid::text FROM pg_subscription WHERE
        subname='butlers_public_sub' AND
        subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database())), '0') AS
        e_old_oid
\\gset
"""
    sql += f"SELECT {literal(manifest.digest)} AS e_target_digest\n\\gset\n"
    sql += _journal_read_sql(manifest)
    sql += _do("""IF EXISTS(SELECT 1 FROM pg_class c WHERE
        c.relnamespace=to_regnamespace('mcp_read')
        AND c.relname<>'manifest_applied' AND NOT EXISTS(SELECT 1 FROM

        jsonb_array_elements_text(current_setting('butlers.e_cleanup_sources')::jsonb)
        source
            WHERE c.relname=replace(source, '.', '__'))) THEN
        RAISE EXCEPTION 'undeclared read view refused'; END IF;""")
    sql += _prior_catalog_sql()
    sql += _transition_sql(manifest)
    sql += "\\if :e_rebuild\n"
    sql += _journal_write_sql()
    sql += """SELECT EXISTS(SELECT 1 FROM pg_subscription WHERE
        subname='butlers_public_sub' AND
        subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database())) AS
        e_has_sub
\\gset
\\if :e_has_sub
ALTER SUBSCRIPTION butlers_public_sub DISABLE;
ALTER SUBSCRIPTION butlers_public_sub SET (slot_name=NONE);
DROP SUBSCRIPTION butlers_public_sub;
\\endif
"""
    # Durable validated attempts retain exact old ownership across interruption.
    sql += """SELECT set_config('butlers.e_cleanup_sources', :'e_cleanup_sources',
        false);\n"""
    sql += _do("""FOR item IN SELECT value AS source FROM jsonb_array_elements_text(
        current_setting('butlers.e_cleanup_sources')::jsonb) LOOP
        IF to_regclass(item.source) IS NOT NULL THEN
            IF (SELECT relowner FROM pg_class WHERE
        oid=to_regclass(item.source))<>
               'mcp_projection_owner'::regrole THEN RAISE EXCEPTION
        'old subscriber owner refused'; END IF;
            EXECUTE format('TRUNCATE TABLE %s', item.source::regclass);
        END IF;
    END LOOP;""")
    sql += "\\connect butlers\n"
    sql += "SELECT set_config('butlers.e_old_oids', :'e_old_oids',false);\n"
    sql += _do(f"""IF EXISTS(SELECT 1 FROM pg_replication_slots WHERE {_owned_slots()}
        AND (slot_type<>'logical' OR plugin<>'pgoutput' OR
        database<>'butlers')) THEN
        RAISE EXCEPTION 'owned-name slot foreign identity refused'; END
        IF;
    deadline:=clock_timestamp()+interval '30 seconds';
    LOOP
        FOR item IN SELECT active_pid FROM pg_replication_slots WHERE
        {_owned_slots()} LOOP
            IF item.active_pid IS NOT NULL THEN PERFORM
        pg_terminate_backend(item.active_pid); END IF;
        END LOOP;
        EXIT WHEN NOT EXISTS(SELECT 1 FROM pg_replication_slots WHERE
        {_owned_slots()} AND
        active_pid IS NOT NULL);
        IF clock_timestamp()>deadline THEN RAISE EXCEPTION
        'owned slot release deadline'; END
        IF;
        PERFORM pg_sleep(0.05);
    END LOOP;
    FOR item IN SELECT slot_name FROM pg_replication_slots WHERE
        {_owned_slots()} LOOP
        PERFORM pg_drop_replication_slot(item.slot_name);
    END LOOP;""")
    sql += "\\endif\n\\connect butlers\n"
    sql += _do(
        """IF NOT EXISTS(SELECT 1 FROM pg_replication_slots WHERE
        slot_name='butlers_public_sub') THEN
    PERFORM pg_create_logical_replication_slot('butlers_public_sub',
        'pgoutput'); END IF;
"""
        + PUBLISHER_GRANTS_SQL
    )
    sql += "\\connect butlers_public\nSET ROLE mcp_projection_owner;\n"
    sql += """SELECT set_config('butlers.e_old_canonical', :'e_old_canonical',
        false);\n"""
    sql += """SELECT set_config('butlers.e_cleanup_sources', :'e_cleanup_sources',
        false);\n"""
    sql += _do(_subscriber_ddl(plans, manifest))
    sql += _do(_managed_table("mcp_local.rebuild_state", _JOURNAL_COLUMNS))
    sql += _projection_bootstrap(manifest)
    # Strip base-table access, including schemas inherited from old projections.
    for schema in sorted({p.table.source.split(".")[0] for p in plans}):
        sql += f"""REVOKE ALL ON SCHEMA {quote(schema)} FROM PUBLIC, mcp_public_ro,
        mcp_proposals_reader;\n"""
        sql += f"""REVOKE ALL ON ALL TABLES IN SCHEMA {quote(schema)} FROM
        PUBLIC,mcp_public_ro,mcp_proposals_reader;\n"""
    sql += "-- PUBLIC_MCP_SUBSCRIBER_DDL_COMPLETE\n"
    sql += "RESET ROLE;\n" + _do(
        _raise_unless(
            "EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND rolsuper)",
            "public MCP subscription superuser required",
        )
    )
    sql += """SELECT EXISTS(SELECT 1 FROM pg_subscription WHERE
        subname='butlers_public_sub' AND
        subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE
        datname=current_database())) AS
        e_has_sub
\\gset
\\if :e_has_sub
\\else
CREATE SUBSCRIPTION butlers_public_sub
CONNECTION
        'host=/controller/run dbname=butlers user=butlers_projection_publisher'
PUBLICATION butlers_public_v1 WITH (connect=false, create_slot=false,
        enabled=false,
    password_required=false, slot_name='butlers_public_sub');
\\endif
ALTER SUBSCRIPTION butlers_public_sub OWNER TO mcp_subscription_owner;
ALTER SUBSCRIPTION butlers_public_sub ENABLE;
ALTER SUBSCRIPTION butlers_public_sub REFRESH PUBLICATION;
"""
    sql += "SET ROLE mcp_projection_owner;\nBEGIN;\nDELETE FROM mcp_local.rebuild_state;\n"
    sql += f"""INSERT INTO mcp_local.manifest_applied(singleton, digest, canonical)
VALUES (true, {literal(manifest.digest)}, {literal(manifest.canonical)})
ON CONFLICT(singleton) DO UPDATE SET digest=EXCLUDED.digest,
        canonical=EXCLUDED.canonical,
    applied_at=clock_timestamp()
WHERE (mcp_local.manifest_applied.digest,
        mcp_local.manifest_applied.canonical)
      IS DISTINCT FROM (EXCLUDED.digest, EXCLUDED.canonical);
COMMIT;
RESET ROLE;
"""
    return sql.replace(
        "\\connect butlers\n",
        "\\connect butlers\nSET standard_conforming_strings=on; "
        "SET lock_timeout='5s'; SET statement_timeout='60s';\n",
    ).replace(
        "\\connect butlers_public\n",
        "\\connect butlers_public\nSET standard_conforming_strings=on; "
        "SET lock_timeout='5s'; SET statement_timeout='60s';\n",
    )
