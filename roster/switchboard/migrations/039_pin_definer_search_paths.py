"""Pin every Switchboard SECURITY DEFINER search path to ``pg_catalog, pg_temp``.

Revision ID: sw_039
Revises: sw_038
Create Date: 2026-09-29 00:00:00.000000

bu-mzm3su.3.  Two families still resolved names through a schema a
less-privileged role can ``CREATE`` in:

- The four partition-maintenance functions (sw_001/sw_002/sw_008/sw_009/sw_032)
  run with ``search_path = <switchboard schema>, pg_temp``.  The Switchboard
  runtime role holds ``CREATE`` on that schema, so it can plant
  ``<schema>.format(text, text)``; that overload beats the catalog's variadic
  ``format`` and runs as the migration owner.  Their bodies also depend on the
  path itself: ``current_schema()``, the unqualified ``PARTITION OF`` parent and
  the unqualified ``DROP TABLE`` target.  They are rewritten so the schema is
  resolved once here, at migration time, and baked in as a literal; nothing
  reads the path at run time any more.
- The registry/QA definers (sw_035/sw_038) carry ``pg_catalog, <schema>,
  pg_temp``, and ``ensure_registry_control`` carries ``pg_catalog, <schema>``
  with ``pg_temp`` implicit (searched first for relation and type names).  Their
  bodies already schema-qualify every relation, so they are ALTER only.

The heartbeat ensure-partition advisory-lock key is unchanged byte for byte:
the old body hashed ``current_schema()``, which under its path was the home
schema, and the new body hashes that same schema name as a literal.  Old and
new callers therefore still queue behind each other during a rolling deploy.

The ``public`` registry functions exist once per database, and their prior
path names whichever schema last ran sw_035 or sw_038.  Upgrade accepts any
single middle schema there; downgrade restores the current migration schema,
which is harmless because those bodies qualify every name.

Downgrade restores the prior partition bodies and every prior path.
"""

from __future__ import annotations

from alembic import op

revision = "sw_039"
down_revision = "sw_038"
branch_labels = None
depends_on = None

PINNED_SEARCH_PATH = "pg_catalog, pg_temp"

#: Registry/QA definers in ``public``: prior path is ``pg_catalog, <schema>, pg_temp``.
_PUBLIC_REGISTRY_SIGNATURES = (
    "public.register_butler_boot(text, uuid)",
    "public.reserve_butler_probe(text)",
    "public.record_butler_probe(text, bigint, bigint, boolean, boolean, boolean, text)",
    "public.set_butler_registry_policy(text, text)",
    "public.qa_local_schedule_policy()",
)

_PARTITION_SIGNATURES = (
    "switchboard_message_inbox_ensure_partition(timestamptz)",
    "switchboard_message_inbox_drop_expired_partitions(interval, timestamptz)",
    "switchboard_connector_heartbeat_log_ensure_partition(timestamptz)",
    "switchboard_connector_heartbeat_log_drop_expired_partitions(interval, timestamptz)",
)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _schema() -> str:
    return str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())


def _search_path(signature: str) -> str | None:
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT (SELECT substr(setting, length('search_path=') + 1) "
            "FROM unnest(proconfig) AS setting WHERE setting LIKE 'search_path=%%') "
            "FROM pg_catalog.pg_proc WHERE oid = to_regprocedure(%(sig)s)",
            {"sig": signature},
        )
        .one_or_none()
    )
    return None if row is None else row[0]


def _exists(signature: str) -> bool:
    return bool(
        op.get_bind()
        .exec_driver_sql("SELECT to_regprocedure(%(sig)s) IS NOT NULL", {"sig": signature})
        .scalar_one()
    )


def _path_schemas(path: str | None) -> list[str]:
    if not path:
        return []
    return [part.strip().strip('"') for part in path.split(",")]


def _pin_registry(schema: str) -> None:
    for signature in _PUBLIC_REGISTRY_SIGNATURES:
        if not _exists(signature):
            continue
        current = _search_path(signature)
        if current == PINNED_SEARCH_PATH:
            continue
        parts = _path_schemas(current)
        if len(parts) != 3 or parts[0] != "pg_catalog" or parts[2] != "pg_temp":
            raise RuntimeError(
                f"sw_039 refuses to pin {signature}: search_path {current!r} is neither "
                "the known prior shape nor already pinned"
            )
        op.execute(f"ALTER FUNCTION {signature} SET search_path = {PINNED_SEARCH_PATH}")

    signature = f"{_quote_ident(schema)}.ensure_registry_control()"
    if _exists(signature):
        current = _search_path(signature)
        if current != PINNED_SEARCH_PATH:
            if _path_schemas(current) != ["pg_catalog", schema]:
                raise RuntimeError(
                    f"sw_039 refuses to pin {signature}: search_path {current!r} is neither "
                    "the known prior value nor already pinned"
                )
            op.execute(f"ALTER FUNCTION {signature} SET search_path = {PINNED_SEARCH_PATH}")


def _restore_registry(schema: str) -> None:
    quoted = _quote_ident(schema)
    for signature in _PUBLIC_REGISTRY_SIGNATURES:
        if _exists(signature) and _search_path(signature) == PINNED_SEARCH_PATH:
            op.execute(
                f"ALTER FUNCTION {signature} SET search_path = pg_catalog, {quoted}, pg_temp"
            )
    signature = f"{quoted}.ensure_registry_control()"
    if _exists(signature) and _search_path(signature) == PINNED_SEARCH_PATH:
        op.execute(f"ALTER FUNCTION {signature} SET search_path = pg_catalog, {quoted}")


# ── Pinned partition bodies ────────────────────────────────────────────────
# ``c_schema`` is the only schema reference; ``__SCHEMA_LITERAL__`` is replaced
# with the migration-time schema as a SQL string literal.

_ENSURE_PARTITION_BODY = """
DECLARE
    c_schema CONSTANT text := __SCHEMA_LITERAL__;
    month_start    TIMESTAMPTZ;
    month_end      TIMESTAMPTZ;
    partition_name TEXT;
    next_start     TIMESTAMPTZ;
    next_end       TIMESTAMPTZ;
    next_name      TEXT;
BEGIN
__LOCK__
    month_start    := date_trunc('month', reference_ts);
    month_end      := month_start + INTERVAL '1 month';
    partition_name := format('__PARENT___p%s', to_char(month_start, 'YYYYMM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.__PARENT__ '
        'FOR VALUES FROM (%L) TO (%L)',
        c_schema, partition_name, c_schema, month_start, month_end
    );

    -- Next month partition (proactive)
    next_start := month_end;
    next_end   := next_start + INTERVAL '1 month';
    next_name  := format('__PARENT___p%s', to_char(next_start, 'YYYYMM'));

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.__PARENT__ '
        'FOR VALUES FROM (%L) TO (%L)',
        c_schema, next_name, c_schema, next_start, next_end
    );

    RETURN partition_name;
END;
"""

# Byte-identical key to sw_032: hashtext(current_schema()::text) under the old
# ``<schema>, pg_temp`` path evaluated to hashtext('<schema>').
_HEARTBEAT_LOCK = """
    -- One lock covers both the requested and proactively-created next
    -- partition, including overlapping calls for adjacent months.
    PERFORM pg_advisory_xact_lock(
        hashtext(c_schema),
        hashtext('switchboard_connector_heartbeat_log_ensure_partition')
    );
"""

_DROP_EXPIRED_BODY = """
DECLARE
    c_schema CONSTANT text := __SCHEMA_LITERAL__;
    partition_name TEXT;
    partition_month DATE;
    cutoff_month DATE;
    dropped_count INTEGER := 0;
BEGIN
    cutoff_month := date_trunc('month', reference_ts - retention)::date;

    FOR partition_name IN
        SELECT child.relname
        FROM pg_catalog.pg_inherits
        JOIN pg_catalog.pg_class parent ON parent.oid = pg_inherits.inhparent
        JOIN pg_catalog.pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_catalog.pg_class child ON child.oid = pg_inherits.inhrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = child.relnamespace
        WHERE parent.relname = '__PARENT__'
        AND parent_ns.nspname = c_schema
        AND ns.nspname = c_schema
        AND child.relname ~ '^__PARENT___p[0-9]{6}$'
    LOOP
        partition_month := to_date(substring(partition_name from '[0-9]{6}$'), 'YYYYMM');
        IF partition_month < cutoff_month THEN
            EXECUTE format('DROP TABLE IF EXISTS %I.%I', c_schema, partition_name);
            dropped_count := dropped_count + 1;
        END IF;
    END LOOP;

    RETURN dropped_count;
END;
"""


def _create_function(
    schema: str,
    name: str,
    arguments: str,
    returns: str,
    body: str,
    search_path: str,
) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_quote_ident(schema)}.{name}(
            {arguments}
        ) RETURNS {returns}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = {search_path}
        AS $fn${body}$fn$
        """
    )


_ENSURE_ARGUMENTS = "reference_ts TIMESTAMPTZ DEFAULT now()"


def _drop_arguments(default_retention: str) -> str:
    return (
        f"retention INTERVAL DEFAULT INTERVAL '{default_retention}',\n"
        "            reference_ts TIMESTAMPTZ DEFAULT now()"
    )


def _pinned_body(template: str, schema: str, parent: str, lock: str = "") -> str:
    return (
        template.replace("__LOCK__", lock)
        .replace("__SCHEMA_LITERAL__", _quote_literal(schema))
        .replace("__PARENT__", parent)
    )


def _install_pinned_partitions(schema: str) -> None:
    for parent, retention, lock in (
        ("message_inbox", "1 month", ""),
        ("connector_heartbeat_log", "7 days", _HEARTBEAT_LOCK),
    ):
        prefix = f"switchboard_{parent}"
        _create_function(
            schema,
            f"{prefix}_ensure_partition",
            _ENSURE_ARGUMENTS,
            "TEXT",
            _pinned_body(_ENSURE_PARTITION_BODY, schema, parent, lock),
            PINNED_SEARCH_PATH,
        )
        _create_function(
            schema,
            f"{prefix}_drop_expired_partitions",
            _drop_arguments(retention),
            "INTEGER",
            _pinned_body(_DROP_EXPIRED_BODY, schema, parent),
            PINNED_SEARCH_PATH,
        )


# ── Prior partition bodies (sw_001 / sw_002 / sw_032), restored on downgrade ─

_PRIOR_MESSAGE_INBOX_ENSURE = """
        DECLARE
            month_start   TIMESTAMPTZ;
            month_end     TIMESTAMPTZ;
            partition_name TEXT;
            next_start    TIMESTAMPTZ;
            next_end      TIMESTAMPTZ;
            next_name     TEXT;
        BEGIN
            -- Current month partition
            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('message_inbox_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            -- Next month partition (proactive)
            next_start := month_end;
            next_end   := next_start + INTERVAL '1 month';
            next_name  := format('message_inbox_p%s',
                                  to_char(next_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                'FOR VALUES FROM (%L) TO (%L)',
                next_name, next_start, next_end
            );

            RETURN partition_name;
        END;
        """

_PRIOR_HEARTBEAT_ENSURE = """
        DECLARE
            month_start    TIMESTAMPTZ;
            month_end      TIMESTAMPTZ;
            partition_name TEXT;
            next_start     TIMESTAMPTZ;
            next_end       TIMESTAMPTZ;
            next_name      TEXT;
        BEGIN
            -- One lock covers both the requested and proactively-created next
            -- partition, including overlapping calls for adjacent months.

            PERFORM pg_advisory_xact_lock(
                hashtext(current_schema()::text),
                hashtext('switchboard_connector_heartbeat_log_ensure_partition')
            );

            month_start    := date_trunc('month', reference_ts);
            month_end      := month_start + INTERVAL '1 month';
            partition_name := format('connector_heartbeat_log_p%s',
                                     to_char(month_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name, month_start, month_end
            );

            next_start := month_end;
            next_end   := next_start + INTERVAL '1 month';
            next_name  := format('connector_heartbeat_log_p%s',
                                  to_char(next_start, 'YYYYMM'));

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF connector_heartbeat_log '
                'FOR VALUES FROM (%L) TO (%L)',
                next_name, next_start, next_end
            );

            RETURN partition_name;
        END;
        """

_PRIOR_DROP_EXPIRED = """
        DECLARE
            partition_name TEXT;
            partition_month DATE;
            cutoff_month DATE;
            dropped_count INTEGER := 0;
        BEGIN
            cutoff_month := date_trunc('month', reference_ts - retention)::date;

            FOR partition_name IN
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_class child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                WHERE parent.relname = '__PARENT__'
                AND ns.nspname = current_schema()
                AND child.relname ~ '^__PARENT___p[0-9]{6}$'
            LOOP
                partition_month := to_date(substring(partition_name from '[0-9]{6}$'), 'YYYYMM');
                IF partition_month < cutoff_month THEN
                    EXECUTE format('DROP TABLE IF EXISTS %I', partition_name);
                    dropped_count := dropped_count + 1;
                END IF;
            END LOOP;

            RETURN dropped_count;
        END;
        """


def _install_prior_partitions(schema: str) -> None:
    prior_path = f"{_quote_ident(schema)}, pg_temp"
    for parent, retention, ensure_body in (
        ("message_inbox", "1 month", _PRIOR_MESSAGE_INBOX_ENSURE),
        ("connector_heartbeat_log", "7 days", _PRIOR_HEARTBEAT_ENSURE),
    ):
        prefix = f"switchboard_{parent}"
        _create_function(
            schema,
            f"{prefix}_ensure_partition",
            _ENSURE_ARGUMENTS,
            "TEXT",
            ensure_body,
            prior_path,
        )
        _create_function(
            schema,
            f"{prefix}_drop_expired_partitions",
            _drop_arguments(retention),
            "INTEGER",
            _PRIOR_DROP_EXPIRED.replace("__PARENT__", parent),
            prior_path,
        )


def _partitions_present(schema: str) -> bool:
    quoted = _quote_ident(schema)
    present = [_exists(f"{quoted}.{signature}") for signature in _PARTITION_SIGNATURES]
    if any(present) and not all(present):
        raise RuntimeError(f"sw_039 found a partial partition-function set in {schema}")
    return all(present)


def upgrade() -> None:
    schema = _schema()
    _pin_registry(schema)
    if _partitions_present(schema):
        _install_pinned_partitions(schema)


def downgrade() -> None:
    schema = _schema()
    if _partitions_present(schema):
        _install_prior_partitions(schema)
    _restore_registry(schema)
